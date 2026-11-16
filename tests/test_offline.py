"""
Offline tests: no network, about 5 seconds.

    python -m tests.test_offline        (or: python -m pytest tests)

They cover every checklist item, both routes through the chart gate, the
statement cache (including rate limiting and resuming), the track record,
and one full run from universe to HTML report with a fake Yahoo.
"""
from __future__ import annotations
import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config as cfg  # noqa: E402
from src import fundamentals as fund  # noqa: E402
from src import tracklog  # noqa: E402
from src.chart import chart_metrics  # noqa: E402
from src.checklist import chart_items, classify, fundamental_items  # noqa: E402
from src.universe import exclusion_reason  # noqa: E402
from tests.fakes import FakeYahoo, company, price_path, quarterly  # noqa: E402

GOOD_EXTRA = [{"label": "Low debt", "ok": True, "detail": "no financial debt"},
              {"label": "Little dilution", "ok": True, "detail": "flat"}]


def good_record(**changes) -> dict:
    rec = {"ticker": "GOOD", "revenue": [100, 125, 155, 195], "op_income": [12, 17, 24, 33],
           "net_income": [9, 13, 18, 25], "cash_flow": [8, 11, 15, 22], "roc": [14, 18, 22, 25],
           "recent_q_growth": 21.0, "extra": list(GOOD_EXTRA)}
    rec.update(changes)
    return rec


def results(rec) -> dict:
    items, _ = fundamental_items(rec, cfg)
    return {i["n"]: i["ok"] for i in items}


def naive(hist: pd.DataFrame) -> pd.DataFrame:
    out = hist.copy()
    out.index = out.index.tz_localize(None).normalize()
    return out


# ------------------------------------------------------------------ #
# Gates 1 and 2
# ------------------------------------------------------------------ #

def test_strong_business_passes_all_nine():
    r = results(good_record())
    assert all(r[n] is True for n in range(1, 10)), r
    c = classify(fundamental_items(good_record(), cfg)[0])
    assert c["gate1"] and c["gate2"] and c["tier"] == "B"      # no chart yet, so watchlist at best


def test_each_check_fails_for_the_right_reason():
    assert results(good_record(revenue=[100, 108, 117, 127]))[1] is False            # 8% a year
    assert results(good_record(revenue=[100, 150, 140, 200]))[2] is False            # one down year
    assert results(good_record(roc=[9, 10, 12, 11]))[3] is False                     # low return on capital
    assert results(good_record(cash_flow=[-5, -2, 15, 22]))[4] is False              # 2 of 4 years positive
    assert results(good_record(cash_flow=[8, 3, 4, 5]))[5] is False                  # 21% cash conversion
    assert results(good_record(op_income=[14, 17, 21, 25]))[8] is False              # margin 14% -> 12.8%
    assert results(good_record(op_income=[12, 17, 30, 33]))[8] is False              # 19.4% last year -> 16.9%
    assert results(good_record(revenue=[100, 140, 190, 200], op_income=[12, 20, 30, 36]))[9] is False   # +5% latest
    assert results(good_record(recent_q_growth=2.0))[9] is False                     # latest quarter stalled
    assert results(good_record(recent_q_growth=None))[9] is True                     # unknown quarter does not block


def test_missing_data_is_never_a_pass():
    r = results(good_record(revenue=[None, 125, 155, 195]))
    assert r[1] is None and r[2] is None and r[9] is None
    assert results(good_record(roc=[None, None, 22, 25]))[3] is None
    assert results(good_record(cash_flow=[None, None, 15, 22]))[4] is None
    assert results(good_record(extra=[]))[6] is None
    assert classify(fundamental_items(good_record(extra=[]), cfg)[0])["gate1"] is False


def test_one_missing_year_of_cash_flow_is_tolerated():
    # Latest year's cash flow statement not published yet: use the 3 years that exist
    r = results(good_record(cash_flow=[8, 11, 15, None]))
    assert r[4] is True and r[5] is True


# ------------------------------------------------------------------ #
# Gate 3
# ------------------------------------------------------------------ #

def chart_for(kind: str, seed: int = 3) -> dict:
    return chart_metrics(naive(price_path(kind, seed)), naive(price_path("index", 99)))


def test_established_uptrend_passes_chart_gate():
    items, info = chart_items(chart_for("established"), cfg)
    assert [i["ok"] for i in items] == [True, True, True], items
    assert info["stage"].startswith("Established")


def test_fresh_turnaround_passes_by_the_emerging_route():
    chart = chart_for("emerging")
    items, info = chart_items(chart, cfg)
    assert chart["weekly_signal"] == "BUY" and chart["weekly_bars"] <= cfg.FRESH_FLIP_WEEKS, chart
    assert [i["ok"] for i in items] == [True, True, True], items
    assert info["stage"].startswith("Emerging")
    # The same chart would fail the stricter established rules: 12-month performance is behind the index
    assert chart["rs_12m"] < 0


def test_downtrend_fails_chart_gate():
    items, info = chart_items(chart_for("downtrend"), cfg)
    assert items[0]["ok"] is False and items[1]["ok"] is False
    assert info["stage"] == "No uptrend"


def test_no_price_data_shows_as_no_data():
    items, _ = chart_items({"error": "no data returned"}, cfg)
    assert [i["ok"] for i in items] == [None, None, None]


# ------------------------------------------------------------------ #
# Statements, cache and rate limiting
# ------------------------------------------------------------------ #

def test_statements_are_read_into_four_aligned_years():
    world = FakeYahoo(companies={"ALFA": company([100e6, 125e6, 155e6, 195e6], [0.12, 0.14, 0.155, 0.17], debt=30e6)})
    with patch("yfinance.Ticker", world.Ticker), patch("time.sleep"):
        entry, status = fund.fetch_statements("ALFA", 0)
    assert status == "ok"
    assert entry["dates"] == ["2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]
    assert entry["revenue"] == [100e6, 125e6, 155e6, 195e6]
    rec = fund.build_record({"ticker": "ALFA"}, entry, cfg)
    # ROIC, latest year: 195e6 x 17% x (1 - 21%) / (97.5e6 equity + 30e6 debt)
    assert abs(rec["roc"][-1] - 195e6 * 0.17 * 0.79 / 127.5e6 * 100) < 1e-6
    assert rec["extra"][0]["ok"] is True and "0.9x" in rec["extra"][0]["detail"]
    assert rec["extra"][1]["ok"] is True


def test_debt_and_dilution_checks():
    heavy = company([100e6, 125e6, 155e6, 195e6], [0.12, 0.14, 0.155, 0.17], debt=200e6, share_growth=0.09)
    world = FakeYahoo(companies={"HEAVY": heavy})
    with patch("yfinance.Ticker", world.Ticker), patch("time.sleep"):
        entry, _ = fund.fetch_statements("HEAVY", 0)
    rec = fund.build_record({"ticker": "HEAVY"}, entry, cfg)
    assert rec["extra"][0]["ok"] is False      # debt is 6x operating profit
    assert rec["extra"][1]["ok"] is False      # shares growing 9% a year


def test_cache_resumes_after_rate_limiting():
    good = company([100e6, 125e6, 155e6, 195e6], [0.12, 0.14, 0.155, 0.17])
    tickers = [f"T{i:02d}" for i in range(12)]
    world = FakeYahoo(companies={t: good for t in tickers})
    world.rate_limit_statements = {"T03": 1}          # one explicit refusal, then fine
    world.fail_statements = {"T07": 99}               # a company Yahoo simply has no data for
    with tempfile.TemporaryDirectory() as tmp, patch.object(fund, "CACHE_PATH", Path(tmp) / "cache.json"), \
            patch("yfinance.Ticker", world.Ticker), patch("time.sleep") as sleep, contextlib.redirect_stdout(io.StringIO()):
        cache: dict = {}
        stats = fund.refresh_cache(tickers, cache, cfg)
        assert stats["fetched"] == 11 and stats["no_data"] == 1 and stats["cooldowns"] == 1 and not stats["stopped"]
        assert cache["T07"]["status"] == "no_data" and cache["T03"]["status"] == "ok"
        assert any(c.args and c.args[0] == cfg.COOLDOWN_SECONDS for c in sleep.call_args_list)
        # A second run straight after fetches nothing: everything is fresh
        before = len(world.calls)
        assert fund.refresh_cache(tickers, cache, cfg)["needed"] == 0 and len(world.calls) == before


def test_cache_stops_when_yahoo_keeps_refusing():
    good = company([100e6, 125e6, 155e6, 195e6], [0.12, 0.14, 0.155, 0.17])
    tickers = [f"T{i:02d}" for i in range(30)]
    world = FakeYahoo(companies={t: good for t in tickers})
    world.fail_statements = {t: 99 for t in tickers[5:]}      # silent empties from the 6th company on
    with tempfile.TemporaryDirectory() as tmp, patch.object(fund, "CACHE_PATH", Path(tmp) / "cache.json"), \
            patch("yfinance.Ticker", world.Ticker), patch("time.sleep"), contextlib.redirect_stdout(io.StringIO()):
        cache: dict = {}
        stats = fund.refresh_cache(tickers, cache, cfg)
        assert stats["fetched"] == 5 and stats["stopped"] == "Yahoo is rate limiting" and stats["left"] == 25
        # Nothing was wrongly recorded as "no data", so the next run retries all 25
        assert sorted(cache) == tickers[:5]
        assert fund.load_cache(Path(tmp) / "cache.json").keys() == cache.keys()


def test_latest_quarter_growth():
    world = FakeYahoo(quarters={"ALFA": quarterly(130e6, 100e6)})
    with patch("yfinance.Ticker", world.Ticker), patch("time.sleep"):
        q = fund.fetch_recent_quarter("ALFA", 0)
    assert abs(q["q_growth"] - 30.0) < 1e-9 and q["q_end"] == "2026-09-30"


def test_sector_exclusions():
    assert exclusion_reason("Financials", "Regional Banks", cfg)
    assert exclusion_reason("Materials", "Steel", cfg)
    assert exclusion_reason("Industrials", "Marine Transportation", cfg)
    assert exclusion_reason("Basic Materials", "Other Industrial Metals & Mining", cfg)
    assert exclusion_reason("Industrials", "Building Products", cfg) is None
    assert exclusion_reason("Materials", "Specialty Chemicals", cfg) is None
    assert exclusion_reason("", "", cfg) is None


# ------------------------------------------------------------------ #
# Track record
# ------------------------------------------------------------------ #

def test_track_record_measures_against_the_index():
    stock, index = naive(price_path("established", 5)), naive(price_path("index", 99))
    flag_day = stock.index[-64]
    log = [{"ticker": "ALFA", "name": "Alfa", "first_flagged": flag_day.strftime("%Y-%m-%d"),
            "price_at_flag": "1.0", "benchmark_at_flag": "1.0", "last_flagged": "2026-11-13", "times_flagged": "9"}]
    perf = tracklog.performance(log, {"ALFA": stock}, index)
    row = perf["rows"][0]
    expected = (stock["Close"].iloc[-1] / stock["Close"].loc[flag_day] - 1) * 100
    assert abs(row["return_pct"] - expected) < 1e-9          # uses fresh prices, not the stored 1.0
    assert perf["summary"]["measured"] == 1 and row["excess_pct"] is not None


# ------------------------------------------------------------------ #
# One full run, universe to report
# ------------------------------------------------------------------ #

def build_world() -> tuple[FakeYahoo, list[dict]]:
    strong = dict(revenue=[400e6, 500e6, 620e6, 780e6], op_margin=[0.12, 0.14, 0.155, 0.17])
    companies = {
        "ALFA": company(**strong),                                                    # A, established
        "BRAV": company(**strong),                                                    # A, emerging
        "CHAR": company(**strong),                                                    # B, chart not ready
        "DELT": company([400e6, 500e6, 620e6, 780e6], [0.17, 0.17, 0.165, 0.16]),     # C, margins slipping
        "ECHO": company([400e6, 420e6, 445e6, 470e6], [0.12, 0.12, 0.12, 0.12]),      # slow grower, dropped
        "FOXT": company(**strong, shares=2e9),                                        # passes, but far too large
    }
    prices = {
        "ALFA": price_path("established", 11), "BRAV": price_path("emerging", 12), "CHAR": price_path("downtrend", 13),
        "DELT": price_path("established", 14), "FOXT": price_path("established", 15), "SPY": price_path("index", 99),
    }
    world = FakeYahoo(companies, prices, quarters={"ALFA": quarterly(230e6, 185e6)},
                      info={"ALFA": {"shortName": "Alfa Controls", "trailingPE": 31.0}})
    universe = [{"ticker": t, "name": f"{t.title()} Corp", "sector": "Industrials", "industry": "Building Products"}
                for t in ["ALFA", "BRAV", "CHAR", "DELT", "ECHO", "FOXT", "HOTL"]]
    universe.append({"ticker": "GOLF", "name": "Golf Bank", "sector": "Financials", "industry": "Regional Banks"})
    return world, universe


def run_main(world, universe, argv=("main.py",)) -> str:
    import main
    out = io.StringIO()
    with patch("yfinance.Ticker", world.Ticker), patch("time.sleep"), patch.object(sys, "argv", list(argv)), \
            patch.object(main, "get_universe", lambda: (universe, "S&P MidCap 400 + SmallCap 600 (test)")), \
            contextlib.redirect_stdout(out):
        main.main()
    return out.getvalue()


def test_full_run_end_to_end():
    world, universe = build_world()
    here = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            run_main(world, universe)
            rows = pd.read_csv("output/all_companies.csv").set_index("ticker")
            assert rows.loc["ALFA", "tier"] == "A" and rows.loc["ALFA", "stage"].startswith("Established")
            assert rows.loc["BRAV", "tier"] == "A" and rows.loc["BRAV", "stage"].startswith("Emerging")
            assert rows.loc["CHAR", "tier"] == "B"
            assert rows.loc["DELT", "tier"] == "C"
            assert rows.loc["ECHO", "tier"] == "-"
            assert rows.loc["FOXT", "tier"] == "-" and "size band" in rows.loc["FOXT", "dropped"]
            assert "GOLF" not in rows.index and "HOTL" not in rows.index       # excluded sector, no data
            assert rows.loc["ALFA", "score"] == 12 and rows.loc["ALFA", "name"] == "Alfa Corp"
            assert 1e9 < rows.loc["ALFA", "market_cap"] < 15e9
            # Prices were fetched only for gate-1 passers, never for ECHO or the excluded bank
            fetched_prices = {s for s, kind in world.calls if kind == "history"}
            assert fetched_prices == {"SPY", "ALFA", "BRAV", "CHAR", "DELT", "FOXT"}, fetched_prices

            html = Path("output/index.html").read_text(encoding="utf-8")
            for needle in ("ALFA", "BRAV", "CHAR", "DELT", "Actionable now", "Watchlist", "Where the stocks dropped out"):
                assert needle in html, needle
            assert "ECHO" not in html and "FOXT" not in html

            log = tracklog.load("data/picks_log.csv")
            assert sorted(r["ticker"] for r in log) == ["ALFA", "BRAV"]

            # Second run: statements come from the cache, picks are not logged twice
            calls_before = len([c for c in world.calls if c[1].endswith(":yearly")])
            run_main(world, universe)
            assert len([c for c in world.calls if c[1].endswith(":yearly")]) == calls_before        # nothing refetched
            assert len(tracklog.load("data/picks_log.csv")) == 2
        finally:
            os.chdir(here)


def test_empty_result_still_produces_a_clear_report():
    world, universe = build_world()
    world.companies = {"ECHO": world.companies["ECHO"]}
    here = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            run_main(world, universe)
            html = Path("output/index.html").read_text(encoding="utf-8")
            assert "No stock passes all three gates" in html and "Data warnings" in html
        finally:
            os.chdir(here)


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print(f"ok   {name}")
    print(f"\nAll {len(tests)} tests passed.")

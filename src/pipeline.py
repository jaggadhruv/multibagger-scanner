"""
The part of the run that is identical for both markets.

A market-specific main.py builds one "record" per company (multi-year
series plus two pre-evaluated balance-sheet checks). Everything after that
happens here:

    business checklist (gates 1 and 2)
    price history for the gate-1 passers only, then the chart gate
    size band, tiers, track record, CSV files and the HTML report
"""
from __future__ import annotations
import csv
from datetime import datetime, timezone
from pathlib import Path

from src import tracklog
from src.chart import fetch_benchmark, fetch_charts, fetch_history
from src.checklist import chart_items, classify, fundamental_items, num, sort_key
from src.report import generate_report

KEEP = ("ticker", "name", "sector", "industry", "fy_end", "market_cap", "pe", "shares_latest", "net_income_latest")


def assess_business(rec: dict, cfg) -> dict:
    items, metrics = fundamental_items(rec, cfg)
    row = {k: rec.get(k) for k in KEEP}
    row.update({"items": items, "metrics": metrics, "chart": {}, "stage": "", "flags": [], "dropped": None})
    row.update(classify(items))
    return row


def add_chart(row: dict, chart: dict | None, cfg) -> None:
    c_items, info = chart_items(chart, cfg)
    row["items"] = [i for i in row["items"] if i["gate"] != 3] + c_items
    row["chart"] = chart or {}
    row["stage"] = info["stage"]
    row["flags"] = list(info["flags"])
    row.update(classify(row["items"]))


def apply_size_band(row: dict, cfg) -> None:
    cap = num(row.get("market_cap"))
    if cap is None:
        row["flags"].append("Market cap unknown, size band not checked")
    elif not (cfg.MARKET_CAP_MIN <= cap <= cfg.MARKET_CAP_MAX):
        row["dropped"] = "market cap outside the size band"


def build_funnel(counts: dict, rows: list[dict]) -> list[tuple]:
    funnel = [
        ("In the universe", counts.get("universe", 0)),
        ("After sector and industry exclusions", counts.get("in_scope", 0)),
        ("With usable multi-year statements", len(rows)),
    ]
    if rows:
        for n in range(1, 10):
            label = next((i["label"] for i in rows[0]["items"] if i["n"] == n), f"Check {n}")
            passed = sum(1 for r in rows for i in r["items"] if i["n"] == n and i["ok"] is True)
            funnel.append((f"Check {n} alone: {label}", passed))
    g1 = [r for r in rows if r["gate1"]]
    g12 = [r for r in g1 if r["gate2"]]
    in_band = [r for r in g12 if not r["dropped"]]
    funnel += [
        ("Pass all of gate 1 (proven business)", len(g1)),
        ("...and gate 2 (early / inflecting)", len(g12)),
        ("...and inside the size band", len(in_band)),
        ("...and gate 3 (chart strength) = tier A", sum(1 for r in in_band if r["tier"] == "A")),
    ]
    return funnel


def flat(row: dict) -> dict:
    """One CSV line per company."""
    m, c = row.get("metrics") or {}, row.get("chart") or {}
    out = {
        "ticker": row["ticker"], "name": row.get("name"), "sector": row.get("sector"), "industry": row.get("industry"),
        "tier": row["tier"], "score": row["score"], "gate1": row["gate1"], "gate2": row["gate2"], "gate3": row["gate3"],
        "dropped": row.get("dropped") or "", "stage": row.get("stage"), "market_cap": row.get("market_cap"),
        "pe": row.get("pe"), "fiscal_year_end": row.get("fy_end"),
        "rev_cagr_3y": m.get("rev_cagr_3y"), "rev_growth_latest": m.get("rev_growth_latest"),
        "return_on_capital_3y": m.get("roc_avg_3y"), "cash_conversion_3y": m.get("cash_conversion_3y"),
        "margin_latest": m.get("margin_latest"), "margin_3y_ago": m.get("margin_3y_ago"),
        "close": c.get("close"), "pct_above_200dma": c.get("pct_above_200"), "pct_from_52w_high": c.get("pct_from_high"),
        "rs_3m": c.get("rs_3m"), "rs_6m": c.get("rs_6m"), "rs_12m": c.get("rs_12m"),
        "weekly_supertrend": c.get("weekly_signal"), "weekly_bars": c.get("weekly_bars"),
        "daily_supertrend": c.get("daily_signal"), "rsi": c.get("rsi"),
    }
    for i in row["items"]:
        out[f"check_{i['n']:02d}"] = {True: "yes", False: "no", None: "no data"}[i["ok"]]
        out[f"check_{i['n']:02d}_detail"] = i["detail"]
    for k, v in list(out.items()):
        if isinstance(v, float):
            out[k] = round(v, 2)
    return out


def write_csv(path: Path, rows: list[dict]) -> None:
    lines = [flat(r) for r in rows]
    fields: list[str] = []
    for line in lines:
        for k in line:
            if k not in fields:
                fields.append(k)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(lines)


def coverage_warnings(rows: list[dict], counts: dict) -> list[str]:
    """Loud warnings when the data itself looks broken, so an empty list is never silent."""
    warnings = []
    in_scope = counts.get("in_scope", 0)
    if in_scope and len(rows) < 0.6 * in_scope:
        warnings.append(f"Statements were available for only {len(rows)} of {in_scope} in-scope companies. "
                        "The shortlist is incomplete until the rest are fetched.")
    if len(rows) >= 20:
        for n in range(1, 10):
            unknown = sum(1 for r in rows for i in r["items"] if i["n"] == n and i["ok"] is None)
            if unknown > 0.5 * len(rows):
                label = next(i["label"] for i in rows[0]["items"] if i["n"] == n)
                warnings.append(f"Check {n} ({label}) had no data for {unknown} of {len(rows)} companies. "
                                "That points at a data-source problem, not at the companies.")
    return warnings


def finish_run(cfg, records: list[dict], counts: dict, health: dict, enrich_fn=None,
               out_dir: str = "output", data_dir: str = "data") -> list[dict]:
    """
    records   one checklist record per in-scope company (status 'ok' or 'no_data')
    counts    {'universe': n, 'in_scope': n}
    health    {'warnings': [...], 'notes': [...]} collected so far
    enrich_fn optional hook(row) run on each gate-1 passer after prices are in;
              it may set market_cap / pe / name and may set row['dropped']
    """
    out, data = Path(out_dir), Path(data_dir)
    now = datetime.now(timezone.utc)
    run_date = now.strftime("%Y-%m-%d")

    print("\n" + "=" * 64 + "\nBusiness checklist (gates 1 and 2)\n" + "=" * 64)
    rows = [assess_business(r, cfg) for r in records if r.get("status") == "ok"]
    g1 = [r for r in rows if r["gate1"]]
    print(f"{len(rows)} companies with statements, {len(g1)} pass gate 1, "
          f"{sum(1 for r in g1 if r['gate2'])} of those pass gate 2")

    log_path = data / "picks_log.csv"
    log = tracklog.load(log_path)

    print("\n" + "=" * 64 + "\nPrices and chart gate (gate-1 passers only)\n" + "=" * 64)
    bench, bench_symbol = (None, None)
    if g1 or log:
        bench, bench_symbol = fetch_benchmark(cfg.BENCHMARK_SYMBOLS, cfg.FETCH_DELAY)
        if bench is None:
            health["warnings"].append(f"No price data for the {cfg.BENCHMARK_NAME}; relative strength used plain "
                                      "returns instead, which is a weaker test.")
        else:
            print(f"Benchmark: {bench_symbol}, last bar {bench.index[-1].date()}")
    charts, histories = fetch_charts([r["ticker"] for r in g1], bench, cfg.PRICE_SUFFIX, cfg.FETCH_DELAY)
    failed = [t for t, c in charts.items() if c.get("error")]
    if failed:
        health["warnings"].append(f"No price history for {len(failed)} gate-1 stock(s): {', '.join(sorted(failed)[:12])}. "
                                  "Their chart checks show as no data.")
    for r in g1:
        add_chart(r, charts.get(r["ticker"]), cfg)
        if enrich_fn is not None:
            try:
                enrich_fn(r)
            except Exception as e:  # noqa: BLE001
                print(f"  enrich failed for {r['ticker']}: {e}")
        apply_size_band(r, cfg)
        if r["dropped"]:
            r["tier"] = "-"

    shown = sorted([r for r in rows if r["tier"] in ("A", "B", "C")], key=sort_key)
    tier_a = [r for r in shown if r["tier"] == "A"]

    new = tracklog.record_picks(log, tier_a, run_date, None if bench is None else bench["Close"].iloc[-1])
    tracklog.save(log_path, log)
    track = tracklog.performance(log, histories, bench,
                                 fetch_fn=lambda t: fetch_history(t + cfg.PRICE_SUFFIX, cfg.FETCH_DELAY)[0])
    print(f"Track record: {len(log)} pick(s) logged, {new} new this run")

    health["warnings"] = coverage_warnings(rows, counts) + health.get("warnings", [])
    write_csv(out / "all_companies.csv", sorted(rows, key=lambda r: ({"A": 0, "B": 1, "C": 2}.get(r["tier"], 3), sort_key(r))))
    write_csv(out / "shortlist.csv", shown)
    generate_report({
        "cfg": cfg, "generated": now.strftime("%Y-%m-%d %H:%M UTC"), "rows": shown,
        "screened": counts.get("in_scope", 0), "funnel": build_funnel(counts, rows),
        "health": health, "track": track,
    }, out / "index.html")

    print("\n" + "=" * 64 + "\nResult\n" + "=" * 64)
    for tier, title in (("A", "Actionable"), ("B", "Watchlist"), ("C", "Bench")):
        names = [r["ticker"] for r in shown if r["tier"] == tier]
        print(f"{title:11s} {len(names):3d}  {', '.join(names[:25])}")
    for w in health["warnings"]:
        print(f"WARNING: {w}")
    return rows

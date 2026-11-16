"""
Multi-year fundamentals for US stocks, from Yahoo Finance annual statements.

Why a cache
    Annual statements change once a year, and Yahoo rate limits anything that
    asks for too much at once. So every company's numbers are stored in
    data/fundamentals_cache.json and only refetched every few weeks, spread
    out so each weekly run fetches roughly a quarter of the universe.

    The first run has to fetch everything (about 3 calls per company, one at
    a time). If Yahoo starts refusing, or the time budget runs out, the run
    stops fetching, keeps what it has, and the next run carries on from
    there. Nothing fetched is ever lost.

What is stored per company (4 fiscal years, oldest first)
    revenue, operating income, net income, tax rate, diluted share count,
    operating cash flow, capital expenditure, free cash flow,
    shareholders' equity, total debt, lease obligations, shares outstanding
"""
from __future__ import annotations
import json
import time
import zlib
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

from src.chart import is_rate_limit
from src.checklist import cagr_pct, num

CACHE_PATH = Path("data/fundamentals_cache.json")
DEFAULT_TAX_RATE = 0.21


# ------------------------------------------------------------------ #
# Cache
# ------------------------------------------------------------------ #

def load_cache(path: Path | None = None) -> dict:
    try:
        return json.loads(Path(path or CACHE_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_cache(cache: dict, path: Path | None = None) -> None:
    path = Path(path or CACHE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, separators=(",", ":"), sort_keys=True), encoding="utf-8")


def _age_days(entry: dict, key: str = "fetched") -> int:
    try:
        return (date.today() - datetime.strptime(entry[key], "%Y-%m-%d").date()).days
    except (KeyError, ValueError, TypeError):
        return 10_000


def is_stale(ticker: str, entry: dict | None, cfg, shortlist: bool = False) -> bool:
    if not entry:
        return True
    age = _age_days(entry)
    if entry.get("status") != "ok":
        return age >= 7                      # retry companies with no data once a week
    if shortlist:
        return age >= cfg.CACHE_DAYS_SHORTLIST
    # A fixed per-ticker offset spreads the refreshes across the weeks
    offset = zlib.crc32(ticker.encode()) % max(1, cfg.CACHE_DAYS_SPREAD)
    return age >= cfg.CACHE_DAYS_MIN + offset


# ------------------------------------------------------------------ #
# Reading Yahoo's statement tables
# ------------------------------------------------------------------ #

def _norm(df: pd.DataFrame | None) -> pd.DataFrame:
    """Row names to lower case without spaces, so 'Total Revenue' and 'TotalRevenue' match."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()
    out = df.copy()
    out.index = [str(i).replace(" ", "").lower() for i in out.index]
    out = out[~out.index.duplicated(keep="first")]
    out.columns = pd.to_datetime(out.columns, errors="coerce")
    return out.loc[:, out.columns.notna()]


def _col(df: pd.DataFrame, when: pd.Timestamp):
    """The column for a fiscal year end, allowing a few days of mismatch between statements."""
    if df.empty:
        return None
    if when in df.columns:
        return when
    gaps = [(abs((c - when).days), c) for c in df.columns]
    gap, col = min(gaps, key=lambda g: g[0])
    return col if gap <= 15 else None


def _val(df: pd.DataFrame, keys, when: pd.Timestamp):
    col = _col(df, when)
    if col is None:
        return None
    for key in ([keys] if isinstance(keys, str) else keys):
        if key in df.index:
            v = num(df.at[key, col])
            if v is not None:
                return v
    return None


def _statement(ticker_obj, names: tuple, freq: str) -> pd.DataFrame:
    for name in names:
        fn = getattr(ticker_obj, name, None)
        if fn is not None:
            return _norm(fn(freq=freq))
    return pd.DataFrame()


def fetch_statements(ticker: str, delay: float) -> tuple[dict | None, str]:
    """
    Returns (cache entry, status). status is one of:
        ok            entry holds 3-4 fiscal years
        empty         Yahoo returned nothing usable (no data, or silently rate limited)
        rate_limited  Yahoo said so explicitly
    """
    try:
        t = yf.Ticker(ticker)
        inc = _statement(t, ("get_income_stmt", "get_financials"), "yearly")
        time.sleep(delay)
        if inc.empty or not any(k in inc.index for k in ("totalrevenue", "operatingrevenue")):
            return None, "empty"
        bal = _statement(t, ("get_balance_sheet", "get_balancesheet"), "yearly")
        time.sleep(delay)
        cfl = _statement(t, ("get_cash_flow", "get_cashflow"), "yearly")
        time.sleep(delay)
    except Exception as e:  # noqa: BLE001
        return None, ("rate_limited" if is_rate_limit(e) else "empty")
    if bal.empty or cfl.empty:
        return None, "empty"

    rev_key = "totalrevenue" if "totalrevenue" in inc.index else "operatingrevenue"
    dates = sorted(c for c in inc.columns if num(inc.at[rev_key, c]) is not None)[-4:]
    if len(dates) < 3:
        return None, "empty"

    def series(df, keys):
        return [_val(df, keys, d) for d in dates]

    entry = {
        "status": "ok",
        "fetched": date.today().isoformat(),
        "dates": [d.strftime("%Y-%m-%d") for d in dates],
        "revenue": series(inc, rev_key),
        "op_income": series(inc, ("operatingincome", "totaloperatingincomeasreported", "ebit")),
        "net_income": series(inc, ("netincome", "netincomecommonstockholders")),
        "tax_rate": series(inc, "taxrateforcalcs"),
        "shares": series(inc, ("dilutedaverageshares", "basicaverageshares")),
        "ocf": series(cfl, ("operatingcashflow", "cashflowfromcontinuingoperatingactivities")),
        "capex": series(cfl, "capitalexpenditure"),
        "fcf": series(cfl, "freecashflow"),
        "equity": series(bal, ("stockholdersequity", "commonstockequity")),
        "total_debt": series(bal, "totaldebt"),
        "lt_debt": series(bal, "longtermdebt"),
        "cur_debt": series(bal, "currentdebt"),
        "lease": series(bal, "capitalleaseobligations"),
        "total_assets": series(bal, "totalassets"),
        "bs_shares": series(bal, ("ordinarysharesnumber", "shareissued")),
    }
    return entry, "ok"


def fetch_recent_quarter(ticker: str, delay: float) -> dict:
    """Latest quarter's revenue against the same quarter a year earlier, in %."""
    out = {"q_fetched": date.today().isoformat(), "q_growth": None, "q_end": None}
    try:
        q = _statement(yf.Ticker(ticker), ("get_income_stmt", "get_financials"), "quarterly")
        time.sleep(delay)
    except Exception:  # noqa: BLE001
        return out
    key = "totalrevenue" if "totalrevenue" in q.index else ("operatingrevenue" if "operatingrevenue" in q.index else None)
    if key is None:
        return out
    cols = sorted(c for c in q.columns if num(q.at[key, c]) is not None)
    if len(cols) < 2:
        return out
    latest = cols[-1]
    for c in cols[:-1]:
        if 350 <= (latest - c).days <= 380:
            a, b = num(q.at[key, c]), num(q.at[key, latest])
            if a and a > 0 and b is not None:
                out["q_growth"] = (b / a - 1.0) * 100.0
                out["q_end"] = latest.strftime("%Y-%m-%d")
            break
    return out


# ------------------------------------------------------------------ #
# Refreshing the cache, politely and resumably
# ------------------------------------------------------------------ #

def refresh_cache(tickers: list[str], cache: dict, cfg, shortlist: set | None = None,
                  max_minutes: float | None = None, force: bool = False) -> dict:
    """
    Fetch statements for every ticker whose cache entry is missing or stale.
    Stops early on the time budget or on persistent rate limiting. Returns stats.
    """
    shortlist = shortlist or set()
    budget = (cfg.FUNDAMENTALS_MAX_MINUTES if max_minutes is None else max_minutes) * 60
    todo = [t for t in tickers if force or is_stale(t, cache.get(t), cfg, t in shortlist)]
    # Shortlisted names first, then companies never fetched, then routine refreshes
    todo.sort(key=lambda t: (t not in shortlist, cache.get(t) is not None, t))
    stats = {"needed": len(todo), "fetched": 0, "no_data": 0, "stopped": None, "cooldowns": 0}
    if not todo:
        print("Fundamentals cache is fresh, nothing to fetch.")
        return stats

    est = len(todo) * (3 * cfg.FETCH_DELAY + 1.5) / 60
    print(f"Fetching annual statements for {len(todo)} of {len(tickers)} companies "
          f"(one at a time, roughly {est:.0f} min, budget {budget / 60:.0f} min)")
    today = date.today().isoformat()
    started = time.time()
    streak: list[str] = []      # consecutive failures, not yet written to the cache
    i = 0
    while i < len(todo):
        if time.time() - started > budget:
            stats["stopped"] = "time budget reached"
            break
        t = todo[i]
        entry, status = fetch_statements(t, cfg.FETCH_DELAY)
        if status == "ok":
            previous = cache.get(t) or {}
            for k in ("q_fetched", "q_growth", "q_end"):      # keep the quarterly figure
                if k in previous:
                    entry[k] = previous[k]
            cache[t] = entry
            stats["fetched"] += 1
            for s in streak:                                   # isolated failures were real gaps
                cache[s] = {"status": "no_data", "fetched": today}
                stats["no_data"] += 1
            streak = []
        else:
            streak.append(t)
            if status == "rate_limited" or len(streak) >= cfg.EMPTY_STREAK_LIMIT:
                if stats["cooldowns"] >= cfg.MAX_COOLDOWNS:
                    stats["stopped"] = "Yahoo is rate limiting"
                    streak = []
                    break
                stats["cooldowns"] += 1
                wait = cfg.COOLDOWN_SECONDS * stats["cooldowns"]
                print(f"  {len(streak)} failure(s) in a row at {t}: likely rate limiting, pausing {wait}s")
                time.sleep(wait)
                i -= len(streak) - 1                           # retry the whole streak
                streak = []
                continue
        i += 1
        done = stats["fetched"] + stats["no_data"] + len(streak)
        if done and done % 50 == 0:
            save_cache(cache)
            print(f"  {done}/{len(todo)} done, {stats['fetched']} with data, {(time.time() - started) / 60:.1f} min")
    if stats["stopped"] is None:
        for s in streak:
            cache[s] = {"status": "no_data", "fetched": today}
            stats["no_data"] += 1
    save_cache(cache)
    left = stats["needed"] - stats["fetched"] - stats["no_data"]
    print(f"Statements: {stats['fetched']} fetched, {stats['no_data']} with no data, {left} left for the next run"
          + (f" ({stats['stopped']})" if stats["stopped"] else ""))
    stats["left"] = left
    return stats


def refresh_quarters(tickers: list[str], cache: dict, cfg) -> None:
    """Latest-quarter growth for the few stocks that pass gate 1."""
    todo = [t for t in tickers if _age_days(cache.get(t) or {}, "q_fetched") >= 7]
    if todo:
        print(f"Fetching latest quarter for {len(todo)} gate-1 stocks...")
    for t in todo:
        if cache.get(t, {}).get("status") == "ok":
            cache[t].update(fetch_recent_quarter(t, cfg.FETCH_DELAY))
    if todo:
        save_cache(cache)


# ------------------------------------------------------------------ #
# Cache entry -> record for the checklist
# ------------------------------------------------------------------ #

def _financial_debt(entry: dict, i: int):
    """Total debt without lease obligations. None when the balance sheet has nothing for that year."""
    total, lease = num(entry["total_debt"][i]), num(entry["lease"][i]) or 0.0
    if total is None:
        parts = [num(entry["lt_debt"][i]), num(entry["cur_debt"][i])]
        if any(p is not None for p in parts):
            return sum(p or 0.0 for p in parts)
        # Yahoo leaves debt blank for debt-free companies; trust that only if the balance sheet exists
        return 0.0 if num(entry["total_assets"][i]) is not None else None
    return max(total - lease, 0.0)


def _roic(entry: dict, i: int):
    op, equity, debt = num(entry["op_income"][i]), num(entry["equity"][i]), _financial_debt(entry, i)
    if op is None or equity is None or debt is None:
        return None
    capital = equity + debt
    if capital <= 0:
        return None
    tax = num(entry["tax_rate"][i])
    if tax is None or not (0.0 <= tax <= 0.5):
        tax = DEFAULT_TAX_RATE
    return op * (1.0 - tax) / capital * 100.0


def build_record(meta: dict, entry: dict | None, cfg) -> dict:
    """Turn one cache entry into the record the checklist understands."""
    rec = {**meta, "status": "no_data", "extra": []}
    if not entry or entry.get("status") != "ok":
        return rec
    n = len(entry["dates"])
    fcf = []
    for i in range(n):
        v = num(entry["fcf"][i])
        if v is None and num(entry["ocf"][i]) is not None:
            v = num(entry["ocf"][i]) + (num(entry["capex"][i]) or 0.0)   # capex is negative in Yahoo data
        fcf.append(v)

    rec.update({
        "status": "ok",
        "fy_end": entry["dates"][-1],
        "revenue": entry["revenue"],
        "op_income": entry["op_income"],
        "net_income": entry["net_income"],
        "cash_flow": fcf,
        "roc": [_roic(entry, i) for i in range(n)],
        "recent_q_growth": entry.get("q_growth"),
        "shares_latest": num(entry["bs_shares"][-1]) or num(entry["shares"][-1]),
        "net_income_latest": num(entry["net_income"][-1]),
    })

    # Item 6: debt against operating profit
    debt, op = _financial_debt(entry, n - 1), num(entry["op_income"][-1])
    label = "Low debt"
    if debt is None:
        rec["extra"].append({"label": label, "ok": None, "detail": "no balance sheet data"})
    elif debt <= 0:
        rec["extra"].append({"label": label, "ok": True, "detail": "no financial debt"})
    elif op is None or op <= 0:
        rec["extra"].append({"label": label, "ok": False, "detail": "has debt but no operating profit"})
    else:
        ratio = debt / op
        rec["extra"].append({"label": label, "ok": ratio <= cfg.DEBT_TO_OPERATING_PROFIT_MAX,
                             "detail": f"debt is {ratio:.1f}x operating profit"})

    # Item 7: dilution
    shares = [num(s) for s in entry["shares"]]
    label = "Little dilution"
    if n >= 4 and shares[0] and shares[-1]:
        change = cagr_pct(shares[0], shares[-1], n - 1)
        word = f"growing {change:.1f}% a year" if change >= 0.05 else (
            f"shrinking {abs(change):.1f}% a year" if change <= -0.05 else "flat")
        rec["extra"].append({"label": label, "ok": change <= cfg.DILUTION_MAX_PCT, "detail": f"share count {word}"})
    else:
        rec["extra"].append({"label": label, "ok": None, "detail": "needs 4 years of share counts"})
    return rec


def enrich_from_info(ticker: str, delay: float) -> dict:
    """Optional extras for the final few names. Any failure just returns {}."""
    try:
        info = yf.Ticker(ticker).info or {}
        time.sleep(delay)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for ours, theirs in (("name", "shortName"), ("sector", "sector"), ("industry", "industry"),
                         ("market_cap", "marketCap"), ("pe", "trailingPE")):
        v = info.get(theirs)
        if v not in (None, "", "Infinity"):
            out[ours] = v
    return out

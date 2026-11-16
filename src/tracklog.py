"""
Track record of the screener's own picks.

Each time a stock first reaches tier A it is written to data/picks_log.csv
with the date and the price that day. Every later run measures how it has
done since, against the index over the same period. After a few months this
is the only real evidence of whether the checklist is worth trusting.

The log holds the screener's picks only. It knows nothing about any trades
or positions of yours.
"""
from __future__ import annotations
import csv
from pathlib import Path

import pandas as pd

FIELDS = ["ticker", "name", "first_flagged", "price_at_flag", "benchmark_at_flag", "last_flagged", "times_flagged"]


def load(path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as f:
        return [dict(r) for r in csv.DictReader(f) if r.get("ticker")]


def save(path, log: list[dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for row in log:
            w.writerow({k: row.get(k, "") for k in FIELDS})


def record_picks(log: list[dict], tier_a: list[dict], run_date: str, bench_close) -> int:
    """Add new tier A names, refresh last_flagged on repeats. Returns how many are new."""
    by_ticker = {r["ticker"]: r for r in log}
    new = 0
    for row in tier_a:
        t = row["ticker"]
        price = (row.get("chart") or {}).get("close")
        if price is None:
            continue
        if t in by_ticker:
            entry = by_ticker[t]
            if entry.get("last_flagged") != run_date:
                entry["last_flagged"] = run_date
                entry["times_flagged"] = int(float(entry.get("times_flagged") or 1)) + 1
        else:
            entry = {
                "ticker": t, "name": row.get("name", ""),
                "first_flagged": run_date, "price_at_flag": round(float(price), 4),
                "benchmark_at_flag": "" if bench_close is None else round(float(bench_close), 4),
                "last_flagged": run_date, "times_flagged": 1,
            }
            log.append(entry)
            by_ticker[t] = entry
            new += 1
    return new


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _price_on(hist: pd.DataFrame | None, date: pd.Timestamp, stored):
    """
    Price on the flag date taken from freshly downloaded history, so a later
    stock split cannot distort the return. Falls back to the stored price if
    the flag date is older than the downloaded window.
    """
    if hist is not None and len(hist) and hist.index[0] <= date:
        v = hist["Close"].asof(date)
        if v is not None and not pd.isna(v) and v > 0:
            return float(v)
    return _f(stored)


def performance(log: list[dict], histories: dict, bench: pd.DataFrame | None, fetch_fn=None) -> dict:
    """
    histories: {ticker: price history} already downloaded this run.
    fetch_fn(ticker) -> history, used for logged tickers not fetched this run.
    """
    rows = []
    for entry in log:
        t = entry["ticker"]
        hist = histories.get(t)
        if hist is None and fetch_fn is not None:
            hist = fetch_fn(t)
        if hist is None or not len(hist):
            rows.append({**entry, "price_now": None, "return_pct": None, "index_return_pct": None, "excess_pct": None})
            continue
        flag = pd.Timestamp(entry["first_flagged"])
        then = _price_on(hist, flag, entry.get("price_at_flag"))
        now = float(hist["Close"].iloc[-1])
        ret = (now / then - 1.0) * 100.0 if then else None
        b_then = _price_on(bench, flag, entry.get("benchmark_at_flag"))
        b_now = float(bench["Close"].iloc[-1]) if bench is not None and len(bench) else None
        b_ret = (b_now / b_then - 1.0) * 100.0 if (b_then and b_now) else None
        rows.append({
            **entry, "price_then": then, "price_now": now, "return_pct": ret, "index_return_pct": b_ret,
            "excess_pct": (ret - b_ret) if (ret is not None and b_ret is not None) else None,
            "days_held": int((hist.index[-1] - flag).days),
        })
    rows.sort(key=lambda r: r["first_flagged"], reverse=True)
    measured = [r for r in rows if r.get("excess_pct") is not None and r.get("days_held", 0) >= 28]
    summary = {"picks": len(rows), "measured": len(measured), "avg_return": None, "avg_excess": None, "beat_index": None}
    if measured:
        summary["avg_return"] = sum(r["return_pct"] for r in measured) / len(measured)
        summary["avg_excess"] = sum(r["excess_pct"] for r in measured) / len(measured)
        summary["beat_index"] = sum(1 for r in measured if r["excess_pct"] > 0)
    return {"rows": rows, "summary": summary}

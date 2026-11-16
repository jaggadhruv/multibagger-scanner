"""
US Multibagger Shortlist: main entry point.

    python main.py                  full run
    python main.py --sample         12 tickers, quick check that everything works
    python main.py --limit 100      first 100 in-scope tickers
    python main.py --skip-fetch     no statement fetching, use the cache as it is
    python main.py --refresh-all    refetch every company's statements

Steps
    1. Universe (S&P 400 + 600, or input/universe.csv) and sector exclusions
    2. Annual statements from Yahoo, through a cache in data/ (see src/fundamentals.py)
    3. Business checklist: gate 1 (proven) and gate 2 (inflecting)
    4. Prices for the gate-1 passers only, then gate 3 (chart)
    5. Tiers, track record, output/index.html and CSV files
"""
from __future__ import annotations
import argparse
import sys

import config as cfg
from src import fundamentals as fund
from src.checklist import classify, fundamental_items, num
from src.pipeline import finish_run
from src.universe import SAMPLE, exclusion_reason, get_universe


def passes_gate1(rec: dict) -> bool:
    if rec.get("status") != "ok":
        return False
    items, _ = fundamental_items(rec, cfg)
    return classify(items)["gate1"]


def main() -> None:
    p = argparse.ArgumentParser(description="US multibagger shortlist")
    p.add_argument("--sample", action="store_true", help="12 tickers, for a quick check")
    p.add_argument("--limit", type=int, default=None, help="only the first N in-scope tickers")
    p.add_argument("--skip-fetch", action="store_true", help="do not fetch statements, use the cache as is")
    p.add_argument("--refresh-all", action="store_true", help="refetch statements for every company")
    p.add_argument("--max-fetch-minutes", type=float, default=None, help="time budget for statement fetching")
    args = p.parse_args()
    health = {"warnings": [], "notes": []}

    print("=" * 64 + "\nUniverse\n" + "=" * 64)
    if args.sample:
        universe, described = list(SAMPLE), "sample of 12 tickers"
    else:
        universe, described = get_universe()
    if not universe:
        print("ERROR: no tickers. Wikipedia could not be read; try again or add input/universe.csv")
        sys.exit(1)
    cfg.UNIVERSE_NAME = described
    if not args.sample and described.startswith("S&P") and len(universe) < 800:
        health["warnings"].append(f"Only {len(universe)} tickers were read from Wikipedia (about 1,000 expected). "
                                  "One of the two index pages probably failed to load.")

    in_scope, excluded = [], 0
    for meta in universe:
        if exclusion_reason(meta.get("sector"), meta.get("industry"), cfg):
            excluded += 1
        else:
            in_scope.append(meta)
    if args.limit:
        in_scope = in_scope[: args.limit]
    print(f"{len(universe)} tickers, {excluded} outside scope (financials, real estate, energy, commodities), "
          f"{len(in_scope)} to screen")
    tickers = [m["ticker"] for m in in_scope]
    meta_by = {m["ticker"]: m for m in in_scope}

    print("\n" + "=" * 64 + "\nAnnual statements\n" + "=" * 64)
    cache = fund.load_cache()
    # Stocks that already pass gate 1 on cached numbers get refreshed first and more often
    shortlist = {t for t in tickers if passes_gate1(fund.build_record(meta_by[t], cache.get(t), cfg))}
    if args.skip_fetch:
        print("Skipping the fetch, using cached statements only.")
    else:
        stats = fund.refresh_cache(tickers, cache, cfg, shortlist, args.max_fetch_minutes, args.refresh_all)
        if stats.get("left"):
            health["notes"].append(f"Statement fetching stopped early ({stats['stopped']}); {stats['left']} companies "
                                   "are still to be fetched and will be picked up by the next run.")

    records = {t: fund.build_record(meta_by[t], cache.get(t), cfg) for t in tickers}
    gate1_now = [t for t in tickers if passes_gate1(records[t])]
    if not args.skip_fetch:
        fund.refresh_quarters(gate1_now, cache, cfg)
        for t in gate1_now:
            records[t] = fund.build_record(meta_by[t], cache.get(t), cfg)

    def enrich(row: dict) -> None:
        """Name, sector, market cap and P/E for the few names that reach this point."""
        info = {} if args.skip_fetch else fund.enrich_from_info(row["ticker"], cfg.FETCH_DELAY)
        for key in ("name", "sector", "industry"):
            if not row.get(key) and info.get(key):
                row[key] = info[key]
        close, shares = num(row["chart"].get("close")), num(row.get("shares_latest"))
        row["market_cap"] = num(info.get("market_cap")) or (close * shares if close and shares else None)
        earnings = num(row.get("net_income_latest"))
        row["pe"] = num(info.get("pe")) or (row["market_cap"] / earnings if row["market_cap"] and earnings and earnings > 0 else None)
        reason = exclusion_reason(row.get("sector"), row.get("industry"), cfg)
        if reason:
            row["dropped"] = f"out of scope ({reason})"

    finish_run(cfg, list(records.values()), {"universe": len(universe), "in_scope": len(in_scope)}, health, enrich)
    print("\nDone. Open output/index.html")


if __name__ == "__main__":
    main()

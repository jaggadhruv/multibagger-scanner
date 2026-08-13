"""
US Multibagger Screener — main entry point.

Usage:
    python main.py                 # Full run: ~1500 tickers, 15-30 min
    python main.py --sample        # Quick smoke test (~30 tickers)
    python main.py --limit 200     # First 200 tickers of the universe
    python main.py --permissive    # Include rows with missing data
    python main.py --top 100       # Show top 100 in report (default 50)
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

import pandas as pd

from src.universe import get_universe, get_sample_tickers
from src.fetch import fetch_fundamentals
from src.screen import apply_filters
from src.score import compute_scores
from src.technicals import fetch_technicals
from src.report import generate_html_report


def main():
    p = argparse.ArgumentParser(description="US multibagger stock screener")
    p.add_argument("--sample", action="store_true",
                   help="Use a hardcoded ~30-ticker sample (for quick tests)")
    p.add_argument("--limit", type=int, default=None,
                   help="Limit universe to first N tickers (for iteration)")
    p.add_argument("--include-large", action="store_true",
                   help="Add S&P 500 to the universe (default: mid+small only, "
                        "since large caps rarely become multibaggers)")
    p.add_argument("--permissive", action="store_true",
                   help="Include rows with missing data in filtering (non-strict)")
    p.add_argument("--top", type=int, default=50,
                   help="Show top N in HTML report (default 50)")
    p.add_argument("--output-dir", default="output",
                   help="Directory for CSV + HTML outputs")
    p.add_argument("--workers", type=int, default=8,
                   help="Concurrent yfinance fetches (default 8, don't go crazy)")
    args = p.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)

    # 1. UNIVERSE
    print("=" * 60)
    print("STEP 1: Building ticker universe")
    print("=" * 60)
    if args.sample:
        tickers = get_sample_tickers()
        print(f"Using sample universe: {len(tickers)} tickers")
    else:
        tickers = get_universe(include_large_cap=args.include_large)
        label = "S&P 1500 (large+mid+small)" if args.include_large else "S&P 400+600 (mid+small)"
        print(f"{label} universe: {len(tickers)} tickers")
    if args.limit:
        tickers = tickers[: args.limit]
        print(f"Limited to first {len(tickers)}")

    if not tickers:
        print("ERROR: No tickers to process. Check network / Wikipedia access.")
        sys.exit(1)

    # 2. FETCH
    print("\n" + "=" * 60)
    print("STEP 2: Fetching fundamentals")
    print("=" * 60)
    raw = fetch_fundamentals(tickers, max_workers=args.workers)
    raw_path = output_dir / "raw_data.csv"
    raw.to_csv(raw_path, index=False)
    print(f"Raw data saved: {raw_path}")

    # Only proceed with rows we actually got data for
    fetched = raw[raw["error"].isna()].copy()
    print(f"Rows with data: {len(fetched)}/{len(raw)}")

    # 3. SCREEN
    print("\n" + "=" * 60)
    print("STEP 3: Applying filters")
    print("=" * 60)
    filtered = apply_filters(fetched, strict=not args.permissive)
    if len(filtered) == 0:
        print("\nNo candidates passed filters.")
        print("Try `--permissive` or loosen thresholds in src/screen.py.")
        sys.exit(0)
    filtered_path = output_dir / "filtered.csv"
    filtered.to_csv(filtered_path, index=False)

    # 4. SCORE (fundamental)
    print("\n" + "=" * 60)
    print("STEP 4: Fundamental scoring")
    print("=" * 60)
    scored = compute_scores(filtered)
    scored_path = output_dir / "scored.csv"
    scored.to_csv(scored_path, index=False)
    print(f"Scored data saved: {scored_path}")

    # 5. TECHNICALS (only for filtered candidates - not the whole universe)
    print("\n" + "=" * 60)
    print("STEP 5: Technical analysis (sequential yfinance, ~0.3s/ticker)")
    print("=" * 60)
    technicals = fetch_technicals(scored["ticker"].tolist(), max_workers=args.workers)
    tech_path = output_dir / "technicals.csv"
    technicals.to_csv(tech_path, index=False)

    # Merge technicals into scored dataframe on ticker
    scored = scored.merge(technicals, on="ticker", how="left", suffixes=("", "_tech"))
    scored.to_csv(output_dir / "final.csv", index=False)

    # Sanity check — warn if technicals didn't populate
    if "technical_score" not in scored.columns or scored["technical_score"].isna().all():
        print("\n" + "!" * 60)
        print("WARNING: technical_score column is empty or missing.")
        print("Check output/technicals.csv → 'technical_error' column for the cause.")
        print("Report will still generate, but Tech Score / Supertrend will show '—'.")
        print("!" * 60)

    # Show top 10 in console
    print("\nTop 10 (Fundamental + Technical):")
    top_cols = ["ticker", "name", "sector", "market_cap",
                "multibagger_score", "technical_score",
                "supertrend_weekly_signal", "supertrend_weekly_weeks",
                "supertrend_daily_signal", "supertrend_daily_days",
                "rsi_14", "pct_from_200ma", "rationale"]
    top_cols = [c for c in top_cols if c in scored.columns]
    with pd.option_context("display.max_columns", None, "display.width", 280,
                            "display.max_colwidth", 50):
        print(scored[top_cols].head(10).to_string(index=False))

    # 6. REPORT
    print("\n" + "=" * 60)
    print("STEP 6: Generating HTML report")
    print("=" * 60)
    report_path = generate_html_report(
        scored_df=scored,
        universe_size=len(tickers),
        fetched_size=len(fetched),
        output_path=output_dir / "index.html",
        top_n=args.top,
    )
    print(f"\nDone. Open in browser:\n  file://{report_path.resolve()}")


if __name__ == "__main__":
    main()

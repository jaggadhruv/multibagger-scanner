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
import time
from pathlib import Path

import pandas as pd

from src.universe import get_universe, get_sample_tickers
from src.fetch import fetch_fundamentals
from src.screen import apply_filters
from src.score import compute_scores
from src.technicals import fetch_technicals
from src.report import generate_html_report


def _cooldown(seconds: int, label: str = "cooldown"):
    """Sleep with a visible countdown so long waits don't look like a hang."""
    if seconds <= 0:
        return
    print(f"\n{label}: waiting {seconds}s to let Yahoo rate limit clear...")
    remaining = seconds
    while remaining > 0:
        step = min(10, remaining)
        time.sleep(step)
        remaining -= step
        if remaining > 0:
            print(f"  {remaining}s remaining...")
    print("  done — resuming.")


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
    p.add_argument("--workers", type=int, default=4,
                   help="Concurrent yfinance fetches for fundamentals (default 4). "
                        "Lower = less likely to be rate-limited, but slower.")
    p.add_argument("--wait-before-technicals", type=int, default=60,
                   help="Seconds to wait between fundamentals and technicals fetch "
                        "(default 60). Gives Yahoo's rate limiter time to reset.")
    p.add_argument("--skip-fundamentals", action="store_true",
                   help="Skip fundamentals fetch — load from cached scored.csv. "
                        "Useful if fundamentals succeeded but technicals failed on a prior run.")
    p.add_argument("--skip-technicals", action="store_true",
                   help="Skip technicals fetch — generate report from fundamentals only.")
    args = p.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)

    if args.skip_fundamentals:
        # -------- Load cached fundamentals from previous run --------
        cached = output_dir / "scored.csv"
        if not cached.exists():
            print(f"ERROR: --skip-fundamentals requires {cached} to exist. Run once without --skip-fundamentals first.")
            sys.exit(1)
        print("=" * 60)
        print("STEP 1-4: SKIPPED (loading cached scored.csv)")
        print("=" * 60)
        scored = pd.read_csv(cached)
        print(f"Loaded {len(scored)} cached candidates from {cached}")
        tickers_len = int(scored["market_cap"].notna().sum()) if "market_cap" in scored.columns else len(scored)
        fetched_len = tickers_len
        universe_size = tickers_len  # unknown, use best available
    else:
        # -------- Full fundamentals pipeline --------
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

        universe_size = len(tickers)

        print("\n" + "=" * 60)
        print(f"STEP 2: Fetching fundamentals ({args.workers} parallel workers)")
        print("=" * 60)
        raw = fetch_fundamentals(tickers, max_workers=args.workers)
        raw_path = output_dir / "raw_data.csv"
        raw.to_csv(raw_path, index=False)
        print(f"Raw data saved: {raw_path}")

        fetched = raw[raw["error"].isna()].copy()
        fetched_len = len(fetched)
        print(f"Rows with data: {fetched_len}/{len(raw)}")

        print("\n" + "=" * 60)
        print("STEP 3: Applying filters")
        print("=" * 60)
        filtered = apply_filters(fetched, strict=not args.permissive)
        if len(filtered) == 0:
            print("\nNo candidates passed filters. Try --permissive or loosen thresholds in src/screen.py.")
            sys.exit(0)
        filtered.to_csv(output_dir / "filtered.csv", index=False)

        print("\n" + "=" * 60)
        print("STEP 4: Fundamental scoring")
        print("=" * 60)
        scored = compute_scores(filtered)
        scored.to_csv(output_dir / "scored.csv", index=False)
        print(f"Scored data saved: {output_dir/'scored.csv'} ({len(scored)} candidates)")

    # -------- Technicals with cooldown --------
    if not args.skip_technicals:
        _cooldown(args.wait_before_technicals,
                  label="Cooldown before technicals fetch")

        print("\n" + "=" * 60)
        print("STEP 5: Technical analysis (sequential yfinance, ~0.5s/ticker)")
        print("=" * 60)
        technicals = fetch_technicals(scored["ticker"].tolist())
        technicals.to_csv(output_dir / "technicals.csv", index=False)

        scored = scored.merge(technicals, on="ticker", how="left", suffixes=("", "_tech"))
        scored.to_csv(output_dir / "final.csv", index=False)

        if "technical_score" not in scored.columns or scored["technical_score"].isna().all():
            print("\n" + "!" * 60)
            print("WARNING: technical_score column is empty or missing.")
            print("Check output/technicals.csv → 'technical_error' column for the cause.")
            print("Tip: try re-running with --skip-fundamentals to retry just the technicals step.")
            print("!" * 60)
    else:
        print("\nSTEP 5: SKIPPED (--skip-technicals). Report will show fundamentals only.")

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

    # -------- REPORT --------
    print("\n" + "=" * 60)
    print("STEP 6: Generating HTML report")
    print("=" * 60)
    report_path = generate_html_report(
        scored_df=scored,
        universe_size=universe_size,
        fetched_size=fetched_len,
        output_path=output_dir / "index.html",
        top_n=args.top,
    )
    print(f"\nDone. Open in browser:\n  file://{report_path.resolve()}")


if __name__ == "__main__":
    main()

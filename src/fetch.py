"""
Fetch fundamental data from Yahoo Finance via yfinance.

Design notes:
- yfinance is unofficial and occasionally flaky. We wrap calls in try/except and
  return partial rows rather than crashing the whole run.
- We use ThreadPoolExecutor for parallelism but keep concurrency modest to avoid
  Yahoo rate-limiting the runner (especially on GitHub Actions).
- The `.info` dict is the fastest way to get most of what we need; when it fails
  we fall back to computing from statements where possible.
"""
from __future__ import annotations
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

# These are the fields we try to pull from yfinance's info dict.
# Missing fields will show up as NaN and are handled downstream.
INFO_FIELDS = {
    "name": "shortName",
    "long_name": "longName",
    "sector": "sector",
    "industry": "industry",
    "country": "country",
    "market_cap": "marketCap",
    "enterprise_value": "enterpriseValue",
    "pe_ratio": "trailingPE",
    "forward_pe": "forwardPE",
    "peg_ratio": "trailingPegRatio",  # yfinance has both; trailing is more reliable
    "price_to_book": "priceToBook",
    "price_to_sales": "priceToSalesTrailing12Months",
    "ev_to_ebitda": "enterpriseToEbitda",
    "ev_to_revenue": "enterpriseToRevenue",
    "roe": "returnOnEquity",
    "roa": "returnOnAssets",
    "operating_margin": "operatingMargins",
    "profit_margin": "profitMargins",
    "gross_margin": "grossMargins",
    "ebitda_margin": "ebitdaMargins",
    "revenue_growth": "revenueGrowth",
    "earnings_growth": "earningsGrowth",
    "quarterly_revenue_growth": "revenueQuarterlyGrowth",
    "quarterly_earnings_growth": "earningsQuarterlyGrowth",
    "debt_to_equity": "debtToEquity",
    "current_ratio": "currentRatio",
    "quick_ratio": "quickRatio",
    "total_cash": "totalCash",
    "total_debt": "totalDebt",
    "free_cash_flow": "freeCashflow",
    "operating_cash_flow": "operatingCashflow",
    "beta": "beta",
    "insider_ownership": "heldPercentInsiders",
    "institutional_ownership": "heldPercentInstitutions",
    "shares_outstanding": "sharesOutstanding",
    "float_shares": "floatShares",
    "short_percent": "shortPercentOfFloat",
    "price": "currentPrice",
    "week52_high": "fiftyTwoWeekHigh",
    "week52_low": "fiftyTwoWeekLow",
    "avg_volume": "averageVolume",
    "recommendation": "recommendationKey",
}


def _fetch_one(ticker: str, retries: int = 2) -> dict[str, Any]:
    """Fetch fundamentals for a single ticker with basic retry."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            stock = yf.Ticker(ticker)
            info = stock.info or {}
            if not info or "symbol" not in info and "shortName" not in info:
                # yfinance sometimes returns an almost-empty dict for delisted names
                raise ValueError("empty info")

            row: dict[str, Any] = {"ticker": ticker, "error": None}
            for our_name, yf_name in INFO_FIELDS.items():
                row[our_name] = info.get(yf_name)
            return row
        except Exception as e:
            last_err = str(e)
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    return {"ticker": ticker, "error": last_err}


def fetch_fundamentals(
    tickers: list[str],
    max_workers: int = 4,
    progress_every: int = 50,
) -> pd.DataFrame:
    """
    Fetch fundamentals for a list of tickers.

    Returns a DataFrame with one row per ticker. Failed fetches have their `error`
    column populated and other columns NaN.
    """
    n = len(tickers)
    print(f"Fetching fundamentals for {n} tickers (max_workers={max_workers})...")
    results: list[dict[str, Any]] = []
    start = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_fetch_one, t): t for t in tickers}
        for i, fut in enumerate(as_completed(futures), start=1):
            results.append(fut.result())
            if i % progress_every == 0 or i == n:
                elapsed = time.time() - start
                rate = i / elapsed if elapsed else 0
                eta = (n - i) / rate if rate else 0
                print(f"  {i}/{n} done · {rate:.1f}/s · ETA {eta:.0f}s")

    df = pd.DataFrame(results)
    n_ok = df["error"].isna().sum()
    print(f"Success: {n_ok}/{n} ({n_ok/n*100:.1f}%)")
    return df

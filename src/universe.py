"""
Ticker universe: S&P mid-cap + small-cap (default) or full S&P 1500.

Scraped from Wikipedia. Free, no API needed.

Default = S&P 400 (MidCap) + S&P 600 (SmallCap) = ~1000 tickers.
The S&P 500 is excluded by default because its members have a minimum ~$18B
market cap, which is too large to plausibly deliver multibagger returns.

Pass `include_large_cap=True` (or use --include-large on the CLI) to get the
full S&P 1500 if you want it for benchmarking or comparison.
"""
from __future__ import annotations
import pandas as pd
import requests
from io import StringIO

# Wikipedia pages for each index constituent list
INDEX_URLS = {
    "sp500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "sp400": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
    "sp600": "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
}

# Common column names for the ticker symbol across the three Wikipedia tables
SYMBOL_COL_CANDIDATES = ["Symbol", "Ticker symbol", "Ticker"]


def _fetch_wiki_table(url: str) -> pd.DataFrame:
    """Fetch first HTML table from a Wikipedia page. Uses requests + pandas.read_html."""
    headers = {"User-Agent": "Mozilla/5.0 (multibagger-screener; educational use)"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))
    if not tables:
        raise ValueError(f"No tables found at {url}")
    return tables[0]


def _extract_symbols(df: pd.DataFrame) -> list[str]:
    """Find the ticker column and return a clean list of symbols."""
    for col in SYMBOL_COL_CANDIDATES:
        if col in df.columns:
            symbols = df[col].astype(str).str.strip().tolist()
            # Yahoo Finance uses '-' where some sources use '.' (e.g. BRK.B -> BRK-B)
            symbols = [s.replace(".", "-") for s in symbols if s and s.lower() != "nan"]
            return symbols
    raise ValueError(f"Could not find symbol column. Columns available: {list(df.columns)}")


def get_universe(include_large_cap: bool = False) -> list[str]:
    """
    Return the ticker universe for the screener.

    Default (include_large_cap=False): S&P 400 (MidCap) + S&P 600 (SmallCap).
        ~1000 tickers — the actual multibagger hunting ground.
    include_large_cap=True: adds S&P 500 for ~1500 total.
        Included only for comparison/benchmarking; S&P 500 names are too big
        to plausibly deliver multibagger returns.
    """
    if include_large_cap:
        indices = ["sp500", "sp400", "sp600"]
    else:
        indices = ["sp400", "sp600"]

    all_tickers: set[str] = set()
    for name in indices:
        url = INDEX_URLS[name]
        try:
            df = _fetch_wiki_table(url)
            symbols = _extract_symbols(df)
            all_tickers.update(symbols)
            print(f"  {name}: {len(symbols)} tickers")
        except Exception as e:
            print(f"  {name}: FAILED — {e}")
    return sorted(all_tickers)


# Backwards-compatible alias
def get_sp1500_tickers() -> list[str]:
    """Alias — returns full S&P 1500. Prefer get_universe() in new code."""
    return get_universe(include_large_cap=True)


def get_sample_tickers() -> list[str]:
    """A small hand-picked set for quick smoke tests (bypasses network)."""
    return [
        "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA",
        "AVGO", "COST", "AMD", "CRM", "ADBE", "NFLX", "INTU",
        "AMAT", "LRCX", "KLAC", "SNPS", "CDNS", "PANW",
        "MELI", "SHOP", "SQ", "PYPL", "ABNB", "UBER",
        "ETSY", "ROKU", "PINS", "SNAP", "SPOT",
    ]


if __name__ == "__main__":
    tickers = get_universe()
    print(f"\nTotal unique tickers (mid+small cap only): {len(tickers)}")
    print(f"First 10: {tickers[:10]}")

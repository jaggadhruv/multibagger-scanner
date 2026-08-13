"""
Technical analysis with dual-source resilience.

Sources tried in order:
  1. stooq.com — fast, no rate limits when it works
  2. yfinance   — slower, sometimes rate-limited on cloud IPs, used as fallback

Both are free, no API key needed. If BOTH fail for a ticker, the actual
error content is included in `technical_error` so you can see exactly why.

Computes:
  * RSI (14-day, Wilder smoothing)
  * 50-day and 200-day moving averages
  * Distance from 200-day MA and 52-week high
  * Supertrend indicator (period=10, multiplier=3.0) — BUY / SELL signal
  * Technical Score (0-10): "how good is this entry point?"
"""
from __future__ import annotations
import random
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from io import StringIO
from typing import Any

import numpy as np
import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")

STOOQ_URL = "https://stooq.com/q/d/l/"

# Full browser User-Agent — critical for stooq/yahoo which block generic UAs
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/plain,text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://stooq.com/",
    "Connection": "keep-alive",
}


# ------------------------------------------------------------------ #
# Indicators
# ------------------------------------------------------------------ #

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI. >70 overbought, <30 oversold."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def supertrend(
    high: pd.Series, low: pd.Series, close: pd.Series,
    period: int = 10, multiplier: float = 3.0,
) -> tuple[pd.Series, pd.Series]:
    """Supertrend. Returns (line, trend) where trend is +1 (BUY) or -1 (SELL)."""
    hl = high - low
    hc = (high - close.shift()).abs()
    lc = (low - close.shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()

    hl2 = (high + low) / 2
    upper_basic = hl2 + multiplier * atr
    lower_basic = hl2 - multiplier * atr

    n = len(close)
    st = np.full(n, np.nan)
    trend = np.zeros(n, dtype=int)
    st[0] = lower_basic.iloc[0]
    trend[0] = 1

    close_v = close.values
    upper_v = upper_basic.values
    lower_v = lower_basic.values

    for i in range(1, n):
        prev_st = st[i - 1]
        prev_trend = trend[i - 1]
        curr_close = close_v[i]

        if prev_trend == 1:
            if curr_close < prev_st:
                st[i], trend[i] = upper_v[i], -1
            else:
                st[i], trend[i] = max(lower_v[i], prev_st), 1
        else:
            if curr_close > prev_st:
                st[i], trend[i] = lower_v[i], 1
            else:
                st[i], trend[i] = min(upper_v[i], prev_st), -1

    return pd.Series(st, index=close.index), pd.Series(trend, index=close.index)


def compute_technical_score(
    rsi_val: float | None, price: float, ma_200: float | None,
    high_52w: float, low_52w: float,
) -> float:
    """0-10 technical opportunity score (higher = better entry)."""
    score = 5.0

    if rsi_val is not None and not pd.isna(rsi_val):
        if 40 <= rsi_val <= 60:      score += 2.0
        elif 30 <= rsi_val < 40:     score += 1.5
        elif 60 < rsi_val <= 70:     score += 0.5
        elif rsi_val > 70:           score -= 2.0
        elif 20 <= rsi_val < 30:     score += 0.5
        elif rsi_val < 20:           score -= 1.0

    if ma_200 is not None and not pd.isna(ma_200) and ma_200 > 0:
        pct = (price - ma_200) / ma_200
        if 0.05 <= pct <= 0.20:      score += 1.5
        elif 0 <= pct < 0.05:        score += 1.0
        elif 0.20 < pct <= 0.40:     score += 0.0
        elif pct > 0.40:             score -= 1.5
        elif -0.10 <= pct < 0:       score -= 0.5
        elif pct < -0.10:            score -= 1.5

    if not pd.isna(high_52w) and high_52w > 0:
        pct_from_high = (high_52w - price) / high_52w
        if 0.10 <= pct_from_high <= 0.25:      score += 1.5
        elif 0.05 <= pct_from_high < 0.10:     score += 0.5
        elif pct_from_high < 0.05:             score -= 1.0
        elif 0.25 < pct_from_high <= 0.40:     score += 0.5
        elif pct_from_high > 0.40:             score -= 0.5

    return max(0.0, min(10.0, round(score, 1)))


# ------------------------------------------------------------------ #
# Source 1: stooq.com
# ------------------------------------------------------------------ #

def _stooq_symbol_variants(ticker: str) -> list[str]:
    """Return possible stooq symbol formats to try for a US ticker."""
    t = ticker.lower()
    variants = [f"{t}.us"]
    if "-" in t:
        variants.append(f"{t.replace('-', '.')}.us")
    return variants


def fetch_from_stooq(ticker: str, days: int = 400) -> tuple[pd.DataFrame | None, str]:
    """
    Try to fetch OHLC history from stooq.com.
    Returns (dataframe_or_None, error_message).
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    params_date = {
        "d1": start_date.strftime("%Y%m%d"),
        "d2": end_date.strftime("%Y%m%d"),
        "i": "d",
    }

    last_error = "unknown"
    for symbol in _stooq_symbol_variants(ticker):
        try:
            r = requests.get(
                STOOQ_URL,
                params={"s": symbol, **params_date},
                headers=BROWSER_HEADERS,
                timeout=20,
            )
        except Exception as e:
            last_error = f"request exception: {type(e).__name__}: {str(e)[:80]}"
            continue

        if r.status_code != 200:
            last_error = f"HTTP {r.status_code}"
            continue

        text = r.text.strip()

        # HTML response = block page or error
        if text.startswith("<") or "<html" in text[:200].lower():
            snippet = text[:100].replace("\n", " ")
            last_error = f"HTML response (blocked?): {snippet}"
            continue

        # Empty
        if not text or "\n" not in text:
            last_error = f"empty/single-line response: {text[:80]!r}"
            continue

        # Stooq "no data" for unknown symbol - try next variant
        if text.lower().startswith("no data"):
            last_error = "no data (unknown symbol)"
            continue

        # Expect CSV header
        if not text.startswith("Date"):
            last_error = f"unexpected format: {text[:80]!r}"
            continue

        try:
            df = pd.read_csv(StringIO(text))
            if df.empty or "Close" not in df.columns:
                last_error = "CSV parsed but no Close column"
                continue
            df["Date"] = pd.to_datetime(df["Date"])
            df = df.set_index("Date").sort_index().dropna(subset=["Close"])
            if len(df) < 60:
                last_error = f"only {len(df)} rows (need 60+)"
                continue
            return df, ""
        except Exception as e:
            last_error = f"CSV parse error: {type(e).__name__}: {str(e)[:80]}"
            continue

    return None, last_error


# ------------------------------------------------------------------ #
# Source 2: yfinance (fallback, slower)
# ------------------------------------------------------------------ #

def fetch_from_yfinance(ticker: str, retries: int = 2) -> tuple[pd.DataFrame | None, str]:
    """
    Fallback: fetch via yfinance. Rate-limited on cloud IPs but sometimes works.
    Returns (dataframe_or_None, error_message).
    """
    last_error = "unknown"
    for attempt in range(retries + 1):
        try:
            hist = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
            if hist is not None and not hist.empty and "Close" in hist.columns:
                hist = hist.dropna(subset=["Close"])
                if len(hist) >= 60:
                    return hist, ""
                last_error = f"yfinance: only {len(hist)} rows"
            else:
                last_error = "yfinance: empty response"
        except Exception as e:
            last_error = f"yfinance: {type(e).__name__}: {str(e)[:80]}"

        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))

    return None, last_error


# ------------------------------------------------------------------ #
# Indicator computation
# ------------------------------------------------------------------ #

def _compute_indicators(ticker: str, hist: pd.DataFrame, source: str) -> dict[str, Any]:
    close = hist["Close"]
    high = hist["High"]
    low = hist["Low"]

    current_price = float(close.iloc[-1])

    rsi_series = rsi(close)
    rsi_val = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else None

    ma_50 = close.rolling(50).mean().iloc[-1]
    ma_200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else np.nan
    ma_50_v = float(ma_50) if not pd.isna(ma_50) else None
    ma_200_v = float(ma_200) if not pd.isna(ma_200) else None

    last_252 = close.tail(252)
    high_52w = float(last_252.max())
    low_52w = float(last_252.min())

    pct_from_200ma = ((current_price - ma_200_v) / ma_200_v * 100) if ma_200_v else None
    pct_from_52w_high = ((high_52w - current_price) / high_52w * 100) if high_52w > 0 else None

    _, st_trend = supertrend(high, low, close)
    current_signal = "BUY" if st_trend.iloc[-1] == 1 else "SELL"
    flips = (st_trend != st_trend.shift()).cumsum()
    days_in_trend = int((flips == flips.iloc[-1]).sum())

    tech_score = compute_technical_score(rsi_val, current_price, ma_200_v, high_52w, low_52w)

    return {
        "ticker": ticker,
        "technical_error": None,
        "technical_source": source,
        "current_price": round(current_price, 2),
        "rsi_14": round(rsi_val, 1) if rsi_val is not None else None,
        "ma_50": round(ma_50_v, 2) if ma_50_v else None,
        "ma_200": round(ma_200_v, 2) if ma_200_v else None,
        "pct_from_200ma": round(pct_from_200ma, 1) if pct_from_200ma is not None else None,
        "pct_from_52w_high": round(pct_from_52w_high, 1) if pct_from_52w_high is not None else None,
        "technical_score": tech_score,
        "supertrend_signal": current_signal,
        "supertrend_days": days_in_trend,
    }


# ------------------------------------------------------------------ #
# Public API
# ------------------------------------------------------------------ #

def _worker(ticker: str, use_yf_fallback: bool = True) -> dict[str, Any]:
    """Try stooq first, then yfinance if enabled."""
    time.sleep(random.uniform(0, 0.15))

    hist, stooq_err = fetch_from_stooq(ticker)
    if hist is not None:
        try:
            return _compute_indicators(ticker, hist, source="stooq")
        except Exception as e:
            stooq_err = f"compute error: {type(e).__name__}: {e}"

    if not use_yf_fallback:
        return {"ticker": ticker, "technical_error": f"stooq: {stooq_err}"}

    # Fallback with polite delay to avoid rate limit
    time.sleep(random.uniform(1.0, 2.5))
    hist, yf_err = fetch_from_yfinance(ticker)
    if hist is not None:
        try:
            return _compute_indicators(ticker, hist, source="yfinance")
        except Exception as e:
            yf_err = f"compute error: {type(e).__name__}: {e}"

    return {"ticker": ticker, "technical_error": f"stooq: {stooq_err} | {yf_err}"}


def fetch_technicals(
    tickers: list[str],
    max_workers: int = 8,
    use_yf_fallback: bool = True,
    **_kwargs,
) -> pd.DataFrame:
    """
    Fetch technicals for a list of tickers.

    Tries stooq.com first, falls back to yfinance if enabled. Set
    `use_yf_fallback=False` to skip the fallback (faster but less resilient).
    """
    n = len(tickers)
    print(f"Fetching technicals for {n} candidates "
          f"(source: stooq → yfinance fallback: {use_yf_fallback}, workers: {max_workers})")

    results: list[dict[str, Any]] = []
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_worker, t, use_yf_fallback): t for t in tickers}
        for i, fut in enumerate(as_completed(futures), start=1):
            results.append(fut.result())
            if i % 20 == 0 or i == n:
                elapsed = time.time() - t0
                print(f"  {i}/{n} done · {elapsed:.0f}s elapsed")

    df = pd.DataFrame(results)
    n_ok = df["technical_error"].isna().sum() if "technical_error" in df.columns else 0
    print(f"\nTechnicals success: {n_ok}/{n} ({n_ok/n*100:.0f}%)")

    # Source breakdown
    if "technical_source" in df.columns:
        by_source = df[df["technical_source"].notna()]["technical_source"].value_counts()
        for src, cnt in by_source.items():
            print(f"  via {src}: {cnt}")

    # If a lot failed, dump sample errors so you can see WHAT is failing
    if n_ok < n * 0.8 and "technical_error" in df.columns:
        errs = df[df["technical_error"].notna()]["technical_error"]
        print("\nSample errors (top 5 unique):")
        for err in errs.drop_duplicates().head(5).tolist():
            print(f"  {err[:200]}")

    return df

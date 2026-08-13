"""
Technical analysis for filtered candidates.

Data source: stooq.com (free, no rate limits, no API key)
  Why not yfinance for prices? On GitHub Actions and other cloud IPs,
  Yahoo's backend aggressively rate-limits (HTTP 429). Stooq is a free
  Polish financial data provider that serves EOD OHLC as CSV and does
  not rate-limit. Perfect for our daily/weekly screener workflow.

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

warnings.filterwarnings("ignore")

STOOQ_URL = "https://stooq.com/q/d/l/"
UA = "Mozilla/5.0 (multibagger-screener; educational use)"


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
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
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
                st[i] = upper_v[i]
                trend[i] = -1
            else:
                st[i] = max(lower_v[i], prev_st)
                trend[i] = 1
        else:
            if curr_close > prev_st:
                st[i] = lower_v[i]
                trend[i] = 1
            else:
                st[i] = min(upper_v[i], prev_st)
                trend[i] = -1

    return pd.Series(st, index=close.index), pd.Series(trend, index=close.index)


def compute_technical_score(
    rsi_val: float | None,
    price: float,
    ma_200: float | None,
    high_52w: float,
    low_52w: float,
) -> float:
    """Composite technical opportunity score 0-10 (higher = better entry)."""
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
# Stooq fetcher
# ------------------------------------------------------------------ #

def _stooq_symbol_variants(ticker: str) -> list[str]:
    """Return possible stooq symbol formats to try for a US ticker."""
    t = ticker.lower()
    variants = [f"{t}.us"]
    # Class shares: yfinance uses BRK-B, stooq sometimes uses brk-b, sometimes brk.b
    if "-" in t:
        variants.append(f"{t.replace('-', '.')}.us")
    return variants


def _fetch_stooq(ticker: str, days: int = 400, retries: int = 2) -> pd.DataFrame | None:
    """
    Fetch daily OHLC history from stooq.com.
    Free, no rate limits, no auth. Returns None on any failure.
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    params_date = {
        "d1": start_date.strftime("%Y%m%d"),
        "d2": end_date.strftime("%Y%m%d"),
        "i": "d",
    }

    for symbol in _stooq_symbol_variants(ticker):
        for attempt in range(retries + 1):
            try:
                r = requests.get(
                    STOOQ_URL,
                    params={"s": symbol, **params_date},
                    headers={"User-Agent": UA},
                    timeout=15,
                )
                if r.status_code != 200:
                    if attempt < retries:
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    break

                text = r.text.strip()
                # Stooq returns literal "No data" for unknown symbols
                if not text or text.lower().startswith("no data") or "\n" not in text:
                    break  # try next variant

                df = pd.read_csv(StringIO(text))
                if df.empty or "Close" not in df.columns:
                    break

                df["Date"] = pd.to_datetime(df["Date"])
                df = df.set_index("Date").sort_index()
                # Drop rows with missing Close (should be rare from stooq)
                df = df.dropna(subset=["Close"])
                return df if len(df) >= 60 else None
            except Exception:
                if attempt < retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                break
    return None


# ------------------------------------------------------------------ #
# Per-ticker indicator computation
# ------------------------------------------------------------------ #

def _compute_indicators(ticker: str, hist: pd.DataFrame) -> dict[str, Any]:
    """Given a per-ticker OHLC DataFrame, compute all indicators."""
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

    st_line, st_trend = supertrend(high, low, close)
    current_signal = "BUY" if st_trend.iloc[-1] == 1 else "SELL"

    flips = (st_trend != st_trend.shift()).cumsum()
    days_in_trend = int((flips == flips.iloc[-1]).sum())

    tech_score = compute_technical_score(rsi_val, current_price, ma_200_v, high_52w, low_52w)

    return {
        "ticker": ticker,
        "technical_error": None,
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

def _worker(ticker: str) -> dict[str, Any]:
    """Small jitter, fetch, compute — one ticker."""
    time.sleep(random.uniform(0, 0.15))  # spread out calls
    hist = _fetch_stooq(ticker)
    if hist is None:
        return {"ticker": ticker, "technical_error": "no history from stooq"}
    try:
        return _compute_indicators(ticker, hist)
    except Exception as e:
        return {"ticker": ticker, "technical_error": f"{type(e).__name__}: {e}"}


def fetch_technicals(
    tickers: list[str],
    max_workers: int = 10,
    **_kwargs,   # accept extra kwargs from old callers
) -> pd.DataFrame:
    """
    Fetch technicals for a list of tickers via stooq.com.

    Stooq doesn't rate-limit, so we can use modest parallelism (10 workers).
    Intended to be called with FILTERED candidates only (30-100 tickers),
    though it scales fine to hundreds.
    """
    n = len(tickers)
    print(f"Fetching price history from stooq.com for {n} candidates "
          f"(max_workers={max_workers})...")

    results: list[dict[str, Any]] = []
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_worker, t): t for t in tickers}
        for i, fut in enumerate(as_completed(futures), start=1):
            results.append(fut.result())
            if i % 20 == 0 or i == n:
                elapsed = time.time() - t0
                print(f"  {i}/{n} done · {elapsed:.0f}s elapsed")

    df = pd.DataFrame(results)
    n_ok = df["technical_error"].isna().sum() if "technical_error" in df.columns else 0
    print(f"\nTechnicals success: {n_ok}/{n} ({n_ok/n*100:.0f}%)")

    # Sample errors if a lot failed
    if n_ok < n * 0.8 and "technical_error" in df.columns:
        errs = df[df["technical_error"].notna()]["technical_error"]
        print("Top error types:")
        for err, count in errs.value_counts().head(3).items():
            print(f"  ({count}x) {err[:140]}")

    return df

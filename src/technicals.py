"""
Technical analysis for filtered candidates.

Computes:
  * RSI (14-day, Wilder smoothing)
  * 50-day and 200-day moving averages
  * Distance from 200-day MA and 52-week high
  * Supertrend indicator (period=10, multiplier=3.0) — BUY / SELL signal
  * Technical Score (0-10): "how good is this entry point?"

Fetching strategy:
  We ONLY compute technicals for candidates that passed the fundamental filter.
  This keeps the extra data fetch bounded — typically 30-100 candidates instead
  of the whole 1000-ticker universe.
"""
from __future__ import annotations
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")


# ------------------------------------------------------------------ #
# Individual indicators
# ------------------------------------------------------------------ #

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    Wilder's RSI. Standard 14-period.

    >70 → overbought, <30 → oversold, 40-60 → neutral sweet spot.
    """
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
    """
    Supertrend indicator.

    Returns (supertrend_line, trend) where trend is +1 (uptrend, BUY) or -1
    (downtrend, SELL).

    Formula: ATR-based bands around HL/2. Line flips when price crosses through.
    Standard parameters: period=10, multiplier=3.0.
    """
    # True Range
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
        curr_upper = upper_v[i]
        curr_lower = lower_v[i]

        if prev_trend == 1:
            if curr_close < prev_st:
                st[i] = curr_upper
                trend[i] = -1
            else:
                st[i] = max(curr_lower, prev_st)
                trend[i] = 1
        else:
            if curr_close > prev_st:
                st[i] = curr_lower
                trend[i] = 1
            else:
                st[i] = min(curr_upper, prev_st)
                trend[i] = -1

    return pd.Series(st, index=close.index), pd.Series(trend, index=close.index)


# ------------------------------------------------------------------ #
# Technical score (0-10)
# ------------------------------------------------------------------ #

def compute_technical_score(
    rsi_val: float | None,
    price: float,
    ma_200: float | None,
    high_52w: float,
    low_52w: float,
) -> float:
    """
    Composite technical opportunity score, 0-10.

    Higher = better entry point (not overextended, healthy trend).
    Anchored at 5.0 (neutral); components add or subtract.
    """
    score = 5.0

    # RSI
    if rsi_val is not None and not pd.isna(rsi_val):
        if 40 <= rsi_val <= 60:      score += 2.0
        elif 30 <= rsi_val < 40:     score += 1.5
        elif 60 < rsi_val <= 70:     score += 0.5
        elif rsi_val > 70:           score -= 2.0
        elif 20 <= rsi_val < 30:     score += 0.5
        elif rsi_val < 20:           score -= 1.0

    # 200MA
    if ma_200 is not None and not pd.isna(ma_200) and ma_200 > 0:
        pct = (price - ma_200) / ma_200
        if 0.05 <= pct <= 0.20:      score += 1.5
        elif 0 <= pct < 0.05:        score += 1.0
        elif 0.20 < pct <= 0.40:     score += 0.0
        elif pct > 0.40:             score -= 1.5
        elif -0.10 <= pct < 0:       score -= 0.5
        elif pct < -0.10:            score -= 1.5

    # 52w range
    if not pd.isna(high_52w) and high_52w > 0:
        pct_from_high = (high_52w - price) / high_52w
        if 0.10 <= pct_from_high <= 0.25:      score += 1.5
        elif 0.05 <= pct_from_high < 0.10:     score += 0.5
        elif pct_from_high < 0.05:             score -= 1.0
        elif 0.25 < pct_from_high <= 0.40:     score += 0.5
        elif pct_from_high > 0.40:             score -= 0.5

    return max(0.0, min(10.0, round(score, 1)))


# ------------------------------------------------------------------ #
# Per-ticker orchestration
# ------------------------------------------------------------------ #

def _compute_for_ticker(ticker: str) -> dict[str, Any]:
    """Fetch 1 year of daily OHLC and compute indicators + score for one ticker."""
    try:
        hist = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
        if hist is None or len(hist) < 60:
            return {"ticker": ticker, "technical_error": "insufficient history"}

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
    except Exception as e:
        return {"ticker": ticker, "technical_error": str(e)}


def fetch_technicals(tickers: list[str], max_workers: int = 8) -> pd.DataFrame:
    """
    Compute technicals for a list of tickers in parallel.
    Intended to be called with the FILTERED candidate list, not the full universe.
    """
    n = len(tickers)
    print(f"Fetching price history + computing technicals for {n} candidates...")
    results: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_compute_for_ticker, t): t for t in tickers}
        for i, fut in enumerate(as_completed(futures), start=1):
            results.append(fut.result())
            if i % 20 == 0 or i == n:
                print(f"  {i}/{n} done")

    df = pd.DataFrame(results)
    n_ok = df["technical_error"].isna().sum() if "technical_error" in df.columns else 0
    print(f"Technicals success: {n_ok}/{n}")
    return df

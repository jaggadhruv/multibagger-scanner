"""
Technical analysis using sequential yfinance fetch.

INSPIRED BY the user's proven Supertrend scanner (engine.py). The parallel
fetch approach kept hitting Yahoo's rate limits from GitHub Actions IPs.
Their working scanner uses SEQUENTIAL calls with a 0.3s delay — Yahoo
tolerates that pattern because it looks like a real user, not a scraper.

Trade-off: ~30-60 seconds for 30-100 candidates (vs 5-10s if parallel worked).
Reliability > speed.

Computes per ticker:
  * RSI (14-day, Wilder smoothing)
  * 50-day and 200-day moving averages
  * Distance from 200-day MA and 52-week high
  * Supertrend DAILY (ATR period=10, multiplier=2.5) — short-term signal
  * Supertrend WEEKLY (same params, daily bars resampled to weekly) — long-term trend
  * Technical Score (0-10): "how good is this entry point?"

Reading the two Supertrend signals together:
  * Weekly BUY + Daily BUY  → high conviction uptrend, all timeframes aligned
  * Weekly BUY + Daily SELL → uptrend with short-term pullback (possible entry)
  * Weekly SELL + Daily BUY → downtrend with short-term bounce (be cautious)
  * Weekly SELL + Daily SELL → all timeframes bearish
"""
from __future__ import annotations
import time
import warnings
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

from src.supertrend import calculate_supertrend

warnings.filterwarnings("ignore")

# --- Supertrend & fetch parameters (match user's working scanner) ---
ATR_PERIOD = 10
ATR_MULTIPLIER = 2.5
LOOKBACK_PERIOD = "3y"     # 3y daily → ~750 daily bars, ~156 weekly bars after resample
INTERVAL = "1d"
FETCH_DELAY = 0.3          # seconds between yfinance calls — polite pacing
MIN_BARS_REQUIRED = 60     # need enough bars for MA/RSI to stabilize
MIN_WEEKLY_BARS = 15       # need ~15 weekly bars for Supertrend ATR(10) to settle


# ------------------------------------------------------------------ #
# Individual indicators
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


def compute_technical_score(
    rsi_val: float | None, price: float, ma_200: float | None,
    high_52w: float, low_52w: float,
) -> float:
    """0-10 technical opportunity score (higher = better entry point)."""
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
# Fetch (sequential — this is the key to reliability)
# ------------------------------------------------------------------ #

def _fetch_history(ticker: str) -> tuple[pd.DataFrame | None, str]:
    """
    Fetch 1y of daily OHLC via yfinance. Sequential caller ensures we
    don't get rate-limited. Mirrors engine.py's fetch_data().
    """
    try:
        data = yf.Ticker(ticker).history(
            period=LOOKBACK_PERIOD,
            interval=INTERVAL,
            auto_adjust=False,
        )
        if data is None or data.empty:
            return None, "no data returned"
        data = data.dropna(subset=["High", "Low", "Close"])
        if len(data) < MIN_BARS_REQUIRED:
            return None, f"only {len(data)} bars (need {MIN_BARS_REQUIRED}+)"
        return data, ""
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:100]}"


def _supertrend_state(hist: pd.DataFrame) -> tuple[str | None, int | None]:
    """
    Run Supertrend on a per-ticker OHLC DataFrame and extract:
      - current signal ("BUY" if Direction=+1, else "SELL")
      - number of trailing bars in the current trend
    Returns (None, None) if not enough data.
    """
    st_df = calculate_supertrend(hist, period=ATR_PERIOD, multiplier=ATR_MULTIPLIER)
    st_df = st_df.dropna(subset=["Supertrend"])
    if len(st_df) < 2:
        return None, None

    direction_now = int(st_df.iloc[-1]["Direction"])
    signal = "BUY" if direction_now == 1 else "SELL"

    dirs = st_df["Direction"].values
    bars_in_trend = 1
    for i in range(len(dirs) - 2, -1, -1):
        if dirs[i] == direction_now:
            bars_in_trend += 1
        else:
            break
    return signal, bars_in_trend


def _daily_to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """
    Resample daily OHLC to weekly (week ending Friday).
    - Open: first day's open
    - High: max of the week
    - Low:  min of the week
    - Close: last day's close
    - Volume: sum of the week
    """
    weekly = daily.resample("W-FRI", label="right").agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }).dropna(subset=["High", "Low", "Close"])
    return weekly


def _compute_indicators(ticker: str, hist: pd.DataFrame) -> dict[str, Any]:
    """Compute all indicators (daily + weekly) from a per-ticker OHLC DataFrame."""
    close = hist["Close"]

    current_price = float(close.iloc[-1])

    # RSI (daily)
    rsi_series = rsi(close)
    rsi_val = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else None

    # Moving averages (daily)
    ma_50 = close.rolling(50).mean().iloc[-1]
    ma_200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else np.nan
    ma_50_v = float(ma_50) if not pd.isna(ma_50) else None
    ma_200_v = float(ma_200) if not pd.isna(ma_200) else None

    # 52-week range (last 252 daily bars = 1 year)
    last_252 = close.tail(252)
    high_52w = float(last_252.max())
    low_52w = float(last_252.min())

    pct_from_200ma = ((current_price - ma_200_v) / ma_200_v * 100) if ma_200_v else None
    pct_from_52w_high = ((high_52w - current_price) / high_52w * 100) if high_52w > 0 else None

    # Supertrend — DAILY (on the daily bars directly)
    daily_signal, daily_bars = _supertrend_state(hist)

    # Supertrend — WEEKLY (resample daily → weekly, then compute)
    weekly_hist = _daily_to_weekly(hist)
    if len(weekly_hist) >= MIN_WEEKLY_BARS:
        weekly_signal, weekly_bars = _supertrend_state(weekly_hist)
    else:
        weekly_signal, weekly_bars = None, None

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
        "supertrend_daily_signal": daily_signal,
        "supertrend_daily_days": daily_bars,
        "supertrend_weekly_signal": weekly_signal,
        "supertrend_weekly_weeks": weekly_bars,
    }


# ------------------------------------------------------------------ #
# Public API
# ------------------------------------------------------------------ #

def fetch_technicals(tickers: list[str], **_kwargs) -> pd.DataFrame:
    """
    Sequential fetch of price history + indicator computation.

    Uses a 0.3s delay between yfinance calls — matches the pattern
    proven to work reliably in the user's own scanner engine. Slower
    than parallel but doesn't trigger Yahoo's rate limiting.

    For 30-100 candidates: expect ~30-60 seconds total.
    """
    n = len(tickers)
    print(f"Fetching technicals for {n} candidates "
          f"(sequential, {FETCH_DELAY}s delay between calls)")

    results: list[dict[str, Any]] = []
    t0 = time.time()

    for i, ticker in enumerate(tickers, 1):
        hist, err = _fetch_history(ticker)
        if hist is None:
            results.append({"ticker": ticker, "technical_error": err})
            status = f"failed: {err[:40]}"
        else:
            try:
                results.append(_compute_indicators(ticker, hist))
                status = "ok"
            except Exception as e:
                err = f"compute: {type(e).__name__}: {str(e)[:80]}"
                results.append({"ticker": ticker, "technical_error": err})
                status = f"failed: {err[:40]}"

        if i % 10 == 0 or i == n:
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed else 0
            eta = (n - i) / rate if rate else 0
            print(f"  {i}/{n} · last: {ticker} → {status} · {elapsed:.0f}s elapsed · ETA {eta:.0f}s")

        # The critical bit — small delay between calls avoids rate limit
        if i < n:
            time.sleep(FETCH_DELAY)

    df = pd.DataFrame(results)
    n_ok = df["technical_error"].isna().sum() if "technical_error" in df.columns else 0
    print(f"\nTechnicals success: {n_ok}/{n} ({n_ok/n*100:.0f}%)")

    if n_ok < n * 0.8 and "technical_error" in df.columns:
        errs = df[df["technical_error"].notna()]["technical_error"]
        print("Sample errors (top 3 unique):")
        for err, count in errs.value_counts().head(3).items():
            print(f"  ({count}x) {err[:140]}")

    return df

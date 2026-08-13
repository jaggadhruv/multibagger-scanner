"""
supertrend.py
-------------
Standalone implementation of the Supertrend indicator.

No third-party TA library is used on purpose (pandas_ta has had repeated
numpy-compatibility breakages), so this only depends on pandas/numpy which
are rock solid. The math follows the standard Supertrend formula used by
most charting platforms (ATR smoothed with Wilder's method / RMA).
"""

import pandas as pd
import numpy as np


def calculate_atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder's smoothed ATR (the standard used for Supertrend)."""
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # Wilder smoothing == ewm with alpha = 1/period, adjust=False
    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return atr


def calculate_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 2.5) -> pd.DataFrame:
    """
    Adds ATR, FinalUpper, FinalLower, Supertrend and Direction columns to a copy
    of df. df must have High, Low, Close columns, sorted oldest -> newest.

    Direction:  1 = bullish (price above the Supertrend line)
               -1 = bearish (price below the Supertrend line)
    """
    df = df.copy()
    high, low, close = df["High"], df["Low"], df["Close"]

    atr = calculate_atr(df, period)
    hl2 = (high + low) / 2

    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()

    n = len(df)
    fu = final_upper.to_numpy(copy=True)
    fl = final_lower.to_numpy(copy=True)
    bu = basic_upper.to_numpy(copy=True)
    bl = basic_lower.to_numpy(copy=True)
    c = close.to_numpy(copy=True)

    supertrend = np.full(n, np.nan)
    direction = np.zeros(n, dtype=int)

    # Seed the first valid value once ATR is available. NOTE: the band
    # recurrence must only start running from this point onward - if it
    # started at i=1 it would immediately propagate NaN forever, because
    # any comparison against a NaN previous band is always False.
    first_valid = atr.first_valid_index()
    if first_valid is None:
        df["ATR"] = atr
        df["FinalUpper"] = final_upper
        df["FinalLower"] = final_lower
        df["Supertrend"] = supertrend
        df["Direction"] = direction
        return df

    start = df.index.get_loc(first_valid)
    fu[start] = bu[start]
    fl[start] = bl[start]
    supertrend[start] = fu[start]
    direction[start] = -1

    for i in range(start + 1, n):
        fu[i] = bu[i] if (bu[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = bl[i] if (bl[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]

        if supertrend[i - 1] == fu[i - 1]:
            if c[i] <= fu[i]:
                supertrend[i] = fu[i]
                direction[i] = -1
            else:
                supertrend[i] = fl[i]
                direction[i] = 1
        else:
            if c[i] >= fl[i]:
                supertrend[i] = fl[i]
                direction[i] = 1
            else:
                supertrend[i] = fu[i]
                direction[i] = -1

    df["ATR"] = atr
    df["FinalUpper"] = final_upper
    df["FinalLower"] = final_lower
    df["Supertrend"] = supertrend
    df["Direction"] = direction
    return df


if __name__ == "__main__":
    # quick self-test with synthetic random-walk weekly candles
    rng = np.random.default_rng(7)
    n = 150
    close = 100 + np.cumsum(rng.normal(0, 2, n))
    high = close + rng.uniform(0.5, 3, n)
    low = close - rng.uniform(0.5, 3, n)
    dates = pd.date_range("2023-01-02", periods=n, freq="W-MON")
    test_df = pd.DataFrame({"High": high, "Low": low, "Close": close}, index=dates)

    result = calculate_supertrend(test_df, period=10, multiplier=2.5)
    flips = result["Direction"].diff().fillna(0) != 0
    print(f"Rows: {len(result)}, flips detected: {flips.sum()}")
    print(result.tail(8)[["Close", "Supertrend", "Direction"]])

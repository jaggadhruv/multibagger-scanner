"""
Price history and chart measurements.

Prices come from Yahoo Finance through yfinance, fetched ONE ticker at a time
with a short pause between calls. That sequential pattern is the one that
runs reliably from GitHub Actions; parallel calls get rate limited.

Only a handful of tickers reach this step (the ones that already passed the
business gate), so the whole thing takes well under a minute.
"""
from __future__ import annotations
import time
import warnings

import numpy as np
import pandas as pd
import yfinance as yf

from src.supertrend import calculate_supertrend

warnings.filterwarnings("ignore")

ATR_PERIOD = 10
ATR_MULTIPLIER = 2.5
LOOKBACK = "3y"
MIN_DAILY_BARS = 60
MIN_WEEKLY_BARS = 15


def is_rate_limit(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return "ratelimit" in text or "rate limit" in text or "too many requests" in text or "429" in text


def fetch_history(symbol: str, delay: float = 0.4, rate_limit_wait: int = 20):
    """
    Returns (DataFrame or None, error text). Index is timezone-naive dates,
    oldest first. Retries once after a pause if Yahoo rate limits the call.
    """
    err = "no data returned"
    for attempt in (1, 2):
        try:
            data = yf.Ticker(symbol).history(period=LOOKBACK, interval="1d", auto_adjust=False)
            if data is not None and not data.empty:
                data = data.dropna(subset=["High", "Low", "Close"]).copy()
                idx = pd.DatetimeIndex(data.index)
                if idx.tz is not None:
                    idx = idx.tz_localize(None)
                data.index = idx.normalize()
                data = data[~data.index.duplicated(keep="last")].sort_index()
                time.sleep(delay)
                return data, ""
            err = "no data returned"
        except Exception as e:  # noqa: BLE001 - yfinance raises many types
            err = f"{type(e).__name__}: {str(e)[:100]}"
            if is_rate_limit(e) and attempt == 1:
                print(f"    [{symbol}] rate limited, waiting {rate_limit_wait}s and retrying once")
                time.sleep(rate_limit_wait)
                continue
        break
    time.sleep(delay)
    return None, err


def fetch_benchmark(symbols: list[str], delay: float = 0.4):
    """Try each benchmark symbol in turn. Returns (DataFrame or None, symbol used)."""
    for s in symbols:
        hist, _ = fetch_history(s, delay)
        if hist is not None and len(hist) >= 260:
            return hist, s
    return None, None


# ------------------------------------------------------------------ #
# Indicators
# ------------------------------------------------------------------ #

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def supertrend_state(ohlc: pd.DataFrame):
    """(signal, bars in current trend) using the same Supertrend as the scanner."""
    st = calculate_supertrend(ohlc, period=ATR_PERIOD, multiplier=ATR_MULTIPLIER)
    st = st.dropna(subset=["Supertrend"])
    if len(st) < 2:
        return None, None
    dirs = st["Direction"].to_numpy()
    now = int(dirs[-1])
    bars = 1
    for i in range(len(dirs) - 2, -1, -1):
        if dirs[i] == now:
            bars += 1
        else:
            break
    return ("BUY" if now == 1 else "SELL"), bars


def to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    if "Volume" in daily.columns:
        agg["Volume"] = "sum"
    cols = [c for c in agg if c in daily.columns]
    return daily[cols].resample("W-FRI", label="right").agg({c: agg[c] for c in cols}).dropna(subset=["High", "Low", "Close"])


def _window_return(close: pd.Series, bars: int):
    """% return over the last `bars` trading days, with the start and end dates."""
    if len(close) <= bars:
        return None, None, None
    start, end = close.iloc[-bars - 1], close.iloc[-1]
    if not start or start <= 0:
        return None, None, None
    return float((end / start - 1.0) * 100.0), close.index[-bars - 1], close.index[-1]


def _bench_return(bench_close: pd.Series | None, start, end):
    if bench_close is None or start is None:
        return None
    a = bench_close.asof(start)
    b = bench_close.asof(end)
    if a is None or b is None or pd.isna(a) or pd.isna(b) or a <= 0:
        return None
    return float((b / a - 1.0) * 100.0)


def chart_metrics(hist: pd.DataFrame, bench: pd.DataFrame | None) -> dict:
    """All chart measurements for one stock. Percentages are plain numbers."""
    close = hist["Close"]
    price = float(close.iloc[-1])
    out = {
        "error": None,
        "close": round(price, 2),
        "last_bar": close.index[-1].strftime("%Y-%m-%d"),
        "ma200": None, "pct_above_200": None, "ma200_rising": None,
        "pct_from_high": None, "rsi": None,
        "ret_3m": None, "ret_6m": None, "ret_12m": None,
        "rs_3m": None, "rs_6m": None, "rs_12m": None,
        "benchmark_ok": bench is not None,
        "weekly_signal": None, "weekly_bars": None,
        "daily_signal": None, "daily_bars": None,
    }

    if len(close) >= 200:
        ma200 = close.rolling(200).mean()
        now = float(ma200.iloc[-1])
        out["ma200"] = round(now, 2)
        out["pct_above_200"] = float((price / now - 1.0) * 100.0)
        if len(ma200.dropna()) > 21:
            out["ma200_rising"] = bool(now > float(ma200.iloc[-22]))
        else:
            out["ma200_rising"] = False

    high = float(close.tail(252).max())
    if high > 0:
        out["pct_from_high"] = (1.0 - price / high) * 100.0

    r = rsi(close).iloc[-1]
    out["rsi"] = None if pd.isna(r) else float(r)

    bench_close = bench["Close"] if bench is not None else None
    for key, bars in (("3m", 63), ("6m", 126), ("12m", 252)):
        ret, start, end = _window_return(close, bars)
        out[f"ret_{key}"] = ret
        if ret is None:
            continue
        b = _bench_return(bench_close, start, end)
        # With no index data, fall back to the plain return (flagged in the report)
        out[f"rs_{key}"] = ret - b if b is not None else (ret if bench_close is None else None)

    out["daily_signal"], out["daily_bars"] = supertrend_state(hist)
    weekly = to_weekly(hist)
    if len(weekly) >= MIN_WEEKLY_BARS:
        out["weekly_signal"], out["weekly_bars"] = supertrend_state(weekly)
    return out


def fetch_charts(symbols: list[str], bench: pd.DataFrame | None, suffix: str = "", delay: float = 0.4):
    """
    Sequential fetch for a short list of symbols.
    Returns ({symbol: metrics dict}, {symbol: price history}).
    """
    charts, histories = {}, {}
    n = len(symbols)
    if n:
        print(f"Fetching price history for {n} stocks, one at a time...")
    for i, sym in enumerate(symbols, 1):
        hist, err = fetch_history(sym + suffix, delay)
        if hist is None or len(hist) < MIN_DAILY_BARS:
            charts[sym] = {"error": err or f"only {0 if hist is None else len(hist)} daily bars"}
            status = f"failed ({charts[sym]['error'][:50]})"
        else:
            try:
                charts[sym] = chart_metrics(hist, bench)
                histories[sym] = hist
                status = "ok"
            except Exception as e:  # noqa: BLE001
                charts[sym] = {"error": f"compute: {type(e).__name__}: {str(e)[:80]}"}
                status = "failed (compute)"
        if i % 10 == 0 or i == n or status != "ok":
            print(f"  {i}/{n} {sym}: {status}")
    ok = sum(1 for c in charts.values() if not c.get("error"))
    if n:
        print(f"Price history: {ok}/{n} ok")
    return charts, histories

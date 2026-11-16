"""
Stand-ins for Yahoo Finance so the whole pipeline can be tested with no network.

The fake tables have the same shape yfinance returns: statement rows named
in CamelCase, one column per fiscal year end with the newest first, and
timezone-aware daily price history.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

END = pd.Timestamp("2026-11-13")  # a Friday
FY = [pd.Timestamp(f"{y}-12-31") for y in (2025, 2024, 2023, 2022)]  # newest first, like Yahoo


def price_path(kind: str, seed: int = 1, bars: int = 756, start: float = 60.0) -> pd.DataFrame:
    """Daily OHLC for a named shape: 'established', 'emerging', 'downtrend' or 'index'."""
    rng = np.random.default_rng(seed)
    if kind == "established":
        drift = np.full(bars, 0.0016)
    elif kind == "emerging":      # flat, then a year-long slide, then a sharp 8-week recovery
        drift = np.concatenate([np.zeros(bars - 256), np.full(216, -0.002), np.full(40, 0.011)])
    elif kind == "downtrend":
        drift = np.full(bars, -0.0012)
    elif kind == "index":
        drift = np.full(bars, 0.0004)
    else:
        raise ValueError(kind)
    close = start * np.exp(np.cumsum(drift + rng.normal(0, 0.004, bars)))
    idx = pd.bdate_range(end=END, periods=bars, tz="America/New_York")
    spread = close * 0.008
    return pd.DataFrame({
        "Open": close - spread / 4, "High": close + spread, "Low": close - spread, "Close": close,
        "Volume": rng.integers(200_000, 900_000, bars), "Dividends": 0.0, "Stock Splits": 0.0,
    }, index=idx)


def company(revenue, op_margin, shares=50e6, debt=0.0, equity_ratio=0.5, fcf_ratio=0.8, tax=0.21, share_growth=0.0):
    """
    revenue and op_margin: 4 values, OLDEST first. Returns the three statement tables.
    Net income = operating income after tax; free cash flow = fcf_ratio x net income.
    """
    rev = np.array(revenue, dtype=float)[::-1]          # newest first
    op = rev * np.array(op_margin, dtype=float)[::-1]
    ni = op * (1 - tax)
    fcf = ni * fcf_ratio
    capex = -0.3 * np.abs(ni)
    sh = (shares * (1 + share_growth) ** np.arange(4))[::-1]      # newest first
    income = pd.DataFrame(
        [rev, op, ni, np.full(4, tax), sh],
        index=["TotalRevenue", "OperatingIncome", "NetIncome", "TaxRateForCalcs", "DilutedAverageShares"], columns=FY)
    balance = pd.DataFrame(
        [rev * equity_ratio, np.full(4, float(debt)), np.zeros(4), rev * 1.2, sh],
        index=["StockholdersEquity", "TotalDebt", "CapitalLeaseObligations", "TotalAssets", "OrdinarySharesNumber"], columns=FY)
    cash = pd.DataFrame(
        [fcf - capex, capex, fcf], index=["OperatingCashFlow", "CapitalExpenditure", "FreeCashFlow"], columns=FY)
    return {"income": income, "balance": balance, "cash": cash}


def quarterly(latest: float, year_ago: float) -> pd.DataFrame:
    cols = [pd.Timestamp("2026-09-30"), pd.Timestamp("2026-06-30"), pd.Timestamp("2026-03-31"),
            pd.Timestamp("2025-12-31"), pd.Timestamp("2025-09-30")]
    mid = (latest + year_ago) / 2
    return pd.DataFrame([[latest, mid, mid, mid, year_ago]], index=["TotalRevenue"], columns=cols)


class RateLimited(Exception):
    """Carries the text yfinance uses, so the screener recognises it."""
    def __init__(self):
        super().__init__("Too Many Requests. Rate limited. Try after a while.")


class FakeYahoo:
    """Use as: patch('yfinance.Ticker', fake.Ticker)."""

    def __init__(self, companies=None, prices=None, quarters=None, info=None):
        self.companies = companies or {}
        self.prices = prices or {}
        self.quarters = quarters or {}
        self.info = info or {}
        self.calls: list[tuple[str, str]] = []
        self.fail_statements: dict[str, int] = {}     # ticker -> how many more calls should fail
        self.rate_limit_statements: dict[str, int] = {}

    def Ticker(self, symbol):  # noqa: N802 - mirrors yfinance
        return _FakeTicker(self, symbol)


class _FakeTicker:
    def __init__(self, world: FakeYahoo, symbol: str):
        self.w, self.s = world, symbol

    def _statement(self, name, freq):
        self.w.calls.append((self.s, f"{name}:{freq}"))
        if self.w.rate_limit_statements.get(self.s, 0) > 0:
            self.w.rate_limit_statements[self.s] -= 1
            raise RateLimited()
        if self.w.fail_statements.get(self.s, 0) > 0:
            if name == "income":
                self.w.fail_statements[self.s] -= 1
            return pd.DataFrame()
        if freq == "quarterly":
            return self.w.quarters.get(self.s, pd.DataFrame())
        return self.w.companies.get(self.s, {}).get(name, pd.DataFrame())

    def get_income_stmt(self, freq="yearly", **_):
        return self._statement("income", freq)

    def get_balance_sheet(self, freq="yearly", **_):
        return self._statement("balance", freq)

    def get_cash_flow(self, freq="yearly", **_):
        return self._statement("cash", freq)

    def history(self, **_):
        self.w.calls.append((self.s, "history"))
        return self.w.prices.get(self.s, pd.DataFrame())

    @property
    def info(self):
        self.w.calls.append((self.s, "info"))
        return self.w.info.get(self.s, {})

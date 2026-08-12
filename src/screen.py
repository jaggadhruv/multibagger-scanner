"""
Hard filters — the first pass that eliminates unlikely multibagger candidates.

Philosophy: filters are NOT for ranking. They're for excluding companies that
can't plausibly be multibaggers (too big to 10x, too indebted to survive, etc.).
Ranking happens in score.py on whatever passes.

Note on yfinance quirks:
- debt_to_equity comes as a percentage-style number (e.g. 55 means 0.55).
  We normalise to a ratio.
- Missing values are common. Our default is to EXCLUDE on missing critical fields
  (conservative). You can flip this via `strict=False`.
"""
from __future__ import annotations
import pandas as pd

# Default filter thresholds — the "multibagger hunting ground" definition.
# Tune these in main.py or a YAML config later.
DEFAULT_FILTERS = {
    # Growth runway — the "small enough to 5-10x, big enough to be consolidated"
    "market_cap_min": 2_000_000_000,     # $2B — stronger, more established candidates
    "market_cap_max": 10_000_000_000,    # $10B — above this, 10x is very rare

    # Quality
    "roe_min": 0.12,                     # 12% ROE
    "operating_margin_min": 0.08,        # 8% op margin
    "gross_margin_min": 0.20,            # 20% gross margin (proxy for pricing power)

    # Growth
    "revenue_growth_min": 0.10,          # 10% YoY revenue growth

    # Financial health
    "debt_to_equity_max": 1.5,           # ratio (already normalised)
    "current_ratio_min": 1.2,
    "positive_fcf_required": True,
}


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Handle yfinance quirks: debt_to_equity comes as % not ratio."""
    df = df.copy()
    if "debt_to_equity" in df.columns:
        # If any value is > 10, treat the column as percentage-style and divide by 100.
        # (yfinance returns e.g. 55.2 meaning 0.552.)
        max_de = df["debt_to_equity"].dropna().abs().max()
        if pd.notna(max_de) and max_de > 10:
            df["debt_to_equity"] = df["debt_to_equity"] / 100.0
    return df


def apply_filters(
    df: pd.DataFrame,
    cfg: dict | None = None,
    strict: bool = True,
) -> pd.DataFrame:
    """
    Apply hard filters to the fetched fundamentals.

    Parameters
    ----------
    df : DataFrame from fetch_fundamentals()
    cfg : dict of thresholds. Falls back to DEFAULT_FILTERS.
    strict : if True, missing values fail the filter (safer). If False, missing
             values pass (more permissive — you'll see more candidates but with
             holes in the data).

    Returns filtered DataFrame with a `passed_filters` boolean column.
    """
    cfg = {**DEFAULT_FILTERS, **(cfg or {})}
    df = _normalise(df)

    n_start = len(df)
    print(f"\nApplying filters to {n_start} rows...")

    # Fail-value: what to substitute NaN with. In strict mode we use a value that
    # guarantees failure of each check.
    def _fill(col: str, fail_value):
        return df[col].fillna(fail_value if strict else _pass_value(col, cfg))

    def _pass_value(col: str, cfg: dict):
        # For permissive mode, substitute a value that passes each check
        passers = {
            "market_cap": (cfg["market_cap_min"] + cfg["market_cap_max"]) / 2,
            "roe": cfg["roe_min"] + 0.01,
            "operating_margin": cfg["operating_margin_min"] + 0.01,
            "gross_margin": cfg["gross_margin_min"] + 0.01,
            "revenue_growth": cfg["revenue_growth_min"] + 0.01,
            "debt_to_equity": cfg["debt_to_equity_max"] - 0.1,
            "current_ratio": cfg["current_ratio_min"] + 0.1,
            "free_cash_flow": 1.0,
        }
        return passers.get(col, 0)

    checks = []
    if "market_cap" in df.columns:
        c = _fill("market_cap", 0).between(cfg["market_cap_min"], cfg["market_cap_max"])
        checks.append(("market_cap", c))
    if "roe" in df.columns:
        c = _fill("roe", -1) >= cfg["roe_min"]
        checks.append(("roe", c))
    if "operating_margin" in df.columns:
        c = _fill("operating_margin", -1) >= cfg["operating_margin_min"]
        checks.append(("operating_margin", c))
    if "gross_margin" in df.columns:
        c = _fill("gross_margin", -1) >= cfg["gross_margin_min"]
        checks.append(("gross_margin", c))
    if "revenue_growth" in df.columns:
        c = _fill("revenue_growth", -1) >= cfg["revenue_growth_min"]
        checks.append(("revenue_growth", c))
    if "debt_to_equity" in df.columns:
        c = _fill("debt_to_equity", 999) <= cfg["debt_to_equity_max"]
        checks.append(("debt_to_equity", c))
    if "current_ratio" in df.columns:
        c = _fill("current_ratio", 0) >= cfg["current_ratio_min"]
        checks.append(("current_ratio", c))
    if cfg["positive_fcf_required"] and "free_cash_flow" in df.columns:
        c = _fill("free_cash_flow", -1) > 0
        checks.append(("positive_fcf", c))

    # Report per-filter pass rates
    print("Per-filter pass rate:")
    combined = pd.Series(True, index=df.index)
    for name, check in checks:
        print(f"  {name:22s}: {check.sum():4d}/{n_start} pass ({check.mean()*100:.1f}%)")
        combined &= check

    passed = df[combined].copy()
    passed["passed_filters"] = True
    print(f"\nAll filters combined: {len(passed)}/{n_start} pass ({len(passed)/n_start*100:.1f}%)")
    return passed.reset_index(drop=True)

"""
Composite factor scoring.

We compute a z-score within each factor family (Quality, Growth, Health,
Valuation, Momentum) then weight-average them into a `composite_score`.

Higher composite_score = more attractive multibagger candidate on our factor
model. This is NOT a probability of becoming a multibagger — it's a ranking
tool for further research.

Robust z-score: we use median + MAD (Median Absolute Deviation) instead of
mean + std because financial ratios have fat tails / outliers.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

# Factor family weights. Should sum to 1.0.
# Rationale:
#   quality + growth get the most weight — these are the strongest multibagger predictors historically
#   health is a survivability filter (some already handled by hard filters)
#   valuation gets less weight — great multibaggers rarely screen "cheap" on P/E
#   momentum is a mild tailwind signal, not a driver
DEFAULT_WEIGHTS = {
    "quality": 0.30,
    "growth": 0.30,
    "health": 0.15,
    "valuation": 0.15,
    "momentum": 0.10,
}


def _robust_z(series: pd.Series) -> pd.Series:
    """Robust z-score using median and MAD. Handles NaN by filling with 0 (neutral)."""
    s = pd.to_numeric(series, errors="coerce")
    med = s.median()
    mad = (s - med).abs().median()
    if pd.isna(mad) or mad == 0:
        return pd.Series(0.0, index=series.index)
    # 1.4826 makes MAD a consistent estimator of std for normal data
    z = (s - med) / (1.4826 * mad)
    # Winsorise extreme values
    return z.clip(-3, 3).fillna(0)


def _safe_mean(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    """Row-wise mean of z-scores, ignoring cols not present."""
    present = [c for c in cols if c in df.columns]
    if not present:
        return pd.Series(0.0, index=df.index)
    z = df[present].apply(_robust_z)
    return z.mean(axis=1)


def compute_scores(df: pd.DataFrame, weights: dict | None = None) -> pd.DataFrame:
    """
    Compute composite score.

    Returns df with new columns:
      quality_score, growth_score, health_score, valuation_score, momentum_score,
      composite_score
    Sorted descending by composite_score.
    """
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    df = df.copy()

    # QUALITY: profitability + margin strength
    df["quality_score"] = _safe_mean(df, [
        "roe", "roa", "operating_margin", "gross_margin", "profit_margin", "ebitda_margin"
    ])

    # GROWTH: top-line and bottom-line momentum
    df["growth_score"] = _safe_mean(df, [
        "revenue_growth", "earnings_growth",
        "quarterly_revenue_growth", "quarterly_earnings_growth"
    ])

    # HEALTH: balance sheet strength (invert debt — less is better)
    debt_z = -_robust_z(df["debt_to_equity"]) if "debt_to_equity" in df.columns else pd.Series(0.0, index=df.index)
    current_z = _robust_z(df["current_ratio"]) if "current_ratio" in df.columns else pd.Series(0.0, index=df.index)
    quick_z = _robust_z(df["quick_ratio"]) if "quick_ratio" in df.columns else pd.Series(0.0, index=df.index)
    df["health_score"] = (debt_z + current_z + quick_z) / 3

    # VALUATION: lower multiples are better (invert)
    val_metrics = ["pe_ratio", "forward_pe", "peg_ratio", "ev_to_ebitda", "price_to_sales"]
    present_val = [c for c in val_metrics if c in df.columns]
    if present_val:
        # Only score companies with positive multiples (negative PE etc. are noise)
        val_z = pd.DataFrame(index=df.index)
        for col in present_val:
            s = pd.to_numeric(df[col], errors="coerce")
            s = s.where(s > 0)  # ignore negatives
            val_z[col] = -_robust_z(s)  # invert: lower is better
        df["valuation_score"] = val_z.mean(axis=1).fillna(0)
    else:
        df["valuation_score"] = 0.0

    # MOMENTUM: proximity to 52-week high
    if "price" in df.columns and "week52_high" in df.columns and "week52_low" in df.columns:
        rng = df["week52_high"] - df["week52_low"]
        proximity = (df["price"] - df["week52_low"]) / rng.replace(0, np.nan)
        df["momentum_score"] = _robust_z(proximity)
    else:
        df["momentum_score"] = 0.0

    # COMPOSITE
    df["composite_score"] = (
        weights["quality"]   * df["quality_score"] +
        weights["growth"]    * df["growth_score"] +
        weights["health"]    * df["health_score"] +
        weights["valuation"] * df["valuation_score"] +
        weights["momentum"]  * df["momentum_score"]
    )

    return df.sort_values("composite_score", ascending=False).reset_index(drop=True)

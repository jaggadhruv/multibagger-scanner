"""
Composite factor scoring + Multibagger Score (0-10) + human-readable rationale.

Design:
  1. Compute z-scores per factor family (Quality, Growth, Health, Valuation, Momentum)
     using robust statistics (median + MAD) to handle fat-tailed financial ratios.
  2. Weight them into a composite score. Weights emphasise financial strength
     (Quality + Health = 55%), then growth (25%), valuation (15%), momentum (5%).
  3. Convert composite to a Multibagger Score on a 0-10 scale using percentile rank
     within the filtered pool. All shown candidates already passed hard filters,
     so worst-passed = 5.0 (respectable baseline), best-passed = 10.0.
  4. Generate a short rationale string per company: top 2 factors as strengths,
     lowest factor as concern, with actual metric values inline.

The Multibagger Score is a RANKING tool — it does NOT estimate probability of
becoming a multibagger. Even a perfect 10 candidate is a research starting point,
not a buy signal.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

# Factor family weights. Emphasise financial strength (Quality + Health).
DEFAULT_WEIGHTS = {
    "quality":   0.30,   # Profitability, capital efficiency
    "growth":    0.25,   # Revenue and earnings growth
    "health":    0.25,   # Balance sheet, liquidity, FCF  ← boosted
    "valuation": 0.15,   # Multiples
    "momentum":  0.05,   # 52-week high proximity  ← reduced
}

FACTOR_KEYS = ["quality", "growth", "health", "valuation", "momentum"]


# ------------------------------------------------------------------ #
# Robust statistics
# ------------------------------------------------------------------ #

def _robust_z(series: pd.Series) -> pd.Series:
    """Robust z-score using median + MAD. Winsorised to [-3, 3]. NaN → 0."""
    s = pd.to_numeric(series, errors="coerce")
    med = s.median()
    mad = (s - med).abs().median()
    if pd.isna(mad) or mad == 0:
        return pd.Series(0.0, index=series.index)
    z = (s - med) / (1.4826 * mad)
    return z.clip(-3, 3).fillna(0)


def _safe_mean(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    present = [c for c in cols if c in df.columns]
    if not present:
        return pd.Series(0.0, index=df.index)
    z = df[present].apply(_robust_z)
    return z.mean(axis=1)


# ------------------------------------------------------------------ #
# 0-10 Multibagger Score
# ------------------------------------------------------------------ #

def _to_multibagger_10(composite: pd.Series) -> pd.Series:
    """
    Convert composite z-score to a 0-10 scale via percentile rank.

    All candidates in the input already passed hard filters, so we anchor:
      worst-passed → 5.0  (respectable baseline)
      best-passed  → 10.0
    A rank-percentile mapping gives smooth differentiation between them.
    """
    n = len(composite)
    if n == 0:
        return composite
    if n == 1:
        return pd.Series([10.0], index=composite.index)
    ranks = composite.rank(ascending=True, method="min")
    percentile = (ranks - 1) / (n - 1)          # 0 (worst) → 1 (best)
    score = 5.0 + 5.0 * percentile              # 5.0 → 10.0
    return score.round(1)


# ------------------------------------------------------------------ #
# Rationale generation
# ------------------------------------------------------------------ #

def _pct(x, sign=False):
    if pd.isna(x): return None
    fmt = f"{x*100:+.0f}%" if sign else f"{x*100:.0f}%"
    return fmt


def _describe_strength(key: str, row: pd.Series) -> str:
    """Short phrase describing why this factor is a strength for this company."""
    if key == "quality":
        bits = []
        if (v := _pct(row.get("roe"))) is not None:                bits.append(f"ROE {v}")
        if (v := _pct(row.get("operating_margin"))) is not None:   bits.append(f"op margin {v}")
        if not bits and (v := _pct(row.get("gross_margin"))) is not None:
            bits.append(f"gross margin {v}")
        detail = f" ({', '.join(bits)})" if bits else ""
        return f"strong profitability{detail}"

    if key == "growth":
        bits = []
        if (v := _pct(row.get("revenue_growth"), sign=True)) is not None:   bits.append(f"revenue {v}")
        if (v := _pct(row.get("earnings_growth"), sign=True)) is not None:  bits.append(f"EPS {v}")
        detail = f" ({', '.join(bits)})" if bits else ""
        return f"solid growth{detail}"

    if key == "health":
        bits = []
        de = row.get("debt_to_equity")
        cr = row.get("current_ratio")
        if pd.notna(de): bits.append(f"D/E {de:.2f}")
        if pd.notna(cr): bits.append(f"CR {cr:.1f}")
        detail = f" ({', '.join(bits)})" if bits else ""
        return f"healthy balance sheet{detail}"

    if key == "valuation":
        bits = []
        pe = row.get("pe_ratio")
        peg = row.get("peg_ratio")
        if pd.notna(pe) and pe > 0:   bits.append(f"P/E {pe:.0f}")
        if pd.notna(peg) and peg > 0: bits.append(f"PEG {peg:.1f}")
        detail = f" ({', '.join(bits)})" if bits else ""
        return f"reasonable valuation{detail}"

    if key == "momentum":
        return "constructive price momentum"

    return f"strong {key}"


def _describe_weakness(key: str, row: pd.Series) -> str:
    """Short phrase describing what to watch out for."""
    if key == "quality":   return "profitability is the softer spot"
    if key == "growth":    return "growth is modest — more of a compounder"
    if key == "health":    return "balance sheet worth monitoring"
    if key == "valuation": return "valuation looks stretched"
    if key == "momentum":  return "recent price action is soft"
    return f"{key} lags peers"


def _cap_first(s: str) -> str:
    """Capitalize first letter only — preserves inline acronyms like ROE, P/E, EPS, D/E."""
    return s[:1].upper() + s[1:] if s else s


def _generate_rationale(row: pd.Series) -> str:
    """
    Build a short rationale for one company based on its factor sub-scores.

    Structure: '<Top strength>; <second strength>. <Weakest factor concern>.'
    """
    scores = {k: row.get(f"{k}_score", 0) for k in FACTOR_KEYS}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top1_key = ranked[0][0]
    top2_key = ranked[1][0]
    weak_key = ranked[-1][0]

    s1 = _cap_first(_describe_strength(top1_key, row))
    s2 = _describe_strength(top2_key, row)
    w  = _cap_first(_describe_weakness(weak_key, row))
    return f"{s1}; {s2}. {w}."


# ------------------------------------------------------------------ #
# Main scoring pipeline
# ------------------------------------------------------------------ #

def compute_scores(df: pd.DataFrame, weights: dict | None = None) -> pd.DataFrame:
    """
    Add factor sub-scores, composite score, 0-10 Multibagger Score, and rationale.

    Returns df sorted descending by multibagger_score.
    New columns:
      quality_score, growth_score, health_score, valuation_score, momentum_score,
      composite_score, multibagger_score (0-10), rationale
    """
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    df = df.copy()

    # --- QUALITY: profitability + margins ---
    df["quality_score"] = _safe_mean(df, [
        "roe", "roa", "operating_margin", "gross_margin",
        "profit_margin", "ebitda_margin",
    ])

    # --- GROWTH: top-line + bottom-line, YoY + quarterly ---
    df["growth_score"] = _safe_mean(df, [
        "revenue_growth", "earnings_growth",
        "quarterly_revenue_growth", "quarterly_earnings_growth",
    ])

    # --- HEALTH: balance sheet + liquidity + FCF quality ---
    # For debt, LOWER is better → negate the z-score
    debt_z = -_robust_z(df["debt_to_equity"]) if "debt_to_equity" in df.columns else pd.Series(0.0, index=df.index)
    cr_z   = _robust_z(df["current_ratio"])    if "current_ratio"  in df.columns else pd.Series(0.0, index=df.index)
    qr_z   = _robust_z(df["quick_ratio"])      if "quick_ratio"    in df.columns else pd.Series(0.0, index=df.index)
    fcf_z  = _robust_z(df["free_cash_flow"])   if "free_cash_flow" in df.columns else pd.Series(0.0, index=df.index)
    df["health_score"] = (debt_z + cr_z + qr_z + fcf_z) / 4

    # --- VALUATION: LOWER multiples are better → negate ---
    val_metrics = ["pe_ratio", "forward_pe", "peg_ratio", "ev_to_ebitda", "price_to_sales"]
    present_val = [c for c in val_metrics if c in df.columns]
    if present_val:
        val_z = pd.DataFrame(index=df.index)
        for col in present_val:
            s = pd.to_numeric(df[col], errors="coerce")
            s = s.where(s > 0)  # ignore negative multiples (noise)
            val_z[col] = -_robust_z(s)
        df["valuation_score"] = val_z.mean(axis=1).fillna(0)
    else:
        df["valuation_score"] = 0.0

    # --- MOMENTUM: proximity to 52w high ---
    if all(c in df.columns for c in ["price", "week52_high", "week52_low"]):
        rng = df["week52_high"] - df["week52_low"]
        prox = (df["price"] - df["week52_low"]) / rng.replace(0, np.nan)
        df["momentum_score"] = _robust_z(prox)
    else:
        df["momentum_score"] = 0.0

    # --- COMPOSITE (weighted z-score aggregate) ---
    df["composite_score"] = (
        weights["quality"]   * df["quality_score"]   +
        weights["growth"]    * df["growth_score"]    +
        weights["health"]    * df["health_score"]    +
        weights["valuation"] * df["valuation_score"] +
        weights["momentum"]  * df["momentum_score"]
    )

    # --- MULTIBAGGER SCORE (0-10) ---
    df["multibagger_score"] = _to_multibagger_10(df["composite_score"])

    # Sort by multibagger score descending
    df = df.sort_values("multibagger_score", ascending=False).reset_index(drop=True)

    # --- RATIONALE (row-by-row, uses sub-scores already computed) ---
    df["rationale"] = df.apply(_generate_rationale, axis=1)

    return df

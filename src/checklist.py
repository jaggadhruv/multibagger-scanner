"""
The 12-point checklist and the three gates.

Every item is an absolute yes/no test against a fixed threshold in config.py.
Nothing here ranks one stock against another, so the number of stocks that
qualify can be zero in a weak week and that is the intended behaviour.

    Gate 1  Proven business     items 1-7   multi-year annual numbers
    Gate 2  Early / inflecting  items 8-9   margins and growth moving the right way
    Gate 3  Chart strength      items 10-12 trend and performance against the index

An item whose data is missing counts as NOT met and shows as "no data" in the
report. A stock must not reach the short list on the strength of numbers we
do not have.

Tiers
    A  Actionable   all three gates pass
    B  Watchlist    gates 1 and 2 pass, chart not ready
    C  Bench        gate 1 passes, gate 2 does not (proven, not inflecting)
    -  everything else (kept in the CSV only)

All percentages are plain numbers: 15.0 means 15%.
"""
from __future__ import annotations
import math

GATE_NAMES = {1: "Proven business", 2: "Early / inflecting", 3: "Chart strength"}


# ------------------------------------------------------------------ #
# Small helpers
# ------------------------------------------------------------------ #

def num(x):
    """Float, or None for missing / NaN / non-numeric."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def tail(values, n):
    """Last n values as cleaned floats (shorter if fewer are available)."""
    vals = [num(v) for v in list(values or [])]
    return vals[-n:]


def cagr_pct(first, last, years):
    """Compound annual growth in %, None unless both ends are positive."""
    first, last = num(first), num(last)
    if first is None or last is None or first <= 0 or last <= 0 or years <= 0:
        return None
    return ((last / first) ** (1.0 / years) - 1.0) * 100.0


def _p(x, signed=False, digits=0):
    x = num(x)
    if x is None:
        return "n/a"
    return f"{x:+.{digits}f}%" if signed else f"{x:.{digits}f}%"


def _item(n, gate, label, ok, detail):
    return {"n": n, "gate": gate, "label": label, "ok": ok, "detail": detail}


# ------------------------------------------------------------------ #
# Multi-year metrics (same maths for both markets)
# ------------------------------------------------------------------ #

def business_metrics(rec: dict) -> dict:
    """
    rec carries annual series, oldest first, aligned to the same fiscal years:
        revenue, op_income, net_income, cash_flow, roc (return on capital, %)
    Only the last 4 years are used: that is what both data sources reliably give.
    """
    rev = tail(rec.get("revenue"), 4)
    op = tail(rec.get("op_income"), 4)
    ni = tail(rec.get("net_income"), 4)
    cf = tail(rec.get("cash_flow"), 4)
    roc = [v for v in tail(rec.get("roc"), 4) if v is not None][-3:]

    m = {
        "rev_years": sum(v is not None for v in rev),
        "rev_cagr_3y": None,
        "rev_yoy": [],            # oldest -> newest, up to 3 steps
        "rev_growth_latest": None,
        "roc_avg_3y": None,
        "cash_known_years": 0,
        "cash_positive_years": None,
        "cash_conversion_3y": None,
        "margin_latest": None,
        "margin_prev": None,
        "margin_3y_ago": None,
        "margin_change_pp": None,
    }

    full4 = len(rev) == 4 and all(v is not None and v > 0 for v in rev)
    if full4:
        m["rev_cagr_3y"] = cagr_pct(rev[0], rev[3], 3)
        m["rev_yoy"] = [(rev[i] / rev[i - 1] - 1.0) * 100.0 for i in (1, 2, 3)]
        m["rev_growth_latest"] = m["rev_yoy"][-1]

    if len(roc) == 3:
        m["roc_avg_3y"] = sum(roc) / 3.0

    known = [v for v in cf if v is not None]
    m["cash_known_years"] = len(known)
    if known:
        m["cash_positive_years"] = sum(1 for v in known if v > 0)

    # Latest 3 years in which both cash flow and profit are known
    pairs = [(c, n) for c, n in zip(cf, ni) if c is not None and n is not None][-3:]
    if len(cf) == len(ni) and len(pairs) == 3:
        total_ni = sum(n for _, n in pairs)
        m["cash_conversion_3y"] = (sum(c for c, _ in pairs) / total_ni * 100.0) if total_ni > 0 else -1.0

    def margin(i):
        try:
            r, o = rev[i], op[i]
        except IndexError:
            return None
        if r is None or o is None or r <= 0:
            return None
        return o / r * 100.0

    if len(rev) == 4 and len(op) == 4:
        m["margin_latest"] = margin(3)
        m["margin_prev"] = margin(2)
        m["margin_3y_ago"] = margin(0)
        if m["margin_latest"] is not None and m["margin_3y_ago"] is not None:
            m["margin_change_pp"] = m["margin_latest"] - m["margin_3y_ago"]
    return m


# ------------------------------------------------------------------ #
# Gates 1 and 2 (fundamentals)
# ------------------------------------------------------------------ #

def fundamental_items(rec: dict, cfg) -> tuple[list, dict]:
    """Returns (items 1-9, metrics). Items 6 and 7 are market specific and
    arrive pre-evaluated in rec['extra'] (see fundamentals.py)."""
    m = business_metrics(rec)
    items = []

    # 1. Growth rate
    v = m["rev_cagr_3y"]
    if v is None:
        items.append(_item(1, 1, f"Revenue growth {cfg.REV_CAGR_MIN:.0f}%+ a year", None,
                           f"needs 4 years of revenue, have {m['rev_years']}"))
    else:
        items.append(_item(1, 1, f"Revenue growth {cfg.REV_CAGR_MIN:.0f}%+ a year", v >= cfg.REV_CAGR_MIN,
                           f"{_p(v)} a year over 3 years"))

    # 2. Consistency
    yoy = m["rev_yoy"]
    if len(yoy) < 3:
        items.append(_item(2, 1, "Revenue up every year", None, "needs 4 years of revenue"))
    else:
        items.append(_item(2, 1, "Revenue up every year", all(g > 0 for g in yoy),
                           ", ".join(_p(g, signed=True) for g in yoy)))

    # 3. Return on capital
    v = m["roc_avg_3y"]
    label = f"{cfg.ROC_LABEL} {cfg.ROC_MIN:.0f}%+"
    if v is None:
        items.append(_item(3, 1, label, None, "needs 3 years of capital data"))
    else:
        items.append(_item(3, 1, label, v >= cfg.ROC_MIN, f"{_p(v)} average over 3 years"))

    # 4. Cash flow positive most years
    pos, n = m["cash_positive_years"], m["cash_known_years"]
    label = f"{cfg.CASH_LABEL} positive most years"
    if pos is None or n < 3:
        items.append(_item(4, 1, label, None, f"needs 3 years of cash flow, have {n}"))
    else:
        need = cfg.CASH_POSITIVE_YEARS_MIN if n >= 4 else n
        items.append(_item(4, 1, label, pos >= need, f"positive in {pos} of {n} years"))

    # 5. Profit backed by cash
    v = m["cash_conversion_3y"]
    label = f"Profit backed by cash ({cfg.CASH_CONVERSION_MIN:.0f}%+)"
    if v is None:
        items.append(_item(5, 1, label, None, "needs 3 years of cash flow and profit"))
    elif v < 0:
        items.append(_item(5, 1, label, False, "no net profit over 3 years"))
    else:
        items.append(_item(5, 1, label, v >= cfg.CASH_CONVERSION_MIN,
                           f"{cfg.CASH_LABEL.lower()} was {_p(v)} of net profit over 3 years"))

    # 6-7. Market specific: debt, and dilution (US) or promoter alignment (India)
    extra = list(rec.get("extra") or [])
    for n_item, slot in ((6, 0), (7, 1)):
        if slot < len(extra):
            e = extra[slot]
            items.append(_item(n_item, 1, e["label"], e["ok"], e["detail"]))
        else:
            items.append(_item(n_item, 1, "Balance sheet check", None, "no data"))

    # 8. Margin expanding
    now, prev, then = m["margin_latest"], m["margin_prev"], m["margin_3y_ago"]
    label = "Operating margin expanding"
    if now is None or then is None:
        items.append(_item(8, 2, label, None, "needs 4 years of operating profit"))
    else:
        ok = (now - then) >= cfg.MARGIN_EXPANSION_MIN_PP and now > 0
        detail = f"{_p(now, digits=1)} now against {_p(then, digits=1)} three years ago"
        if ok and prev is not None and (prev - now) > cfg.MARGIN_RECENT_DIP_MAX_PP:
            ok = False
            detail += f", but down from {_p(prev, digits=1)} last year"
        items.append(_item(8, 2, label, ok, detail))

    # 9. Growth holding up
    latest, pace, q = m["rev_growth_latest"], m["rev_cagr_3y"], num(rec.get("recent_q_growth"))
    label = "Growth holding up"
    if latest is None or pace is None:
        items.append(_item(9, 2, label, None, "needs 4 years of revenue"))
    else:
        ok = latest >= cfg.GROWTH_NOW_MIN and latest >= cfg.GROWTH_FADE_RATIO * pace
        detail = f"latest year {_p(latest, signed=True)} against a 3-year pace of {_p(pace)}"
        if q is not None:
            detail += f"; latest quarter {_p(q, signed=True)}"
            if q < cfg.RECENT_QUARTER_GROWTH_MIN:
                ok = False
        items.append(_item(9, 2, label, ok, detail))

    return items, m


# ------------------------------------------------------------------ #
# Gate 3 (chart)
# ------------------------------------------------------------------ #

def chart_items(chart: dict | None, cfg) -> tuple[list, dict]:
    """
    Two routes through the chart gate, chosen by how old the weekly uptrend is:

      Emerging     weekly Supertrend turned to BUY within FRESH_FLIP_WEEKS.
                   Needs: price above the 200-day average, beating the index
                   over 3 months.
      Established  weekly Supertrend on BUY for longer.
                   Needs: price above a RISING 200-day average, within
                   NEAR_HIGH_MAX_PCT of the 52-week high, beating the index
                   over both 6 and 12 months.
    """
    info = {"stage": "No chart data", "route": None, "flags": []}
    labels = ("Weekly Supertrend on BUY", "Price above 200-day average", f"Beating the {cfg.BENCHMARK_NAME}")
    if not chart or chart.get("error"):
        why = (chart or {}).get("error") or "no price data"
        return [_item(10 + i, 3, labels[i], None, why) for i in range(3)], info

    sig, weeks = chart.get("weekly_signal"), num(chart.get("weekly_bars"))
    buy = sig == "BUY"
    fresh = buy and weeks is not None and weeks <= cfg.FRESH_FLIP_WEEKS
    route = "emerging" if fresh else "established"
    info["route"] = route if buy else None
    items = []

    # 10. Weekly Supertrend
    if sig is None:
        items.append(_item(10, 3, labels[0], None, "not enough weekly bars"))
    else:
        w = f"{int(weeks)} week{'s' if weeks != 1 else ''}" if weeks is not None else "n/a"
        items.append(_item(10, 3, labels[0], buy, f"{sig} for {w}"))

    # 11. Position against the 200-day average (and the 52-week high)
    above, rising, off_high = num(chart.get("pct_above_200")), chart.get("ma200_rising"), num(chart.get("pct_from_high"))
    if above is None:
        items.append(_item(11, 3, labels[1], None, "fewer than 200 daily bars"))
    else:
        slope = "rising" if rising else "falling"
        detail = f"{_p(above, signed=True)} against the 200-day average ({slope})"
        if off_high is not None:
            detail += f", {_p(off_high)} below the 52-week high"
        if route == "emerging":
            ok = above > 0
        else:
            ok = above > 0 and bool(rising) and off_high is not None and off_high <= cfg.NEAR_HIGH_MAX_PCT
        items.append(_item(11, 3, labels[1], ok, detail))

    # 12. Relative strength
    r3, r6, r12 = num(chart.get("rs_3m")), num(chart.get("rs_6m")), num(chart.get("rs_12m"))
    basis = "" if chart.get("benchmark_ok") else " (index data missing, plain returns used)"
    detail = f"3m {_p(r3, signed=True)}, 6m {_p(r6, signed=True)}, 12m {_p(r12, signed=True)}{basis}"
    if route == "emerging":
        ok = None if r3 is None else r3 > 0
    else:
        ok = None if (r6 is None or r12 is None) else (r6 > 0 and r12 > 0)
    items.append(_item(12, 3, labels[2], ok, detail))

    # Stage label and caution flags (flags never change the tier)
    if buy:
        info["stage"] = f"Emerging ({int(weeks)}w)" if fresh else f"Established ({int(weeks)}w)"
    elif sig == "SELL":
        info["stage"] = "No uptrend"
    rsi = num(chart.get("rsi"))
    if above is not None and above > cfg.EXTENDED_ABOVE_200_PCT:
        info["flags"].append(f"Extended: {above:.0f}% above the 200-day average")
    if rsi is not None and rsi > cfg.RSI_HOT:
        info["flags"].append(f"Daily RSI {rsi:.0f}")
    if buy and chart.get("daily_signal") == "SELL":
        info["flags"].append("Daily Supertrend on SELL (short-term pullback)")
    return items, info


# ------------------------------------------------------------------ #
# Putting it together
# ------------------------------------------------------------------ #

def gate_passes(items: list, gate: int) -> bool:
    mine = [i for i in items if i["gate"] == gate]
    return bool(mine) and all(i["ok"] is True for i in mine)


def classify(items: list) -> dict:
    g1, g2, g3 = (gate_passes(items, g) for g in (1, 2, 3))
    if g1 and g2 and g3:
        tier = "A"
    elif g1 and g2:
        tier = "B"
    elif g1:
        tier = "C"
    else:
        tier = "-"
    return {
        "tier": tier,
        "gate1": g1, "gate2": g2, "gate3": g3,
        "score": sum(1 for i in items if i["ok"] is True),
        "missing": [i["label"] for i in items if i["ok"] is not True],
    }


def sort_key(row: dict):
    """Within a tier: more boxes ticked first, then growth plus return on capital."""
    m = row.get("metrics") or {}
    strength = (num(m.get("rev_cagr_3y")) or 0.0) + (num(m.get("roc_avg_3y")) or 0.0)
    return (-row.get("score", 0), -strength, row.get("ticker", ""))

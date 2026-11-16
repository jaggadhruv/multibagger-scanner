"""
Ticker universe for the US screener.

Default: S&P MidCap 400 + S&P SmallCap 600 (about 1,000 stocks), read from
Wikipedia together with each company's GICS sector and sub-industry, so the
sector exclusions cost no extra data calls.

Optional: put your own list in input/universe.csv and it is used instead.
The first column must hold tickers. Columns named name / sector / industry
are used if present. This is the way to widen the hunt beyond the S&P
indices, for example with a quarterly export from a free screener.
"""
from __future__ import annotations
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

INDEX_URLS = {
    "S&P 400": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
    "S&P 600": "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
}
CUSTOM_UNIVERSE = Path("input/universe.csv")
HEADERS = {"User-Agent": "Mozilla/5.0 (multibagger-screener; personal research)"}


def _clean(value) -> str:
    return "" if value is None or (isinstance(value, float) and value != value) else str(value).strip()


def _find_col(df: pd.DataFrame, *needles: str):
    for col in df.columns:
        low = str(col).lower()
        if any(n in low for n in needles):
            return col
    return None


def _rows_from_table(df: pd.DataFrame) -> list[dict]:
    sym = _find_col(df, "symbol", "ticker")
    if sym is None:
        raise ValueError(f"no ticker column in {list(df.columns)}")
    name = _find_col(df, "security", "company", "name")
    sub = _find_col(df, "sub-industry", "sub industry", "industry")
    sector = _find_col(df, "sector")
    rows = []
    for _, r in df.iterrows():
        ticker = _clean(r[sym]).upper().replace(".", "-")   # BRK.B -> BRK-B, as Yahoo writes it
        if not ticker or ticker == "NAN":
            continue
        rows.append({
            "ticker": ticker,
            "name": _clean(r[name]) if name is not None else "",
            "sector": _clean(r[sector]) if sector is not None else "",
            "industry": _clean(r[sub]) if sub is not None else "",
        })
    return rows


def _from_wikipedia() -> list[dict]:
    rows: dict[str, dict] = {}
    for label, url in INDEX_URLS.items():
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            tables = pd.read_html(StringIO(resp.text))
            # The constituents table is the first one with a Symbol column
            table = next(t for t in tables if _find_col(t, "symbol", "ticker") is not None and len(t) > 100)
            found = _rows_from_table(table)
            for r in found:
                rows.setdefault(r["ticker"], r)
            print(f"  {label}: {len(found)} tickers")
        except Exception as e:  # noqa: BLE001
            print(f"  {label}: FAILED ({type(e).__name__}: {e})")
    return sorted(rows.values(), key=lambda r: r["ticker"])


def _from_csv(path: Path) -> list[dict]:
    df = pd.read_csv(path)
    df = df.rename(columns={df.columns[0]: "ticker"}) if _find_col(df, "symbol", "ticker") is None else df
    rows = _rows_from_table(df)
    seen, out = set(), []
    for r in rows:
        if r["ticker"] not in seen:
            seen.add(r["ticker"])
            out.append(r)
    print(f"  custom universe {path}: {len(out)} tickers")
    return out


def get_universe() -> tuple[list[dict], str]:
    """Returns (rows, description). Each row: ticker, name, sector, industry."""
    if CUSTOM_UNIVERSE.exists():
        return _from_csv(CUSTOM_UNIVERSE), f"custom list ({CUSTOM_UNIVERSE})"
    return _from_wikipedia(), "S&P MidCap 400 + SmallCap 600"


def exclusion_reason(sector: str, industry: str, cfg) -> str | None:
    """Why a company is outside the checklist's scope, or None if it is in scope."""
    s, i = (sector or "").strip().lower(), (industry or "").strip().lower()
    for bad in cfg.EXCLUDED_SECTORS:
        if s and s == bad.lower():
            return f"sector: {sector}"
    for kw in cfg.EXCLUDED_INDUSTRY_KEYWORDS:
        if kw in i:
            return f"industry: {industry}"
    return None


SAMPLE = [
    {"ticker": t, "name": "", "sector": "", "industry": ""}
    for t in ["AAON", "APPF", "CRUS", "DUOL", "EXEL", "FIVE", "QLYS", "RMBS", "BOOT", "OLLI", "BROS", "CARG"]
]

"""
Generate a self-contained HTML report with a sortable, filterable table.

New in this version:
  * Multibagger Score (0-10) rendered as a colored badge
  * Rationale column with per-company reasoning
  * Ranked by Multibagger Score descending
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>US Multibagger Screener — {ts_short}</title>
<link rel="stylesheet" href="https://cdn.datatables.net/1.13.7/css/jquery.dataTables.min.css">
<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
<script src="https://cdn.datatables.net/1.13.7/js/jquery.dataTables.min.js"></script>
<style>
  :root {{
    --bg: #fafbfc;
    --card: #ffffff;
    --border: #e1e4e8;
    --text: #24292e;
    --muted: #6a737d;
    --accent: #0366d6;
    --good: #22863a;
    --warn: #b08800;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg); color: var(--text);
    margin: 0 auto; padding: 24px; max-width: 1700px;
  }}
  header {{ margin-bottom: 24px; }}
  h1 {{ margin: 0 0 4px; font-size: 24px; }}
  .meta {{ color: var(--muted); font-size: 13px; }}
  .cards {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 12px; margin: 20px 0;
  }}
  .card {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 6px; padding: 12px 16px;
  }}
  .card .label {{ font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }}
  .card .value {{ font-size: 22px; font-weight: 600; margin-top: 4px; }}
  .criteria {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 6px; padding: 14px 18px; margin-bottom: 20px; font-size: 13px;
  }}
  .criteria strong {{ color: var(--accent); }}
  table.dataTable {{
    background: var(--card); border: 1px solid var(--border); border-radius: 6px;
    font-size: 12.5px; width: 100% !important;
  }}
  table.dataTable thead th {{
    background: #f6f8fa; border-bottom: 2px solid var(--border);
    padding: 10px 8px; font-weight: 600; color: var(--text);
  }}
  table.dataTable tbody td {{ padding: 8px; border-bottom: 1px solid #eef1f4; vertical-align: top; }}
  table.dataTable tbody tr:hover {{ background: #f6f8fa; }}
  .rank {{ font-weight: 600; color: var(--muted); text-align: center; }}
  .ticker a {{ color: var(--accent); text-decoration: none; font-weight: 600; }}
  .ticker a:hover {{ text-decoration: underline; }}
  .rationale {{ font-size: 12px; color: #444; max-width: 380px; line-height: 1.4; }}
  .pos {{ color: var(--good); }}
  .neg {{ color: #cb2431; }}

  /* Multibagger Score badge */
  .mb-score {{
    display: inline-block; min-width: 40px; padding: 4px 8px;
    border-radius: 12px; font-weight: 700; font-size: 13px;
    text-align: center; color: white;
  }}
  .mb-elite {{ background: #22863a; }}   /* 9.0 - 10.0 */
  .mb-strong {{ background: #2f9e5c; }}  /* 8.0 - 8.9 */
  .mb-good {{ background: #7cb342; color: #1a3d0a; }}  /* 7.0 - 7.9 */
  .mb-fair {{ background: #f0c040; color: #4a3800; }}  /* 6.0 - 6.9 */
  .mb-marginal {{ background: #e0a040; color: #4a2800; }}  /* 5.0 - 5.9 */

  footer {{
    margin-top: 32px; padding-top: 16px; border-top: 1px solid var(--border);
    color: var(--muted); font-size: 12px;
  }}
  footer .disclaimer {{
    background: #fff8e1; border-left: 3px solid var(--warn);
    padding: 10px 14px; margin: 12px 0; color: #5c4a00;
  }}
</style>
</head>
<body>
<header>
  <h1>US Multibagger Candidate Screener</h1>
  <div class="meta">Generated {ts} · Data via Yahoo Finance (yfinance)</div>
</header>

<div class="cards">
  <div class="card"><div class="label">Universe scanned</div><div class="value">{universe:,}</div></div>
  <div class="card"><div class="label">Data fetched</div><div class="value">{fetched:,}</div></div>
  <div class="card"><div class="label">Passed filters</div><div class="value">{n_pass:,}</div></div>
  <div class="card"><div class="label">Shown in table</div><div class="value">{n_show:,}</div></div>
</div>

<div class="criteria">
<strong>Hard filters</strong>: Market cap $2B–$10B · ROE ≥12% · Op margin ≥8% · Gross margin ≥20% · Revenue growth ≥10% · D/E ≤1.5 · Current ratio ≥1.2 · Positive FCF<br>
<strong>Multibagger Score (0-10)</strong>: Weighted composite of Quality (30%) + Health (25%) + Growth (25%) + Valuation (15%) + Momentum (5%). Financial strength (Quality + Health = 55%) dominates. Score is percentile-ranked within the filtered pool: 10 = best passer, 5 = weakest passer.<br>
<strong>Score bands</strong>:
<span class="mb-score mb-elite" style="padding:2px 6px;font-size:11px">9.0+</span> Elite ·
<span class="mb-score mb-strong" style="padding:2px 6px;font-size:11px">8.0-8.9</span> Strong ·
<span class="mb-score mb-good" style="padding:2px 6px;font-size:11px">7.0-7.9</span> Good ·
<span class="mb-score mb-fair" style="padding:2px 6px;font-size:11px">6.0-6.9</span> Fair ·
<span class="mb-score mb-marginal" style="padding:2px 6px;font-size:11px">5.0-5.9</span> Marginal
</div>

{table}

<footer>
  <div class="disclaimer">
    <strong>Not investment advice.</strong> This is a factor screener — the Multibagger Score ranks candidates within the filtered pool; it is not a probability of future returns. Multibagger identification is inherently uncertain and even a perfect score is a research starting point, not a buy signal. Do your own due diligence on business model, management quality, competitive dynamics, and valuation before acting on any name here.
  </div>
  <div>Data source: Yahoo Finance</div>
</footer>

<script>
$(document).ready(function() {{
    $('#results').DataTable({{
        pageLength: 25,
        order: [[0, 'asc']],
        columnDefs: [
            {{ targets: 0, className: 'rank' }},
            {{ targets: 5, orderable: true, type: 'num' }},   // Score sorts numerically
            {{ targets: 6, className: 'rationale' }},
        ]
    }});
}});
</script>
</body>
</html>
"""


# ------------------------------------------------------------------ #
# Formatters
# ------------------------------------------------------------------ #

def _fmt_cap(x):
    if pd.isna(x): return "—"
    if x >= 1e9: return f"${x/1e9:.2f}B"
    if x >= 1e6: return f"${x/1e6:.0f}M"
    return f"${x:.0f}"


def _fmt_pct(x):
    if pd.isna(x): return "—"
    cls = "pos" if x >= 0 else "neg"
    return f'<span class="{cls}">{x*100:.1f}%</span>'


def _fmt_ratio(x):
    if pd.isna(x): return "—"
    return f"{x:.2f}"


def _ticker_link(t):
    return f'<span class="ticker"><a href="https://finance.yahoo.com/quote/{t}" target="_blank" rel="noopener">{t}</a></span>'


def _score_badge(score):
    """Render Multibagger Score as a colored badge."""
    if pd.isna(score):
        return "—"
    if score >= 9.0:   cls = "mb-elite"
    elif score >= 8.0: cls = "mb-strong"
    elif score >= 7.0: cls = "mb-good"
    elif score >= 6.0: cls = "mb-fair"
    else:              cls = "mb-marginal"
    # Include a hidden numeric span so DataTables can sort correctly
    return f'<span class="mb-score {cls}" data-order="{score}">{score:.1f}</span>'


# ------------------------------------------------------------------ #
# Main entry
# ------------------------------------------------------------------ #

def generate_html_report(
    scored_df: pd.DataFrame,
    universe_size: int,
    fetched_size: int,
    output_path: str | Path,
    top_n: int = 50,
) -> Path:
    """Write HTML report to output_path. Returns the path."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    n_pass = len(scored_df)
    top = scored_df.head(top_n).copy()

    n_rows = len(top)
    _none = pd.Series([None] * n_rows, index=top.index)

    display = pd.DataFrame({
        "Rank":         range(1, n_rows + 1),
        "Ticker":       top["ticker"].apply(_ticker_link),
        "Name":         top.get("name", pd.Series([""] * n_rows, index=top.index)).fillna(""),
        "Sector":       top.get("sector", pd.Series([""] * n_rows, index=top.index)).fillna(""),
        "Mkt Cap":      top["market_cap"].apply(_fmt_cap),
        "Score /10":    top["multibagger_score"].apply(_score_badge),
        "Rationale":    top["rationale"].fillna("—"),
        "ROE":          top.get("roe",              _none).apply(_fmt_pct),
        "Op Margin":    top.get("operating_margin", _none).apply(_fmt_pct),
        "Gross Margin": top.get("gross_margin",     _none).apply(_fmt_pct),
        "Rev Growth":   top.get("revenue_growth",   _none).apply(_fmt_pct),
        "D/E":          top.get("debt_to_equity",   _none).apply(_fmt_ratio),
        "P/E":          top.get("pe_ratio",         _none).apply(_fmt_ratio),
        "PEG":          top.get("peg_ratio",        _none).apply(_fmt_ratio),
    })

    table_html = display.to_html(
        table_id="results",
        classes="display compact",
        index=False,
        escape=False,
        border=0,
    )

    now = datetime.now(timezone.utc)
    html = HTML_TEMPLATE.format(
        ts=now.strftime("%Y-%m-%d %H:%M UTC"),
        ts_short=now.strftime("%Y-%m-%d"),
        universe=universe_size,
        fetched=fetched_size,
        n_pass=n_pass,
        n_show=n_rows,
        table=table_html,
    )

    output_path.write_text(html, encoding="utf-8")
    print(f"Report written: {output_path.resolve()}")
    return output_path

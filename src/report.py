"""
Generate a self-contained HTML report with a sortable, filterable table.

Columns include:
  * Multibagger Score (0-10) — fundamental composite, colored badge
  * Technical Score (0-10) — entry-point quality, colored badge
  * Supertrend Signal — BUY / SELL badge with days-in-trend
  * Rationale — auto-generated per-company reasoning
  * Key ratios (ROE, growth, PEG) and technicals (RSI, % from 200MA, % from 52w high)
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
    --bg: #fafbfc; --card: #ffffff; --border: #e1e4e8;
    --text: #24292e; --muted: #6a737d; --accent: #0366d6;
    --good: #22863a; --warn: #b08800; --bad: #cb2431;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg); color: var(--text);
    margin: 0 auto; padding: 24px; max-width: 1900px;
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
    line-height: 1.6;
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
  .rationale {{ font-size: 12px; color: #444; max-width: 340px; line-height: 1.4; }}
  .pos {{ color: var(--good); }}
  .neg {{ color: var(--bad); }}

  .score-badge {{
    display: inline-block; min-width: 40px; padding: 4px 8px;
    border-radius: 12px; font-weight: 700; font-size: 13px;
    text-align: center; color: white;
  }}
  .mb-elite    {{ background: #22863a; }}
  .mb-strong   {{ background: #2f9e5c; }}
  .mb-good     {{ background: #7cb342; color: #1a3d0a; }}
  .mb-fair     {{ background: #f0c040; color: #4a3800; }}
  .mb-marginal {{ background: #e0a040; color: #4a2800; }}
  .tech-great  {{ background: #22863a; }}
  .tech-good   {{ background: #7cb342; color: #1a3d0a; }}
  .tech-neutral{{ background: #f0c040; color: #4a3800; }}
  .tech-weak   {{ background: #e0a040; color: #4a2800; }}
  .tech-bad    {{ background: var(--bad); }}

  .signal {{
    display: inline-block; padding: 3px 10px; border-radius: 4px;
    font-weight: 700; font-size: 12px; letter-spacing: 0.5px;
  }}
  .signal-buy  {{ background: #d4edda; color: #155724; border: 1px solid #22863a; }}
  .signal-sell {{ background: #f8d7da; color: #721c24; border: 1px solid var(--bad); }}
  .signal-days {{ display: block; font-size: 10px; color: var(--muted); margin-top: 2px; font-weight: 400; }}

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
<strong>Hard filters</strong>: Market cap $1B–$15B · ROE ≥12% · Op margin ≥8% · Gross margin ≥20% · Revenue growth ≥10% · D/E ≤1.5 · Current ratio ≥1.2 · Positive FCF<br>
<strong>Multibagger Score (0-10, fundamental)</strong>: Quality (30%) + Health (25%) + Growth (25%) + Valuation (15%) + Momentum (5%). Financial strength (Quality + Health = 55%) dominates.<br>
<strong>Technical Score (0-10, entry-timing)</strong>: RSI in 40-60 sweet spot (+2), 5-20% above 200-day MA (+1.5), 10-25% pullback from 52w high (+1.5). Overextended prices are penalised.<br>
<strong>Supertrend (period=10, mult=3.0)</strong>: <span class="signal signal-buy">BUY</span> = price above trend line; <span class="signal signal-sell">SELL</span> = price below. Days count shows how long the current signal has held.
</div>

{table}

<footer>
  <div class="disclaimer">
    <strong>Not investment advice.</strong> The Multibagger Score is a fundamental ranking; the Technical Score is an entry-timing indicator; the Supertrend signal is a trend-following heuristic. Together they help you filter and prioritise — they do not tell you what to buy. All three can be wrong. Do your own due diligence on business model, management quality, competitive dynamics, and valuation before acting on any name here.
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
            {{ targets: -1, className: 'rationale' }},
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


def _fmt_pct_raw(x):
    """For already-in-percent values (e.g. pct_from_200ma)."""
    if pd.isna(x): return "—"
    cls = "pos" if x >= 0 else "neg"
    return f'<span class="{cls}">{x:+.1f}%</span>'


def _fmt_ratio(x):
    if pd.isna(x): return "—"
    return f"{x:.2f}"


def _fmt_rsi(x):
    if pd.isna(x): return "—"
    # Color code: green in sweet spot, yellow warning, red extremes
    if 40 <= x <= 60:      cls = "pos"
    elif 30 <= x < 40 or 60 < x <= 70:  cls = ""
    else:                  cls = "neg"
    return f'<span class="{cls}">{x:.0f}</span>'


def _ticker_link(t):
    return f'<span class="ticker"><a href="https://finance.yahoo.com/quote/{t}" target="_blank" rel="noopener">{t}</a></span>'


def _mb_badge(score):
    if pd.isna(score): return "—"
    if score >= 9.0:   cls = "mb-elite"
    elif score >= 8.0: cls = "mb-strong"
    elif score >= 7.0: cls = "mb-good"
    elif score >= 6.0: cls = "mb-fair"
    else:              cls = "mb-marginal"
    return f'<span class="score-badge {cls}" data-order="{score}">{score:.1f}</span>'


def _tech_badge(score):
    if pd.isna(score): return "—"
    if score >= 8.0:   cls = "tech-great"
    elif score >= 6.5: cls = "tech-good"
    elif score >= 5.0: cls = "tech-neutral"
    elif score >= 3.5: cls = "tech-weak"
    else:              cls = "tech-bad"
    return f'<span class="score-badge {cls}" data-order="{score}">{score:.1f}</span>'


def _supertrend_badge(signal, days):
    if pd.isna(signal) or signal is None: return "—"
    cls = "signal-buy" if signal == "BUY" else "signal-sell"
    days_str = f'<span class="signal-days">{int(days)}d</span>' if pd.notna(days) else ""
    return f'<span class="signal {cls}" data-order="{signal}">{signal}</span>{days_str}'


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
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    n_pass = len(scored_df)
    top = scored_df.head(top_n).copy()
    n_rows = len(top)
    _none = pd.Series([None] * n_rows, index=top.index)

    def _col(name):
        return top.get(name, _none)

    display = pd.DataFrame({
        "Rank":         range(1, n_rows + 1),
        "Ticker":       top["ticker"].apply(_ticker_link),
        "Name":         _col("name").fillna("").str.slice(0, 32),
        "Sector":       _col("sector").fillna(""),
        "Mkt Cap":      top["market_cap"].apply(_fmt_cap),
        "MB /10":       top["multibagger_score"].apply(_mb_badge),
        "Tech /10":     _col("technical_score").apply(_tech_badge),
        "Supertrend":   [_supertrend_badge(s, d) for s, d in
                         zip(_col("supertrend_signal"), _col("supertrend_days"))],
        "RSI":          _col("rsi_14").apply(_fmt_rsi),
        "vs 200MA":     _col("pct_from_200ma").apply(_fmt_pct_raw),
        "vs 52wHigh":   _col("pct_from_52w_high").apply(lambda x: f'<span class="neg">-{x:.1f}%</span>' if pd.notna(x) else "—"),
        "ROE":          _col("roe").apply(_fmt_pct),
        "Rev Growth":   _col("revenue_growth").apply(_fmt_pct),
        "PEG":          _col("peg_ratio").apply(_fmt_ratio),
        "Rationale":    _col("rationale").fillna("—"),
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

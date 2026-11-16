"""
HTML report. One self-contained page, no JavaScript, no external files.

Layout, top to bottom:
    summary line and any data warnings
    A  Actionable now      one card per stock, full 12-point checklist
    B  Watchlist           business qualifies, chart not ready
    C  Bench (collapsed)   proven business, not inflecting
    funnel                 how many stocks survive each step
    track record           how earlier tier A picks have done against the index
    method                 every threshold in use, read from config.py
"""
from __future__ import annotations
from html import escape
from pathlib import Path

from src.checklist import GATE_NAMES, num

CSS = """
:root{--bg:#f7f6f2;--card:#fff;--ink:#1d2127;--muted:#69707a;--line:#e3e1da;--accent:#2f5d8a;
--good:#2e7d4f;--good-bg:#e7f3ec;--bad:#b3423a;--bad-bg:#f8e9e7;--warn:#8a6100;--warn-bg:#fbf1d6;--chip:#eeede8}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--card:#1d2024;--ink:#e6e7e9;--muted:#9aa1ab;--line:#2c3036;
--accent:#8db7df;--good:#6fc391;--good-bg:#1c2e24;--bad:#e58a82;--bad-bg:#33201e;--warn:#e0bb5c;--warn-bg:#322a14;--chip:#272a2f}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:28px 16px 48px}
h1{font-size:24px;margin:0 0 2px}
h2{font-size:17px;margin:34px 0 4px}
.sub{color:var(--muted);font-size:13px;margin:0 0 10px}
.num,td.n{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:18px 0 6px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px}
.tile b{display:block;font-size:22px}
.tile span{font-size:12px;color:var(--muted)}
.banner{border-radius:8px;padding:10px 14px;margin:12px 0;font-size:14px}
.banner.warn{background:var(--warn-bg);color:var(--warn)}
.banner.note{background:var(--chip);color:var(--muted)}
.banner ul{margin:4px 0 0 18px;padding:0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:12px 0}
.card .head{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 12px}
.tk{font-size:19px;font-weight:700}
.nm{color:var(--muted)}
.badge{font-size:12px;font-weight:600;border-radius:999px;padding:2px 10px;background:var(--chip)}
.badge.em{background:var(--good-bg);color:var(--good)}
.badge.es{background:var(--chip);color:var(--accent)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px 16px;margin:12px 0 4px}
.stats div span{display:block;font-size:11.5px;color:var(--muted)}
.stats div b{font-size:15.5px}
.gates{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:6px 22px;margin-top:10px;border-top:1px solid var(--line);padding-top:10px}
.gates h4{margin:0 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
.it{font-size:13px;margin:3px 0;padding-left:20px;position:relative}
.it:before{position:absolute;left:0;font-weight:700}
.it.y:before{content:"\\2713";color:var(--good)}
.it.x:before{content:"\\2717";color:var(--bad)}
.it.u:before{content:"?";color:var(--warn)}
.it small{display:block;color:var(--muted)}
.flags{margin-top:8px;font-size:13px;color:var(--warn)}
.tablewrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;margin:10px 0}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:11.5px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);white-space:nowrap}
tr:last-child td{border-bottom:0}
td.n{text-align:right;white-space:nowrap}
.pos{color:var(--good)}.neg{color:var(--bad)}
.empty{background:var(--card);border:1px dashed var(--line);border-radius:10px;padding:16px 18px;color:var(--muted)}
details{margin:10px 0}summary{cursor:pointer;font-weight:600}
.bar{display:inline-block;height:8px;background:var(--accent);border-radius:4px;vertical-align:middle;opacity:.7}
.method{list-style:none;padding-left:4px;margin:4px 0 12px}.method li{margin:3px 0}
footer{margin-top:34px;padding-top:14px;border-top:1px solid var(--line);font-size:12.5px;color:var(--muted)}
"""


def _pct(x, signed=False, digits=0):
    x = num(x)
    if x is None:
        return "n/a"
    return f"{x:+.{digits}f}%" if signed else f"{x:.{digits}f}%"


def _signed_span(x, digits=0):
    v = num(x)
    if v is None:
        return "n/a"
    return f'<span class="{"pos" if v >= 0 else "neg"}">{v:+.{digits}f}%</span>'


def _pts(x):
    v = num(x)
    return "n/a" if v is None else f"{v:+.1f} pts"


def _cap(x, cfg):
    x = num(x)
    if x is None:
        return "n/a"
    if cfg.CAP_UNIT == "crore":
        return f"₹{x:,.0f} Cr"
    return f"${x / 1e9:.1f}B" if x >= 1e9 else f"${x / 1e6:.0f}M"


def _link(row, cfg):
    url = cfg.TICKER_URL.format(ticker=row["ticker"])
    return f'<a href="{escape(url)}" target="_blank" rel="noopener">{escape(row["ticker"])}</a>'


def _item_html(it):
    cls = "y" if it["ok"] is True else ("x" if it["ok"] is False else "u")
    detail = it["detail"] if it["ok"] is not None else f"no data: {it['detail']}"
    return f'<div class="it {cls}">{escape(it["label"])}<small>{escape(str(detail))}</small></div>'


def _card(row, cfg):
    m, c = row.get("metrics") or {}, row.get("chart") or {}
    stage = row.get("stage", "")
    badge_cls = "em" if stage.startswith("Emerging") else "es"
    meta = " · ".join(x for x in [row.get("industry") or row.get("sector") or "", _cap(row.get("market_cap"), cfg),
                                  f"P/E {num(row.get('pe')):.0f}" if num(row.get("pe")) else "",
                                  f"latest year ends {row['fy_end']}" if row.get("fy_end") else ""] if x)
    margin = f"{_pct(m.get('margin_latest'), digits=1)} <span class='nm'>(was {_pct(m.get('margin_3y_ago'), digits=1)})</span>"
    stats = [
        ("Revenue growth, 3 years", f"{_pct(m.get('rev_cagr_3y'))} a year"),
        (cfg.ROC_LABEL, _pct(m.get("roc_avg_3y"))),
        ("Operating margin", margin),
        ("Against the index, 6 months", _signed_span(c.get("rs_6m"))),
        ("Below 52-week high", _pct(c.get("pct_from_high"))),
        ("Price", f"{cfg.CURRENCY}{num(c.get('close')):,.2f}" if num(c.get("close")) else "n/a"),
    ]
    stats_html = "".join(f"<div><span>{escape(k)}</span><b class='num'>{v}</b></div>" for k, v in stats)
    gates_html = ""
    for g in (1, 2, 3):
        items = "".join(_item_html(i) for i in row["items"] if i["gate"] == g)
        gates_html += f"<div><h4>Gate {g} · {escape(GATE_NAMES[g])}</h4>{items}</div>"
    flags = row.get("flags") or []
    flags_html = f'<div class="flags">Caution: {escape("; ".join(flags))}</div>' if flags else ""
    return (f'<div class="card"><div class="head"><span class="tk">{_link(row, cfg)}</span>'
            f'<span>{escape(row.get("name") or "")}</span><span class="badge {badge_cls}">{escape(stage)}</span>'
            f'<span class="badge num">{row.get("score", 0)}/12</span></div>'
            f'<div class="sub">{escape(meta)}</div><div class="stats">{stats_html}</div>'
            f'<div class="gates">{gates_html}</div>{flags_html}</div>')


def _missing(row, gate):
    parts = []
    for it in row["items"]:
        if it["gate"] == gate and it["ok"] is not True:
            parts.append(f"<b>{escape(it['label'])}</b>: {escape(str(it['detail']))}")
    return "<br>".join(parts) or "n/a"


def _table(rows, cfg, gate, why_title):
    head = ("<tr><th>Ticker</th><th>Company</th><th>Mkt cap</th><th>Growth 3y</th>"
            f"<th>{escape(cfg.ROC_SHORT)}</th><th>Op margin</th><th>Chart</th><th>{escape(why_title)}</th></tr>")
    body = ""
    for r in rows:
        m = r.get("metrics") or {}
        body += (f"<tr><td><b>{_link(r, cfg)}</b></td><td>{escape(r.get('name') or '')}<br>"
                 f"<span class='nm'>{escape(r.get('industry') or r.get('sector') or '')}</span></td>"
                 f"<td class='n'>{_cap(r.get('market_cap'), cfg)}</td>"
                 f"<td class='n'>{_pct(m.get('rev_cagr_3y'))}</td><td class='n'>{_pct(m.get('roc_avg_3y'))}</td>"
                 f"<td class='n'>{_pct(m.get('margin_latest'), digits=1)}<br><span class='nm'>{_pts(m.get('margin_change_pp'))}</span></td>"
                 f"<td>{escape(r.get('stage') or '')}</td><td>{_missing(r, gate)}</td></tr>")
    return f'<div class="tablewrap"><table>{head}{body}</table></div>'


def _funnel(funnel):
    top = max((n for _, n in funnel if isinstance(n, (int, float))), default=1) or 1
    rows = ""
    for label, n in funnel:
        width = max(2, int(220 * n / top)) if n else 2
        rows += f"<tr><td>{escape(label)}</td><td class='n'>{n:,}</td><td><span class='bar' style='width:{width}px'></span></td></tr>"
    return f'<div class="tablewrap"><table><tr><th>Step</th><th>Stocks left</th><th></th></tr>{rows}</table></div>'


def _track(track, cfg):
    if not track or not track.get("rows"):
        return ('<div class="empty">No tier A picks logged yet. Each pick is logged the first time it appears, '
                'then measured against the index on every later run.</div>')
    s = track["summary"]
    if s["measured"]:
        line = (f"{s['measured']} pick(s) at least 4 weeks old: average return {_pct(s['avg_return'], signed=True, digits=1)}, "
                f"average {_pct(s['avg_excess'], signed=True, digits=1)} against the {escape(cfg.BENCHMARK_NAME)}, "
                f"{s['beat_index']} of {s['measured']} ahead of the index.")
        if s["measured"] < 20:
            line += " Too few picks to draw a conclusion yet."
    else:
        line = "No pick is 4 weeks old yet, so there is nothing to measure."
    body = ""
    for r in track["rows"]:
        then, now = num(r.get("price_then") or r.get("price_at_flag")), num(r.get("price_now"))
        body += (f"<tr><td><b>{escape(r['ticker'])}</b></td><td>{escape(r.get('name') or '')}</td>"
                 f"<td class='n'>{escape(str(r['first_flagged']))}</td>"
                 f"<td class='n'>{f'{then:,.2f}' if then else 'n/a'}</td><td class='n'>{f'{now:,.2f}' if now else 'n/a'}</td>"
                 f"<td class='n'>{_signed_span(r.get('return_pct'), 1)}</td><td class='n'>{_signed_span(r.get('index_return_pct'), 1)}</td>"
                 f"<td class='n'>{_signed_span(r.get('excess_pct'), 1)}</td><td class='n'>{escape(str(r.get('last_flagged', '')))}</td></tr>")
    head = ("<tr><th>Ticker</th><th>Company</th><th>First picked</th><th>Price then</th><th>Price now</th>"
            "<th>Return</th><th>Index</th><th>Difference</th><th>Last in tier A</th></tr>")
    return f'<p class="sub">{line}</p><div class="tablewrap"><table>{head}{body}</table></div>'


def _method(cfg):
    g1 = [
        f"Revenue growth of at least {cfg.REV_CAGR_MIN:.0f}% a year over 3 years",
        "Revenue higher in each of the last 3 years",
        f"{cfg.ROC_LABEL} of at least {cfg.ROC_MIN:.0f}%, averaged over 3 years",
        f"{cfg.CASH_LABEL} positive in at least {cfg.CASH_POSITIVE_YEARS_MIN} of the last 4 years",
        f"{cfg.CASH_LABEL} of at least {cfg.CASH_CONVERSION_MIN:.0f}% of net profit over 3 years",
        cfg.METHOD_ITEM_6,
        cfg.METHOD_ITEM_7,
    ]
    g2 = [
        f"Operating margin at least {cfg.MARGIN_EXPANSION_MIN_PP:g} percentage point(s) above 3 years ago, and not down "
        f"more than {cfg.MARGIN_RECENT_DIP_MAX_PP:g} on last year",
        f"Latest-year growth of at least {cfg.GROWTH_NOW_MIN:.0f}% and at least {cfg.GROWTH_FADE_RATIO * 100:.0f}% of the 3-year pace; "
        f"latest quarter up at least {cfg.RECENT_QUARTER_GROWTH_MIN:.0f}% on a year ago when that figure is available",
    ]
    g3 = [
        "Weekly Supertrend (ATR 10, multiplier 2.5) on BUY",
        f"Emerging (BUY for {cfg.FRESH_FLIP_WEEKS} weeks or less): price above the 200-day average. "
        f"Established: price above a rising 200-day average and within {cfg.NEAR_HIGH_MAX_PCT:.0f}% of the 52-week high",
        f"Emerging: ahead of the {cfg.BENCHMARK_NAME} over 3 months. Established: ahead over both 6 and 12 months",
    ]
    out, n = "", 0
    for g, lines in ((1, g1), (2, g2), (3, g3)):
        lis = ""
        for line in lines:
            n += 1
            lis += f"<li><b>{n}.</b> {escape(line)}</li>"
        out += f"<p><b>Gate {g} · {escape(GATE_NAMES[g])}</b></p><ul class='method'>{lis}</ul>"
    return out


def generate_report(ctx: dict, output_path) -> Path:
    cfg = ctx["cfg"]
    rows = ctx["rows"]
    a = [r for r in rows if r["tier"] == "A"]
    b = [r for r in rows if r["tier"] == "B"]
    c = [r for r in rows if r["tier"] == "C"]
    health = ctx.get("health") or {}

    banners = ""
    if health.get("warnings"):
        lis = "".join(f"<li>{escape(w)}</li>" for w in health["warnings"])
        banners += f'<div class="banner warn"><b>Data warnings for this run</b><ul>{lis}</ul></div>'
    if health.get("notes"):
        lis = "".join(f"<li>{escape(w)}</li>" for w in health["notes"])
        banners += f'<div class="banner note"><ul>{lis}</ul></div>'

    if a:
        a_html = "".join(_card(r, cfg) for r in a)
    else:
        a_html = ('<div class="empty">No stock passes all three gates in this run. The bar is fixed, not relative, '
                  'so an empty week is a valid result. The watchlist below shows which businesses already qualify '
                  'and what their charts still lack.</div>')
    b_html = _table(b, cfg, 3, "What the chart still lacks") if b else \
        '<div class="empty">No business currently passes gates 1 and 2 with a chart that is not ready.</div>'
    c_html = _table(c, cfg, 2, "Why it is not inflecting") if c else '<div class="empty">None.</div>'

    tiles = [
        (len(a), "Actionable now"), (len(b), "Watchlist"), (len(c), "Bench"),
        (ctx.get("screened", 0), "Stocks screened"),
    ]
    tiles_html = "".join(f'<div class="tile"><b class="num">{n:,}</b><span>{escape(t)}</span></div>' for n, t in tiles)

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(cfg.REPORT_TITLE)}</title><style>{CSS}</style></head><body><div class="wrap">
<h1>{escape(cfg.REPORT_TITLE)}</h1>
<p class="sub">Generated {escape(ctx.get("generated", ""))} · {escape(cfg.UNIVERSE_NAME)} · fundamentals: {escape(cfg.FUNDAMENTALS_SOURCE)} · prices: Yahoo Finance</p>
<div class="tiles">{tiles_html}</div>
{banners}

<h2>A · Actionable now</h2>
<p class="sub">Passes all 12 checks: a proven business, still inflecting, with the chart confirming. Ordered by growth plus return on capital.</p>
{a_html}

<h2>B · Watchlist</h2>
<p class="sub">The business passes gates 1 and 2. The chart does not confirm yet, so these are names to watch, not to act on.</p>
{b_html}

<h2>C · Bench</h2>
<details><summary>{len(c)} proven {"business" if len(c) == 1 else "businesses"} not inflecting right now</summary>
<p class="sub">Passes gate 1 but not gate 2: growth is fading or margins are not expanding.</p>
{c_html}</details>

<h2>Where the stocks dropped out</h2>
<p class="sub">Rows marked "alone" count how many stocks pass that one check by itself; the other rows are cumulative. A check that almost nothing passes is the one to look at in config.py.</p>
{_funnel(ctx.get("funnel") or [])}

<h2>Track record of earlier picks</h2>
{_track(ctx.get("track"), cfg)}

<h2>Method</h2>
<p class="sub">Every check is a fixed yes or no. A check with missing data counts as not met. Thresholds live in config.py.</p>
{_method(cfg)}

<footer>
<p><b>Not investment advice.</b> This is a screening tool for research. The checklist has not been validated against
past returns; the track record above is the only evidence it will ever produce, and it needs months to mean anything.
Company numbers come from free data sources and can be wrong or out of date, so confirm them in the company's own
filings before acting. Management quality, governance and fraud risk are not measured here.</p>
</footer></div></body></html>"""

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    print(f"Report written: {path}")
    return path

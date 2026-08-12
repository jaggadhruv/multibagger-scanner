# US Multibagger Screener

A factor-based screener that ranks US stocks (S&P 1500) by their fit to a
multibagger profile: small-to-mid cap, high quality, growing, financially
healthy, reasonably valued.

**Free**. No paid APIs. No signup. Runs locally and on GitHub Actions.

---

## What this actually does

1. Pulls ~1,000 tickers from the S&P 400 (MidCap) + S&P 600 (SmallCap) — the multibagger hunting ground. Add `--include-large` to include S&P 500 (rarely useful).
2. Fetches fundamentals for each via `yfinance` (free).
3. Applies hard filters (market cap $1B–$15B, ROE ≥12%, positive FCF, etc.) — narrows to ~30–100 candidates.
4. **Fundamental scoring** — composite Multibagger Score (0-10) weighted toward financial strength.
5. **Technical analysis** (only for candidates that passed filters — keeps it fast):
   - RSI (14-day, Wilder smoothing)
   - Distance from 50-day and 200-day moving averages
   - Position within 52-week range
   - **Technical Score (0-10)** — "how good is this entry point?"
   - **Supertrend indicator** — BUY / SELL signal with days-in-trend
6. Writes a self-contained interactive HTML report ranked by Multibagger Score.

**This is a candidate generator, not a buy list.** Fundamental score, technical score, and Supertrend signal are three independent lenses — the more they agree, the higher-conviction the setup, but all three can be wrong.

---

## Quick start (local)

You'll need Python 3.10+.

```bash
# 1. Clone your repo (once you push it)
git clone https://github.com/<your-username>/multibagger-screener.git
cd multibagger-screener

# 2. Set up a virtual environment
python -m venv .venv
source .venv/bin/activate      # macOS/Linux
# .venv\Scripts\activate       # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Smoke test with a small sample (~30 tickers, 1 minute)
python main.py --sample

# 5. Open the report
open output/index.html          # macOS
# start output/index.html       # Windows
# xdg-open output/index.html    # Linux
```

If that works, do a real run:

```bash
# Full S&P 1500 run — 15 to 30 minutes depending on network / rate limiting
python main.py

# Or scale up gradually
python main.py --limit 200
python main.py --limit 500
```

---

## CLI flags

| Flag | What it does |
|------|--------------|
| `--sample` | ~30 hand-picked tickers, useful for smoke tests |
| `--limit N` | First N tickers of the universe (for iteration) |
| `--include-large` | Add S&P 500 to the universe (default: mid+small only). Only useful for benchmarking; large caps rarely become multibaggers. |
| `--permissive` | Include rows with missing data (larger candidate pool, noisier) |
| `--top N` | Number of stocks to show in the HTML report (default 50) |
| `--workers N` | Concurrent yfinance fetches (default 8; higher = faster but more likely to be rate-limited) |
| `--output-dir DIR` | Where CSVs and HTML go (default `output/`) |

---

## Project layout

```
multibagger-screener/
├── main.py                     # Entry point
├── requirements.txt
├── README.md
├── .gitignore
├── src/
│   ├── universe.py             # Get tickers from Wikipedia
│   ├── fetch.py                # yfinance fundamentals
│   ├── screen.py               # Hard filters
│   ├── score.py                # Fundamental scoring + rationale
│   ├── technicals.py           # RSI, MA, Supertrend, Technical Score
│   └── report.py               # HTML report generation
├── .github/workflows/
│   └── screener.yml            # Weekly automated run + Pages deploy
└── output/                     # Generated (gitignored except index.html)
    ├── raw_data.csv
    ├── filtered.csv
    ├── scored.csv
    └── index.html
```

---

## Tuning the screener

**Loosening or tightening filters** — edit `DEFAULT_FILTERS` in `src/screen.py`:

```python
DEFAULT_FILTERS = {
    "market_cap_min": 2_000_000_000,     # $2B floor (strong consolidated candidates)
    "market_cap_max": 10_000_000_000,    # $10B ceiling (still room to 10x)
    "roe_min": 0.12,                     # 12%
    "operating_margin_min": 0.08,
    "gross_margin_min": 0.20,
    "revenue_growth_min": 0.10,
    "debt_to_equity_max": 1.5,
    "current_ratio_min": 1.2,
    "positive_fcf_required": True,
}
```

**Changing factor weights** — edit `DEFAULT_WEIGHTS` in `src/score.py`:

```python
DEFAULT_WEIGHTS = {
    "quality":   0.30,   # Profitability, capital efficiency
    "growth":    0.25,   # Revenue and earnings growth
    "health":    0.25,   # Balance sheet, liquidity, FCF (financial strength)
    "valuation": 0.15,
    "momentum":  0.05,
}
```

**Multibagger Score (0-10)** — percentile-ranked within the filtered pool.
Best passing candidate = 10.0, weakest passing candidate = 5.0.
The report also generates a per-company rationale explaining the score.

**Sector-neutral scoring** (a common next step): group by sector before
computing z-scores, so you're comparing tech companies to tech companies rather
than to utilities. Left as an exercise — it's a ~10-line change in `score.py`.

---

## Pushing to GitHub

```bash
git init
git add .
git commit -m "Initial multibagger screener"
git branch -M main
git remote add origin https://github.com/<your-username>/multibagger-screener.git
git push -u origin main
```

---

## Automating with GitHub Actions

The workflow at `.github/workflows/screener.yml` will:

- Run **every Saturday at 06:00 UTC** (after Friday US close).
- Run **on-demand** from the Actions tab (with optional `limit` / `permissive` inputs).
- Upload the whole `output/` folder as an artifact (downloadable for 30 days).
- Publish the HTML report to **GitHub Pages** at `https://<your-username>.github.io/multibagger-screener/report.html`.

### One-time setup for GitHub Pages

1. Push the repo. Let the first Action run finish (or trigger it manually via **Actions → Weekly Multibagger Screener → Run workflow**).
2. This creates a `gh-pages` branch.
3. Go to **Settings → Pages**. Under "Build and deployment", set:
   - Source: **Deploy from a branch**
   - Branch: **gh-pages** / root
4. Save. Within a minute your report will be live at `https://<your-username>.github.io/multibagger-screener/`.

### Cost

Public repos on GitHub Actions: **unlimited free minutes**. This job runs in
~15–30 min once a week, so cost is $0.

---

## Known limitations / honest caveats

- **yfinance is unofficial.** Yahoo occasionally changes their internal
  endpoints and yfinance breaks until it's updated. If you see mass NaN,
  `pip install --upgrade yfinance` usually fixes it.
- **Rate limiting.** With 8 concurrent workers you should be fine. Bumping to
  20+ workers gets you rate-limited and rows fail. Retries help but don't
  eliminate this.
- **Data quality varies.** Small caps in particular have missing values for
  things like PEG ratio or gross margin. Strict mode excludes them; permissive
  mode keeps them with penalty.
- **Sector neutrality.** Current scoring is cross-sectional across all sectors,
  so highly profitable sectors (software, semis) will dominate. If you want
  sector-neutral ranks, group by sector before z-scoring.
- **Survivorship bias.** Wikipedia constituent lists reflect *current* index
  members. Companies that were dropped (often after decline) aren't in your
  universe. This matters more for backtesting than for forward screening.
- **No qualitative overlay.** Business model, management quality, moat depth,
  fraud risk — none of these are in the screener. That's your job before you
  act on any name.
- **US markets only for now.** India requires a separate data pipeline (see
  next steps).

---

## Suggested next steps

1. **Sector-neutral scoring** — see comment in `score.py`.
2. **5-year growth CAGRs** — pull from `stock.financials` rather than just
   trailing YoY. More stable signal.
3. **Insider transactions** — `stock.insider_transactions` gives you buys/sells.
4. **News flagging** — auditor changes, guidance cuts, management departures.
5. **Backtesting harness** — replay the screen at monthly snapshots on
   historical constituent lists, track forward 3/5-year returns.
6. **India module** — a parallel pipeline using nsepython / screener.in for
   Indian stocks, with promoter pledge as an additional filter.

---

## Disclaimer

This tool is for **educational and research purposes only**. Nothing here is
investment advice. The author (you, once you fork it) makes no warranties about
data accuracy or fitness for any purpose. You are solely responsible for your
investment decisions.

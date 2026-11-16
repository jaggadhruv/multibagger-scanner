# US Multibagger Shortlist

A weekly screen of US mid and small caps that returns a short list, not a ranking.
A stock is shown only if it clears a fixed 12-point checklist: a business that has
already proven itself, is still inflecting, and whose chart is confirming it.

Free data only (Yahoo Finance through `yfinance`, Wikipedia for the index lists).
No API keys, no secrets.

## What changed from the old version

| Old | New |
|---|---|
| Scored stocks against each other, so someone always got 10/10 and 50 names were shown | Every check is a fixed yes or no. Zero stocks in a week is a valid result |
| One-year snapshot ratios | 4 years of annual statements: growth, return on capital, cash flow, margins |
| Banks, REITs, oil, miners mixed in | Excluded up front, the checklist does not fit them |
| Chart data shown but ignored in ranking | Chart is a gate. No uptrend, no tier A |
| Rewarded pullbacks, penalised strength | Requires strength, either established or just starting |
| No record of results | Every pick is logged and measured against the S&P 500 |

## The checklist

**Gate 1, proven business** (all seven required)

1. Revenue growth of 15%+ a year over 3 years
2. Revenue up in each of the last 3 years
3. Return on invested capital of 15%+, 3-year average
4. Free cash flow positive in at least 3 of the last 4 years
5. Free cash flow at least 60% of net profit over 3 years
6. Financial debt no more than 3x operating profit
7. Share count growing no more than 4% a year

**Gate 2, early / inflecting** (both required)

8. Operating margin higher than 3 years ago, and not rolling over
9. Growth holding up: latest year and latest quarter not fading

**Gate 3, chart strength** (all three required, two routes)

10. Weekly Supertrend on BUY
11. Price above the 200-day average
12. Beating the S&P 500

A weekly BUY that is 12 weeks old or less is treated as **emerging**: it needs the
price above the 200-day average and a 3-month lead over the index. An older BUY is
**established** and must meet a stricter test: a rising 200-day average, within 15%
of the 52-week high, ahead of the index over both 6 and 12 months.

Size band: market cap between $1B and $15B.

## What the report shows

| Tier | Meaning |
|---|---|
| **A, Actionable now** | Passes all 12 checks |
| **B, Watchlist** | Business passes gates 1 and 2, chart not ready. Shows exactly what the chart lacks |
| **C, Bench** (collapsed) | Proven business that is not inflecting right now |

Below the tiers: a funnel showing how many stocks pass each check, and a track
record of every earlier tier A pick against the index.

A check with missing data counts as **not met**. If a whole check has no data for
most companies, the report says so in a warning box at the top.

## Running it

```bash
pip install -r requirements.txt
python -m tests.test_offline     # 17 offline tests, about 5 seconds, no network
python main.py --sample          # 12 tickers, checks the live data path
python main.py                   # full run
```

| Flag | Use |
|---|---|
| `--sample` | 12 tickers |
| `--limit N` | first N in-scope tickers |
| `--skip-fetch` | use cached statements only, fetch nothing but prices |
| `--refresh-all` | ignore the cache and refetch every company |
| `--max-fetch-minutes N` | time budget for statement fetching (default 40) |

## The first run takes longer

Annual statements are fetched one company at a time (about 650 companies, 3 calls
each, roughly 25 to 30 minutes) and stored in `data/fundamentals_cache.json`. After that,
each weekly run refreshes only the quarter of the cache that has gone stale.

If Yahoo starts refusing requests, or the time budget runs out, the run stops
fetching, keeps what it has, publishes a report marked as incomplete, and the next
run continues where this one stopped. Run the workflow again to finish the fill.

## GitHub Actions

`.github/workflows/screener.yml` runs every Saturday 06:00 UTC and on demand.
It commits `data/` back to the repository (cache and picks log) and publishes
`output/` to GitHub Pages. It needs **Settings > Actions > General > Workflow
permissions > Read and write**, which the previous version already required.

## Tuning

Every threshold is in `config.py`. The funnel in the report shows which check
removes the most stocks. Change one number, run with `--skip-fetch`, and the new
result is ready in under a minute because the statements are already cached.

To screen beyond the S&P 400 and 600, save any list of tickers as
`input/universe.csv` (first column tickers; optional `name`, `sector`, `industry`
columns). It replaces the index lists.

## Files

```
main.py                 entry point
config.py               every threshold
src/universe.py         index lists, sector exclusions
src/fundamentals.py     Yahoo statements, cache, market-specific checks 6 and 7
src/checklist.py        the 12 checks, gates and tiers
src/chart.py            prices, 200-day average, relative strength, Supertrend
src/supertrend.py       the Supertrend indicator
src/tracklog.py         picks log and performance against the index
src/pipeline.py         ties the steps together, writes CSVs
src/report.py           HTML report
tests/                  offline tests with a fake Yahoo
data/                   fundamentals_cache.json, picks_log.csv (tracked in git)
output/                 index.html, all_companies.csv, shortlist.csv (regenerated)
```

Files from the old version that are no longer used and can be deleted:
`src/fetch.py`, `src/screen.py`, `src/score.py`, `src/technicals.py`.

## Limits

- **Not validated.** No backtest stands behind these thresholds. The track record
  in the report is the evidence, and it needs months and dozens of picks to mean anything.
- **Annual data lags.** Statements are up to a year old by the time the next report
  arrives. Check 9 uses the latest quarter to catch a stall, when Yahoo provides it.
- **Yahoo data has errors.** Confirm the numbers in the company's 10-K before acting.
- **The universe is the S&P 400 and 600.** Younger companies outside those indices
  are not screened unless you supply your own list.
- **The picks log holds the screener's picks only.** It never contains your own
  trades or positions.
- Management quality, governance and fraud risk are not measured.

For research only. Not investment advice.

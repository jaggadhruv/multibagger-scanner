"""
Every setting for the US screener lives here. Change a number, run again.

Percentages are plain numbers: 15.0 means 15%.
"""

# ------------------------------------------------------------------ #
# Market
# ------------------------------------------------------------------ #
MARKET = "US"
REPORT_TITLE = "US Multibagger Shortlist"
UNIVERSE_NAME = "S&P MidCap 400 + SmallCap 600"
FUNDAMENTALS_SOURCE = "Yahoo Finance annual statements"
CURRENCY = "$"
CAP_UNIT = "usd"
TICKER_URL = "https://finance.yahoo.com/quote/{ticker}"
PRICE_SUFFIX = ""                       # appended to tickers for Yahoo price history
BENCHMARK_SYMBOLS = ["SPY", "^GSPC"]    # tried in order
BENCHMARK_NAME = "S&P 500"

# ------------------------------------------------------------------ #
# Universe
# ------------------------------------------------------------------ #
# Sectors where the checklist does not work. Banks, insurers and REITs do not
# have meaningful operating margins, debt ratios or free cash flow. Energy and
# the commodity industries below look like growth stocks at the top of a price
# cycle and then give it all back.
EXCLUDED_SECTORS = ["Financials", "Financial Services", "Real Estate", "Energy"]
EXCLUDED_INDUSTRY_KEYWORDS = [
    "oil & gas", "coal", "uranium", "metals & mining", "steel", "gold", "silver", "aluminum", "copper",
    "precious metals", "commodity chemicals", "fertilizers", "agricultural inputs", "agricultural products",
    "farm products", "forest products", "paper products", "marine transportation", "marine shipping",
]

# Size band: big enough to be established, small enough to still multiply.
MARKET_CAP_MIN = 1_000_000_000
MARKET_CAP_MAX = 15_000_000_000

# ------------------------------------------------------------------ #
# Gate 1: proven business
# ------------------------------------------------------------------ #
REV_CAGR_MIN = 15.0                 # revenue growth, % a year over 3 years
ROC_LABEL = "Return on invested capital"
ROC_SHORT = "ROIC"
ROC_MIN = 15.0                      # after-tax operating profit / (equity + debt), 3-year average
CASH_LABEL = "Free cash flow"
CASH_POSITIVE_YEARS_MIN = 3         # of the last 4 years
CASH_CONVERSION_MIN = 60.0          # 3-year free cash flow as % of 3-year net profit
DEBT_TO_OPERATING_PROFIT_MAX = 3.0  # financial debt (leases excluded) / latest operating profit
DILUTION_MAX_PCT = 4.0              # share count growth, % a year over 3 years

# ------------------------------------------------------------------ #
# Gate 2: early / inflecting
# ------------------------------------------------------------------ #
MARGIN_EXPANSION_MIN_PP = 1.0       # operating margin now vs 3 years ago, percentage points
MARGIN_RECENT_DIP_MAX_PP = 1.5      # ...and not down more than this against last year
GROWTH_NOW_MIN = 12.0               # latest fiscal year revenue growth, %
GROWTH_FADE_RATIO = 0.6             # latest growth must be at least this share of the 3-year pace
RECENT_QUARTER_GROWTH_MIN = 8.0     # latest quarter vs a year ago, % (only checked when available)

# ------------------------------------------------------------------ #
# Gate 3: chart strength
# ------------------------------------------------------------------ #
FRESH_FLIP_WEEKS = 12               # weekly Supertrend BUY this young counts as "emerging"
NEAR_HIGH_MAX_PCT = 15.0            # established trends must be within this % of the 52-week high
EXTENDED_ABOVE_200_PCT = 40.0       # caution flag only, never changes the tier
RSI_HOT = 75.0                      # caution flag only

# ------------------------------------------------------------------ #
# Data fetching
# ------------------------------------------------------------------ #
FETCH_DELAY = 0.4                   # seconds between Yahoo calls
FUNDAMENTALS_MAX_MINUTES = 40       # stop fetching statements after this long; the next run resumes
CACHE_DAYS_MIN = 28                 # statements are refetched every 28-55 days, spread across weeks
CACHE_DAYS_SPREAD = 28
CACHE_DAYS_SHORTLIST = 10           # ...and every 10 days for stocks that pass gate 1
EMPTY_STREAK_LIMIT = 6              # this many failures in a row is treated as rate limiting
COOLDOWN_SECONDS = 90
MAX_COOLDOWNS = 2

# Text shown in the report's Method section for the two market-specific checks
METHOD_ITEM_6 = (f"Financial debt (leases excluded) of no more than {DEBT_TO_OPERATING_PROFIT_MAX:g} times "
                 "the latest operating profit")
METHOD_ITEM_7 = f"Share count growing no more than {DILUTION_MAX_PCT:g}% a year over 3 years"

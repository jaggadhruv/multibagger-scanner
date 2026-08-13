"""
Standalone data source diagnostic — run this to see EXACTLY what each source
returns for a single ticker.

Usage:
    python debug_sources.py                # tests AAPL
    python debug_sources.py MSFT           # tests MSFT
"""
import sys
import requests
from src.technicals import BROWSER_HEADERS, STOOQ_URL, fetch_from_stooq, fetch_from_yfinance

ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"

print("=" * 70)
print(f"DIAGNOSING SOURCES FOR: {ticker}")
print("=" * 70)

# --- Test 1: raw stooq HTTP call ---
print("\n[1] Raw stooq HTTP call")
print("-" * 70)
symbol = ticker.lower() + ".us"
url = f"{STOOQ_URL}?s={symbol}&i=d"
print(f"URL: {url}")
print(f"Headers: {BROWSER_HEADERS}")

try:
    r = requests.get(STOOQ_URL, params={"s": symbol, "i": "d"},
                     headers=BROWSER_HEADERS, timeout=20)
    print(f"\nStatus code: {r.status_code}")
    print(f"Response headers: {dict(r.headers)}")
    print(f"Response length: {len(r.text)} chars")
    print(f"\n--- First 500 chars of response ---")
    print(r.text[:500])
    print("--- END ---")
except Exception as e:
    print(f"REQUEST FAILED: {type(e).__name__}: {e}")

# --- Test 2: our stooq wrapper ---
print("\n\n[2] fetch_from_stooq() wrapper")
print("-" * 70)
hist, err = fetch_from_stooq(ticker)
if hist is not None:
    print(f"SUCCESS — got {len(hist)} rows")
    print(f"Date range: {hist.index[0].date()} to {hist.index[-1].date()}")
    print(f"Latest close: {hist['Close'].iloc[-1]}")
else:
    print(f"FAILED — {err}")

# --- Test 3: yfinance fallback ---
print("\n\n[3] fetch_from_yfinance() fallback")
print("-" * 70)
hist, err = fetch_from_yfinance(ticker, retries=1)
if hist is not None:
    print(f"SUCCESS — got {len(hist)} rows")
    print(f"Date range: {hist.index[0].date()} to {hist.index[-1].date()}")
    print(f"Latest close: {hist['Close'].iloc[-1]}")
else:
    print(f"FAILED — {err}")

print("\n" + "=" * 70)
print("DIAGNOSIS")
print("=" * 70)
print("""
If [1] returned an HTML page or non-200 status → stooq is blocking this IP.
If [1] returned "No data" → the ticker doesn't exist on stooq.
If [2] and [3] both failed → try another data provider entirely.
If [3] worked → yfinance fallback should save the day (slower but reliable).
""")

import subprocess, sys
subprocess.check_call([sys.executable, "-m", "pip", "install", "yfinance", "-q"])

import yfinance as yf
import pandas as pd
import os

os.makedirs("backtests", exist_ok=True)

years = 5
print(f"Downloading {years} years of SPY daily data from Yahoo Finance...")

spy = yf.download("SPY", period=f"{years}y", interval="1d",
                  auto_adjust=True, progress=False)

# yfinance MultiIndex: level 0 = field (Close/Open/...), level 1 = ticker (SPY)
# Drop the ticker level, keep field names, lowercase them
if isinstance(spy.columns, pd.MultiIndex):
    spy.columns = [col[0].lower() for col in spy.columns]
else:
    spy.columns = [c.lower() for c in spy.columns]

spy.index = pd.to_datetime(spy.index).date
spy = spy.sort_index()

# Verify expected columns exist
missing = [c for c in ["open","high","low","close","volume"] if c not in spy.columns]
if missing:
    print(f"WARNING: missing columns {missing}")
    print(f"Got: {spy.columns.tolist()}")
    print("Trying alternate column extraction...")
    # Fallback: rename positionally
    spy.columns = ["open","high","low","close","volume"]

spy = spy[["open","high","low","close","volume"]]
spy.to_csv("backtests/spy_history.csv")

print(f"Saved {len(spy)} bars to backtests/spy_history.csv")
print(f"Date range: {spy.index[0]} to {spy.index[-1]}")
print(f"Columns: {spy.columns.tolist()}")
print(f"Sample row: {spy.iloc[-1].to_dict()}")
print("")
print("Done. Now run:")
print("  python write_backtest.py")
print("  python -m backtests.spy_daily_backtest --years 5 --source local")

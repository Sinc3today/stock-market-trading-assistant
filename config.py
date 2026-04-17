"""
config.py — Central configuration for the Trading Assistant
All thresholds, API settings, and scoring constants live here.
Change values here to affect the entire system.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────
# API KEYS
# ─────────────────────────────────────────
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID_STANDARD = int(os.getenv("DISCORD_CHANNEL_ID_STANDARD", 0))
DISCORD_CHANNEL_ID_HIGH_CONVICTION = int(os.getenv("DISCORD_CHANNEL_ID_HIGH_CONVICTION", 0))
DISCORD_CHANNEL_ID_NEWS = int(os.getenv("DISCORD_CHANNEL_ID_NEWS", 0))

# ─────────────────────────────────────────
# SCORING THRESHOLDS
# ─────────────────────────────────────────
SCORE_ALERT_MINIMUM = 75          # Below this = logged only, no alert fired
SCORE_HIGH_CONVICTION = 90        # At or above this = High Conviction alert 🔴
SCORE_WATCHLIST = 60              # Below alert min but above this = watchlist log

# Timeframe confluence bonus multiplier
CONFLUENCE_BONUS_MULTIPLIER = 1.15

# ─────────────────────────────────────────
# SCORING LAYER MAX POINTS
# ─────────────────────────────────────────
SCORE_TREND_MAX = 35
SCORE_SETUP_MAX = 35
SCORE_VOLUME_MAX = 30

# ─────────────────────────────────────────
# RISK / REWARD
# ─────────────────────────────────────────
MIN_RISK_REWARD_RATIO = 2.0       # Hard gate — alert suppressed if R/R below this

# ─────────────────────────────────────────
# INDICATOR SETTINGS
# ─────────────────────────────────────────

# Moving Averages
MA_SHORT = 20
MA_MID = 50
MA_LONG = 200

# Donchian Channel
DONCHIAN_PERIOD = 20              # 20-period channel (swing)
DONCHIAN_INTRADAY_PERIOD = 20     # Same logic, applied to intraday bars

# Volume
VOLUME_SPIKE_MULTIPLIER = 1.5     # Volume must be 1.5x the 20-period average
VOLUME_LOOKBACK = 20              # Periods to average for baseline volume

# RSI
RSI_PERIOD = 14
RSI_DIVERGENCE_LOOKBACK = 5       # Pivot confirmation window (Option B)

# ─────────────────────────────────────────
# TIMEFRAME SETTINGS
# ─────────────────────────────────────────

# Swing mode
SWING_PRIMARY_TIMEFRAME = "day"
SWING_SECONDARY_TIMEFRAME = "4hour"

# Intraday mode
INTRADAY_PRIMARY_TIMEFRAME = "15min"
INTRADAY_SECONDARY_TIMEFRAME = "5min"

# ─────────────────────────────────────────
# SCANNER SETTINGS
# ─────────────────────────────────────────
EARNINGS_BLOCK_DAYS = 3           # Block alerts within N days of earnings
MARKET_OPEN = "09:30"             # Eastern time
MARKET_CLOSE = "16:00"            # Eastern time
INTRADAY_SCAN_INTERVAL_MIN = 5    # How often intraday scanner runs (minutes)

# ─────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────
WATCHLIST_PATH = "config/watchlist.json"
LOG_DIR = "logs/"
CACHE_DIR = ".cache/"

# ─────────────────────────────────────────
# ENVIRONMENT
# ─────────────────────────────────────────
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

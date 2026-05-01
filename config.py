"""
config.py — Central configuration for the Trading Assistant

v2 changes (SPY Options Focus):
  SCORE_ALERT_MINIMUM:     75 → 45  (was killing all alerts)
  SCORE_HIGH_CONVICTION:   90 → 68  (was unreachable)
  MIN_RISK_REWARD_RATIO:  2.0 → 1.5 (debit spreads have defined risk)
  VOLUME_SPIKE_MULTIPLIER: 1.5 → 1.2
  SPY_SPREAD_WIDTH:        $5 → $10 (SPY at $700 — $10 wide = better R/R)
  REGIME_FILTER_ENABLED:   False    (keep off until alerts confirmed flowing)
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────
# API KEYS
# ─────────────────────────────────────────
POLYGON_API_KEY    = os.getenv("POLYGON_API_KEY")
ALPACA_API_KEY     = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY  = os.getenv("ALPACA_SECRET_KEY")
ALPACA_BASE_URL    = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
DISCORD_BOT_TOKEN  = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID_STANDARD        = int(os.getenv("DISCORD_CHANNEL_ID_STANDARD", 0))
DISCORD_CHANNEL_ID_HIGH_CONVICTION = int(os.getenv("DISCORD_CHANNEL_ID_HIGH_CONVICTION", 0))

# ─────────────────────────────────────────
# PUSHOVER  (primary alert channel)
# ─────────────────────────────────────────
# Get these from https://pushover.net:
#   PUSHOVER_USER_KEY  → Your Account → User Key
#   PUSHOVER_API_TOKEN → Create Application → API Token
PUSHOVER_USER_KEY  = os.getenv("PUSHOVER_USER_KEY")
PUSHOVER_API_TOKEN = os.getenv("PUSHOVER_API_TOKEN")

# ─────────────────────────────────────────
# CLAUDE API  (for the alert detail chat page)
# ─────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# ─────────────────────────────────────────
# WEB SERVER  (FastAPI alert detail page)
# ─────────────────────────────────────────
WEB_SERVER_HOST = os.getenv("WEB_SERVER_HOST", "0.0.0.0")
WEB_SERVER_PORT = int(os.getenv("WEB_SERVER_PORT", "8000"))

# Base URL embedded in Pushover notification links.
# Set to your machine's LAN IP (or public hostname) so the link works on your phone.
# Example: http://192.168.1.100:8000   (LAN, simplest)
#          https://alerts.yourdomain.com  (if you expose it publicly)
# Leave blank to omit links from Pushover notifications.
DASHBOARD_BASE_URL = os.getenv("DASHBOARD_BASE_URL", "")

# ─────────────────────────────────────────
# SCORING THRESHOLDS
# ─────────────────────────────────────────
# Max raw score across all indicators = 86 pts
# After confluence bonus (x1.15) max = ~99
# 45/86 = 52% agreement → standard alert
# 68/86 = 79% agreement → high conviction
SCORE_ALERT_MINIMUM   = 45
SCORE_HIGH_CONVICTION = 68
SCORE_WATCHLIST       = 30

CONFLUENCE_BONUS_MULTIPLIER = 1.15

# ─────────────────────────────────────────
# SCORING LAYER MAX POINTS
# ─────────────────────────────────────────
SCORE_TREND_MAX  = 35
SCORE_SETUP_MAX  = 35
SCORE_VOLUME_MAX = 30

# ─────────────────────────────────────────
# RISK / REWARD
# ─────────────────────────────────────────
MIN_RISK_REWARD_RATIO = 1.5

# ─────────────────────────────────────────
# INDICATOR SETTINGS
# ─────────────────────────────────────────
MA_SHORT = 20
MA_MID   = 50
MA_LONG  = 200

DONCHIAN_PERIOD          = 20
DONCHIAN_INTRADAY_PERIOD = 20

VOLUME_SPIKE_MULTIPLIER = 1.2
VOLUME_LOOKBACK         = 20

RSI_PERIOD              = 14
RSI_DIVERGENCE_LOOKBACK = 5

# ─────────────────────────────────────────
# SPY OPTIONS SETTINGS
# ─────────────────────────────────────────

# Spread width for SPY debit spreads.
# SPY is currently ~$700 — $10 wide spreads give better R/R than $5.
# $5 wide: ~1.4:1 R/R  (tight, lower cost)
# $10 wide: ~1.4:1 R/R but larger absolute profit potential
# Adjust this if SPY price changes significantly (e.g. back to $500 → use $5)
SPY_SPREAD_WIDTH = 10

# Iron condor settings
IC_RANGE_THRESHOLD_PCT = 2.5   # SPY range over 10 days < 2.5% = IC candidate
IC_RSI_LOW  = 40
IC_RSI_HIGH = 60

# DTE recommendations
DTE_SWING_RECOMMENDED    = 21   # 21 DTE for swing SPY plays
DTE_INTRADAY_RECOMMENDED = 7    # 7 DTE for intraday SPY plays

# ─────────────────────────────────────────
# REGIME FILTER
# ─────────────────────────────────────────
# Keep False until alert flow is confirmed working
REGIME_FILTER_ENABLED = False

# ─────────────────────────────────────────
# TIMEFRAME SETTINGS
# ─────────────────────────────────────────
SWING_PRIMARY_TIMEFRAME      = "day"
SWING_SECONDARY_TIMEFRAME    = "4hour"
INTRADAY_PRIMARY_TIMEFRAME   = "15min"
INTRADAY_SECONDARY_TIMEFRAME = "5min"

# ─────────────────────────────────────────
# SCANNER SETTINGS
# ─────────────────────────────────────────
EARNINGS_BLOCK_DAYS        = 2
MARKET_OPEN                = "09:30"
MARKET_CLOSE               = "16:00"
INTRADAY_SCAN_INTERVAL_MIN = 5

# ─────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────
WATCHLIST_PATH = "config/watchlist.json"
LOG_DIR        = "logs/"
CACHE_DIR      = ".cache/"

# ─────────────────────────────────────────
# ENVIRONMENT
# ─────────────────────────────────────────
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
LOG_LEVEL   = os.getenv("LOG_LEVEL", "INFO")

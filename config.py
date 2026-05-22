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

import json
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

# Base URL used to build per-alert links inside Pushover notifications.
# Points at the Cloudflare-fronted host that serves the per-alert web app.
PUSHOVER_BASE_URL  = os.getenv("PUSHOVER_BASE_URL", "https://alerts.nexus-lab.work")

# ─────────────────────────────────────────
# CLAUDE API  (for the alert detail chat page)
# ─────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# ─────────────────────────────────────────
# LOCAL LLM FALLBACK  (nucbox Ollama)
# ─────────────────────────────────────────
# When the hosted Anthropic API fails or returns empty (e.g. monthly usage
# cap), the reflector / morning briefer fall back to the local Ollama stack
# so the self-learning loop keeps producing output. Endpoint + model are
# config-driven, never hardcoded.
OLLAMA_HOST  = os.getenv("OLLAMA_HOST",  "http://nexus-nucbox-k8-plus:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "phi4:14b")
OLLAMA_FALLBACK_ENABLED = os.getenv("OLLAMA_FALLBACK_ENABLED", "true").lower() == "true"

# ─────────────────────────────────────────
# STRATEGY STRUCTURE PREFERENCE
# ─────────────────────────────────────────
# Prefer debit spreads over credit spreads in trending regimes. The user
# dislikes credit spreads; the 5-year backtest (2026-05-20) confirms the
# switch is performance-neutral (+$40, +0.01 Sharpe) because credit spreads
# fire only ~3x in 5 years (IVR rarely >= 50). Iron condors are unaffected
# and remain the core edge. The bull-put extension gate + MIN_CREDIT_SPREAD_RR
# stay as protection if this is flipped off.
PREFER_DEBIT_OVER_CREDIT = os.getenv("PREFER_DEBIT_OVER_CREDIT", "true").lower() == "true"

# ─────────────────────────────────────────
# WEB SERVER  (FastAPI alert detail page)
# ─────────────────────────────────────────
# Bind to localhost only — Cloudflare Tunnel is the intended public ingress.
# Override to 0.0.0.0 in .env if you need LAN access during dev.
WEB_SERVER_HOST = os.getenv("WEB_SERVER_HOST", "127.0.0.1")
WEB_SERVER_PORT = int(os.getenv("WEB_SERVER_PORT", "8000"))

# PUSHOVER_BASE_URL (above) is the single source of truth for the public host
# embedded in Pushover notification links. Cloudflare Tunnel routes that
# hostname to http://localhost:WEB_SERVER_PORT where alerts/web_app.py runs.

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

# Reaction-history gate (uses data/earnings_history.py).
#   When True, gates.py asks EarningsHistory for the ticker's typical
#   post-earnings move BEFORE deciding whether the proximity block fires:
#     - "volatile" (>3.5% avg) → always blocked inside EARNINGS_BLOCK_DAYS+1
#     - "calm"      (<1.5% avg) → block window tightens to 0 days
#       (alerts allowed up to and including the day before earnings)
#     - "normal"    (1.5-3.5%) → unchanged proximity block
#   Off by default until validated on real watchlist data.
EARNINGS_REACTION_GATE_ENABLED = False
EARNINGS_CALM_WINDOW_DAYS      = 0    # block window override for calm reactors
MARKET_OPEN                = "09:30"
MARKET_CLOSE               = "16:00"
INTRADAY_SCAN_INTERVAL_MIN = 5

# News scanner / Polygon API rate limits.
# Polygon free tier = 5 req/sec; 1.5s between ticker fetches keeps us safe.
POLYGON_RATE_LIMIT_SEC = 1.5
POLYGON_TIMEOUT_SEC    = 10
NEWS_ARTICLES_LIMIT    = 10   # per-ticker fetch + market summary cap

# ─────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────
WATCHLIST_PATH = "config/watchlist.json"
LOG_DIR        = "logs/"
CACHE_DIR      = ".cache/"


# ─────────────────────────────────────────
# WATCHLIST LOADER
# ─────────────────────────────────────────
# Single source of truth for every scanner's ticker universe.
#
# When watchlist.json has "spy_focus": true, the swing / intraday /
# options_enabled universes all collapse to ["SPY"] — the bot scans and
# alerts on SPY only. The full multi-ticker lists stay in the file untouched;
# flip spy_focus to false to bring the other tickers back in one move.
SPY_FOCUS_KEYS = ("swing", "intraday", "options_enabled")


def load_watchlist() -> dict:
    """Load watchlist.json and apply the spy_focus collapse (see above)."""
    try:
        with open(WATCHLIST_PATH, "r") as f:
            wl = json.load(f)
    except Exception:
        # SPY is always a safe fallback universe.
        return {k: ["SPY"] for k in SPY_FOCUS_KEYS}

    if wl.get("spy_focus"):
        for key in SPY_FOCUS_KEYS:
            if key in wl:
                wl[key] = ["SPY"]
    return wl


# ─────────────────────────────────────────
# META-LABELING (secondary take/skip + conviction model)
# ─────────────────────────────────────────
# Inert until a trained model passes the walk-forward ship bar AND a human
# flips this to True. Flag off OR model missing => gate is a no-op.
META_LABEL_ENABLED  = False
META_PROB_THRESHOLD = 0.55                       # take if P(win) >= this
META_TIER_CUTOFFS   = {"med": 0.55, "high": 0.70}
META_MODEL_PATH     = "logs/learning/meta_model.joblib"

# ─────────────────────────────────────────
# ENVIRONMENT
# ─────────────────────────────────────────
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
LOG_LEVEL   = os.getenv("LOG_LEVEL", "INFO")

"""
config.py — Central configuration for the Trading Assistant

v2 changes (SPY Options Focus):
  SCORE_ALERT_MINIMUM:     75 → 45  (was killing all alerts)
  SCORE_HIGH_CONVICTION:   90 → 68  (was unreachable)
  MIN_RISK_REWARD_RATIO:  2.0 → 1.5 (debit spreads have defined risk)
  VOLUME_SPIKE_MULTIPLIER: 1.5 → 1.2
  SPY_SPREAD_WIDTH:        $5 → $10 (SPY at $700 — $10 wide = better R/R)
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

# Polygon "I:VIX" index data is NOT_AUTHORIZED on the current Starter plan
# (verified 2026-06-06), so VIXClient skips it and uses the free CBOE CSV.
# Flip to True via .env if the plan ever authorizes index aggregates.
VIX_USE_POLYGON    = os.getenv("VIX_USE_POLYGON", "false").lower() == "true"
ALPACA_BASE_URL    = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

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
# INTRADAY-TOUCH EXIT (backtest ship-bar floors)
# ─────────────────────────────────────────
# Binding floors for the default-2σ preset in backtests/intraday_touch_wf.py.
# Five other presets are hard-coded inside the harness itself (learning context;
# they print verdicts but do not auto-ship). See spec
# docs/superpowers/specs/2026-05-22-intraday-touch-exit-design.md §6.
INTRADAY_TOUCH_SHIP_MIN_DOLLAR = 25.0    # statistical floor ($/trade, ~2σ on ~230 OOS)
INTRADAY_TOUCH_SHIP_MIN_FRAC   = 0.10    # scale floor (improvement >= 10% of baseline)
INTRADAY_TOUCH_SHIP_MIN_ATTRIB = 0.15    # >=15% of OOS exits via target_intraday


# ─────────────────────────────────────────
# PER-SUB-STRATEGY EXIT RULES
# ─────────────────────────────────────────
# Foundation for the multi-strategy expansion (Phase 2 will wire these into a
# strategy-aware ExitManager). Three strategies × three DTE buckets = 9
# sub-strategies; each gets its own exit-rule tuple. Naming convention:
#   PROFIT_TARGET_PCT_{DTE_BUCKET}_{STRUCTURE}
#   STOP_PCT_{DTE_BUCKET}_{STRUCTURE}
#   FORCED_CLOSE_TIME_{DTE_BUCKET}_{STRUCTURE}  (HH:MM ET, for 0DTE)
#   FORCED_CLOSE_MINUTES_BEFORE_EXPIRY_{DTE_BUCKET}  (for 1-3DTE)
# Where STRUCTURE in {CALL (call_debit_spread), PUT (put_debit_spread), COND
# (iron_condor)}.

# 45 DTE — keep today's tuned values (no live behavior change in Phase 1).
PROFIT_TARGET_PCT_45DTE_CALL    = 0.70
PROFIT_TARGET_PCT_45DTE_PUT     = 0.70
PROFIT_TARGET_PCT_45DTE_COND    = 0.70
DTE_CLOSE_THRESHOLD_45DTE       = 21
# Experimental: None = no stop (current behavior). Hypothesis engine may
# propose a bounded value via TUNABLE_PARAMS to test if a hard stop helps.
STOP_PCT_45DTE                  = None

# 1-3 DTE — theta is faster, gamma is closer; smaller targets, real stops.
PROFIT_TARGET_PCT_1_3DTE_CALL   = 0.50
PROFIT_TARGET_PCT_1_3DTE_PUT    = 0.50
PROFIT_TARGET_PCT_1_3DTE_COND   = 0.50
STOP_PCT_1_3DTE_CALL            = 0.50
STOP_PCT_1_3DTE_PUT             = 0.50
CONDOR_SHORT_STRIKE_TOUCH_EXIT_1_3DTE       = True
FORCED_CLOSE_MINUTES_BEFORE_EXPIRY_1_3DTE   = 30

# 0 DTE — gamma is everything; never let it expire.
PROFIT_TARGET_PCT_0DTE_CALL     = 1.00       # 100% (credit doubled) for debits
PROFIT_TARGET_PCT_0DTE_PUT      = 1.00
PROFIT_TARGET_PCT_0DTE_COND     = 0.30       # smaller + faster for condors
STOP_PCT_0DTE_CALL              = 0.75
STOP_PCT_0DTE_PUT               = 0.75
CONDOR_SHORT_STRIKE_TOUCH_EXIT_0DTE = True
FORCED_CLOSE_TIME_0DTE_DEBIT    = "15:30"    # ET, HH:MM
FORCED_CLOSE_TIME_0DTE_CONDOR   = "15:00"    # ET — gamma into the bell

# ── Intraday time-exit model (2026-06-05) ───────────────────────────────────
# Global kill-switch: when False the live ExitManager skips ALL scratch/hard-close
# time rules (falls back to today's target/stop/forced-close behavior).
INTRADAY_TIME_EXIT_ENABLED = True

# Per-(strategy, dte_bucket) time-exit params. None until a walk-forward arm
# EARNS the combo (Task 7). Keyed "STRATEGY_BUCKET". Only 0DTE/1-3DTE are managed.
# scratch_theta is a fraction of max_profit: pnl below it at scratch_time => bail.
#
# DECISION 2026-06-06 — SHIPS INERT. The T7 walk-forward (2024-25, 251 deduped
# trades) cleared NO arm for ANY combo: no time-stop flipped a loser positive
# (best put_debit_spread 0DTE arm scratch@12:00 trimmed -$21.49 -> -$17.20/trade,
# still deeply negative), AND every arm FAILED the parity gate — the BS-off-spot
# live mark cannot reproduce the real-option-bar exits (agree <=0.85 < 0.90,
# mean pnl gap ~$27 >> $10; iron_condor 0DTE worse, agree 0.40-0.54). These stay
# empty by decision, not omission. Re-open only behind real/delayed intraday
# OPTION aggregates that close the parity gap (Starter plan has none today).
SCRATCH_TIME      = {}   # e.g. {"put_debit_spread_0DTE": "13:00"}
SCRATCH_THETA     = {}   # e.g. {"put_debit_spread_0DTE": 0.0}
HARD_CLOSE_TIME   = {}   # e.g. {"put_debit_spread_0DTE": "14:00"}

# Live/backtest parity gate (Task 6). B ships for a combo only if the BS-off-spot
# mark reproduces the real-mark exits on >= MIN_AGREE of trades AND the per-trade
# mean pnl gap between the two marks' arms is < MAX_PNL_GAP dollars.
EXIT_PARITY_MIN_AGREE   = 0.90
EXIT_PARITY_MAX_PNL_GAP = 10.0


# ─────────────────────────────────────────
# PHASE 3: INTRADAY ENTRY PIPELINE
# ─────────────────────────────────────────
# Wires intraday_scanner's high-conviction setups → paper_broker.execute_signal.
# Kill-switch for the intraday-scanner → paper_broker wiring. Default True
# (Phase 3's behavior change ships ON at merge); flip to False + commit to
# instantly disable the pipeline without untangling code.
INTRADAY_PAPER_BROKER_ENABLED = True

# Which conviction tier qualifies as an intraday entry. Configurable so we
# can widen later to include "standard" (45-67 score) without code change.
ENTRY_TIER_MINIMUM = "high"   # one of "high" / "standard"

# H2 DTE assignment: morning (< this ET time) → 0DTE; afternoon → 1-3DTE.
# Friday PM safeguard fires in the router regardless (no weekend exposure).
INTRADAY_DTE_MORNING_CUTOFF = "12:30"

# Ultra-conviction exception: setups with score ≥ this open BOTH 0DTE and
# 1-3DTE buckets (rare — empirically 1-2/week on high-conv setups).
ULTRA_CONVICTION_DOUBLE_DTE_SCORE = 85

# Option D position dedup: max entries per (strategy, dte_bucket) per day.
# After a position closes, a fresh setup can re-open up to this cap.
INTRADAY_PER_COMBO_DAILY_CAP = 2

# Per-(strategy, dte_bucket) exit-feasibility thresholds for dual-book routing.
# Entries clearing BOTH thresholds → disciplined book; else → learning book
# (the falsification sandbox).
#
# CALIBRATED 2026-06-02 from the full 2024-2025 walk-forward (real-priced,
# 16 windows). Finding: 0DTE is a structural loser even router-filtered
# (-$14.08/trade over 599 trades; total -$8,434) — it re-confirms the 0DTE
# shelving. 1-3DTE is the only non-losing bucket (+$9.46/trade) but tiny
# sample (49 trades/2yr). Policy (user, strict/honest): gate ALL 0DTE to the
# learning sandbox (prohibitive bar) where the falsificationist loop keeps
# probing for a regime in which 0DTE earns its keep; let 1-3DTE into the
# disciplined book (permissive). Revisit 0DTE when the sandbox shows OOS edge.
_PROHIBITIVE = 1e9   # no real 0DTE structure clears this target → always learning
INTRADAY_FEASIBILITY = {
    ("call_debit_spread", "0DTE"):   {"min_target_dollars": _PROHIBITIVE, "min_rr": 0.0},
    ("call_debit_spread", "1-3DTE"): {"min_target_dollars": 0.0,          "min_rr": 0.0},
    ("put_debit_spread",  "0DTE"):   {"min_target_dollars": _PROHIBITIVE, "min_rr": 0.0},
    ("put_debit_spread",  "1-3DTE"): {"min_target_dollars": 0.0,          "min_rr": 0.0},
    ("iron_condor",       "0DTE"):   {"min_target_dollars": _PROHIBITIVE, "min_rr": 0.0},
    ("iron_condor",       "1-3DTE"): {"min_target_dollars": 0.0,          "min_rr": 0.0},
}


# ─────────────────────────────────────────
# ENVIRONMENT
# ─────────────────────────────────────────
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
LOG_LEVEL   = os.getenv("LOG_LEVEL", "INFO")

# ─────────────────────────────────────────────────────────────
# Phase 4a — Learning Loop Hygiene
# ─────────────────────────────────────────────────────────────

# Item 3: KB confidence cap for single-day entries
KB_DAILY_CONFIDENCE_CAP = 0.7

# Item 4: evidence-citation tolerance for float matches (±0.1%)
KB_EVIDENCE_FLOAT_TOLERANCE_PCT = 0.1

# Item 5: anomaly triggers for reflector routing (Sonnet escalation)
REFLECTOR_ANOMALY_STOPS_MIN          = 2     # ≥N stop-outs today
REFLECTOR_ANOMALY_PRED_MISS_PCT      = 1.5   # |predicted - actual| as % of SPY
REFLECTOR_ANOMALY_NEW_SUBSTRATEGY    = True  # any sub-strategy fired 1st time
REFLECTOR_ANOMALY_REGIME_CHANGE      = True  # regime differs vs yesterday

# Item 6: regime-drift threshold for off_hours_learner
REGIME_DRIFT_THRESHOLD_PCT           = 10.0  # ≥N pts shift in 60d distribution
REGIME_DRIFT_RECENT_DAYS             = 60    # last-N trading days


# ─────────────────────────────────────────────────────────────
# US NYSE Market Holidays (C3 hotfix — 2026-05-25)
# ─────────────────────────────────────────────────────────────
# Hand-curated 2026 set. For future years, extend the set or
# adopt pandas_market_calendars / exchange_calendars.

from datetime import date as _date

US_MARKET_HOLIDAYS_2026 = {
    _date(2026, 1, 1),    # New Year's Day (Thu)
    _date(2026, 1, 19),   # MLK Day (Mon)
    _date(2026, 2, 16),   # Presidents' Day (Mon)
    _date(2026, 4, 3),    # Good Friday
    _date(2026, 5, 25),   # Memorial Day (Mon)
    _date(2026, 6, 19),   # Juneteenth (Fri)
    _date(2026, 7, 3),    # July 4 observed (July 4 = Sat) (Fri)
    _date(2026, 9, 7),    # Labor Day (Mon)
    _date(2026, 11, 26),  # Thanksgiving (Thu)
    _date(2026, 12, 25),  # Christmas (Fri)
}

US_MARKET_HOLIDAYS = US_MARKET_HOLIDAYS_2026  # alias for future-proofing


def is_trading_day(d) -> bool:
    """Return True iff the given date is a US equity-market trading day.

    Accepts datetime.date or datetime.datetime. Weekends and holidays
    are excluded. Half-days (e.g. July 3 early close, Black Friday)
    are STILL trading days — this function only gates full closures.
    """
    if hasattr(d, "date"):
        d = d.date()
    if d.weekday() >= 5:
        return False
    if d in US_MARKET_HOLIDAYS:
        return False
    return True
REGIME_DRIFT_PRIOR_DAYS              = 60    # prior-N trading days for comparison


# ─────────────────────────────────────────────────────────────
# Extension-gate shadow-test
# ─────────────────────────────────────────────────────────────
# On extension-skip days, paper-trade the bull play the gate refused
# (book="shadow") + score the directional counterfactual. The hypothesis
# engine proposes relaxing EXTENDED_TREND_MAX_PCT when the shadow beats
# the gate over SHADOW_MIN_DAYS at >= SHADOW_MIN_WINRATE.
SHADOW_TEST_ENABLED = True
SHADOW_MIN_DAYS     = 10
SHADOW_MIN_WINRATE  = 0.55

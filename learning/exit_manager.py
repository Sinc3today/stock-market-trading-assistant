"""
learning/exit_manager.py -- Mid-life exit rules for open [AUTO-PAPER] positions.

Runs daily (~16:08 ET, between OutcomeResolver at :05 and ExpiryResolver at
:10). For each open [AUTO-PAPER] trade not yet expired, marks the spread to a
Black-Scholes value (VIX as the IV proxy -- the project convention, since
"VIX IS SPY's 30-day IV") and applies, in order:

  1. Profit target -- close once >= PROFIT_TARGET_PCT of max profit is
     captured. Gives quicker wins.
  2. Time stop     -- close at <= DTE_CLOSE_THRESHOLD days to expiry if the
     target hasn't hit.
  3. No hard stop  -- losers are left to ride; ExpiryResolver (intrinsic at
     expiry) is the backstop. Validated 2026-05-20: a position can go ITM
     then recover, and a hard stop would lock the loss right before a bounce.

Same-day / intraday exits ARE allowed -- realism comes from the FILL MODEL,
not a hold lock. The close fills at the mark plus EXIT_SLIPPAGE applied in our
disfavor (the "nearest ask"), never the idealized mid. In practice a position
only clears the profit target on day 0 if the underlying moved hard that day
(the catalyst quick-win case), because with no time decay yet the mark sits
near entry.

Expiry stays with ExpiryResolver (no time premium left at expiry, so intrinsic
value is exact there). This module only handles the mid-life window.
"""

from __future__ import annotations

import math
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger

from journal.trade_recorder import TradeRecorder
from learning.paper_broker  import AUTO_TAG


# ── Tunables (registered in hypothesis_engine.TUNABLE_PARAMS) ─────────
PROFIT_TARGET_PCT    = 0.70   # close once this fraction of max profit is captured
DTE_CLOSE_THRESHOLD  = 21     # close when this many days (or fewer) to expiry
EXIT_SLIPPAGE        = 0.05   # per-share haircut applied against us on the fill

_OUTCOME_EMOJI = {"win": "✅", "loss": "❌", "breakeven": "➖"}


# ── Per-(strategy, dte_bucket) exit rules ───────────────────────────────────
# Phase 2b-3: ExitManager dispatches to the right rule by (strategy, dte_bucket).
# 45DTE values match the legacy globals exactly (PROFIT_TARGET_PCT=0.70,
# DTE_CLOSE_THRESHOLD=21) — that's the byte-identical parity contract.

# Map raw strategy strings (which include legacy names like "debit_spread") to
# the canonical structure key. The codebase historically used both
# "debit_spread" / "credit_spread" / "iron_condor" / "single_leg" in trade
# records and "call_debit_spread" / "put_debit_spread" / "iron_condor" in the
# new Phase-1 constants. We normalize here.
_STRUCTURE_KEY = {
    "call_debit_spread": "CALL",
    "put_debit_spread":  "PUT",
    "iron_condor":       "COND",
    # Legacy names — direction disambiguates between CALL/PUT debit
    "debit_spread":      "CALL",   # historical default; direction can refine if needed
    "credit_spread":     "CALL",
    "single_leg":        "CALL",
}


def _exit_rule_for(strategy: str | None, dte_bucket: str | None) -> dict:
    """Return the exit-rule dict for the given (strategy, dte_bucket).
    Untagged trades (dte_bucket=None) default to 45DTE rules — the only thing
    the bot has historically produced.

    Returns: {
        "profit_target_pct": float,
        "stop_pct": float | None,
        "dte_close_threshold": int,
        "condor_short_strike_touch": bool,
        "forced_close_time": str | None,   # HH:MM ET for 0DTE; None otherwise
        "forced_close_minutes_before_expiry": int | None,  # for 1-3DTE
    }
    """
    structure = _STRUCTURE_KEY.get(strategy or "", "CALL")
    bucket = dte_bucket or "45DTE"   # legacy untagged → 45DTE

    if bucket == "45DTE":
        # Look up the per-structure constant; all three currently equal 0.70.
        pt_pct = {
            "CALL": config.PROFIT_TARGET_PCT_45DTE_CALL,
            "PUT":  config.PROFIT_TARGET_PCT_45DTE_PUT,
            "COND": config.PROFIT_TARGET_PCT_45DTE_COND,
        }[structure]
        return {
            "profit_target_pct":   pt_pct,
            "stop_pct":            config.STOP_PCT_45DTE,    # None by default
            "dte_close_threshold": config.DTE_CLOSE_THRESHOLD_45DTE,
            "condor_short_strike_touch":          False,
            "forced_close_time":                   None,
            "forced_close_minutes_before_expiry":  None,
        }

    if bucket == "1-3DTE":
        pt_pct = {
            "CALL": config.PROFIT_TARGET_PCT_1_3DTE_CALL,
            "PUT":  config.PROFIT_TARGET_PCT_1_3DTE_PUT,
            "COND": config.PROFIT_TARGET_PCT_1_3DTE_COND,
        }[structure]
        stop_pct = (config.STOP_PCT_1_3DTE_CALL if structure == "CALL"
                    else config.STOP_PCT_1_3DTE_PUT if structure == "PUT"
                    else None)   # condors use strike-touch, not %-of-max-loss
        return {
            "profit_target_pct":   pt_pct,
            "stop_pct":            stop_pct,
            "dte_close_threshold": 0,    # 1-3DTE managed by forced-close, not DTE threshold
            "condor_short_strike_touch":          (structure == "COND"
                and config.CONDOR_SHORT_STRIKE_TOUCH_EXIT_1_3DTE),
            "forced_close_time":                   None,
            "forced_close_minutes_before_expiry":  config.FORCED_CLOSE_MINUTES_BEFORE_EXPIRY_1_3DTE,
        }

    if bucket == "0DTE":
        pt_pct = {
            "CALL": config.PROFIT_TARGET_PCT_0DTE_CALL,
            "PUT":  config.PROFIT_TARGET_PCT_0DTE_PUT,
            "COND": config.PROFIT_TARGET_PCT_0DTE_COND,
        }[structure]
        stop_pct = (config.STOP_PCT_0DTE_CALL if structure == "CALL"
                    else config.STOP_PCT_0DTE_PUT if structure == "PUT"
                    else None)
        forced_time = (config.FORCED_CLOSE_TIME_0DTE_CONDOR if structure == "COND"
                       else config.FORCED_CLOSE_TIME_0DTE_DEBIT)
        return {
            "profit_target_pct":   pt_pct,
            "stop_pct":            stop_pct,
            "dte_close_threshold": 0,
            "condor_short_strike_touch":          (structure == "COND"
                and config.CONDOR_SHORT_STRIKE_TOUCH_EXIT_0DTE),
            "forced_close_time":                   forced_time,
            "forced_close_minutes_before_expiry":  None,
        }

    # Unknown bucket — defensive default (treat as 45DTE).
    return _exit_rule_for(strategy, "45DTE")


# ── Black-Scholes (r = 0; VIX/100 as sigma) ──────────────────────────

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(opt_type: str, spot: float, strike: float, t_years: float,
             sigma: float) -> float:
    """
    Black-Scholes price of a single European option, r=0. Falls back to
    intrinsic value when there's no time or no vol left (T<=0 or sigma<=0),
    which is exactly the expiry limit.
    """
    otype = (opt_type or "").lower()
    if t_years <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        if otype == "call":
            return max(0.0, spot - strike)
        return max(0.0, strike - spot)
    vol_t = sigma * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (sigma * sigma / 2.0) * t_years) / vol_t
    d2 = d1 - vol_t
    if otype == "call":
        return spot * _norm_cdf(d1) - strike * _norm_cdf(d2)
    return strike * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def format_exit_message(closed: list[dict]) -> str:
    """One Pushover/Discord line per mid-life exit, plus a header."""
    if not closed:
        return ""
    lines = [f"**Paper exits closed ({len(closed)})**"]
    for c in closed:
        emoji = _OUTCOME_EMOJI.get(c.get("outcome", ""), "·")
        pnl   = c.get("pnl_dollars")
        pnl_s = f"${pnl:+,.0f}" if isinstance(pnl, (int, float)) else "—"
        lines.append(
            f"{emoji} `{c.get('trade_id','?')}` "
            f"{c.get('strategy','?')} → {c.get('reason','exit')} "
            f"@ ${c.get('exit_price',0):.2f} ({pnl_s})"
        )
    return "\n".join(lines)


class ExitManager:
    """Profit-target + time-stop exits for open [AUTO-PAPER] positions."""

    def __init__(
        self,
        polygon_client=None,
        vix_client=None,
        trade_recorder: TradeRecorder | None = None,
    ):
        self.polygon = polygon_client
        self.vix     = vix_client
        self.trades  = trade_recorder or TradeRecorder()

    # ── MAIN ──────────────────────────────────────────

    def manage_open(
        self,
        today:        date  | None = None,
        spy_close:    float | None = None,
        vix:          float | None = None,
        dte_buckets:  list[str] | None = None,
    ) -> list[dict]:
        """
        Walk open [AUTO-PAPER] trades and close any that hit the profit
        target or the time stop. Returns a list of closed-trade dicts.
        Expiry-day positions are left for ExpiryResolver.

        dte_buckets: if provided, only process positions whose dte_bucket
        field matches one of the given values (e.g. ["45DTE"] for the
        daily 16:08 cron, ["0DTE", "1-3DTE"] for the intraday cron).
        Untagged legacy trades are treated as "45DTE" for filtering purposes.
        If None (default), all open positions are processed (back-compat).
        """
        today = today or date.today()

        # Compute the filtered open-position list FIRST so we can short-circuit
        # before any Polygon/VIX network call when there's nothing to manage
        # (e.g., the every-5-min intraday cron when no 0DTE/1-3DTE positions
        # exist on the books — its 78 fires/day would otherwise leak ~156
        # market-data calls into Polygon's quota for no reason).
        open_auto = [
            t for t in self.trades.get_all_trades()
            if t.get("outcome") == "open" and AUTO_TAG in (t.get("notes_entry") or "")
        ]
        if dte_buckets is not None:
            buckets_set = set(dte_buckets)
            open_auto = [
                t for t in open_auto
                if (t.get("dte_bucket") or "45DTE") in buckets_set
            ]
        if not open_auto:
            return []

        # Now fetch market data — only when we actually have positions to mark.
        if spy_close is None:
            spy_close = self._fetch_spy_close()
        if spy_close is None:
            logger.warning("ExitManager: no SPY close available, skipping")
            return []
        if vix is None:
            vix = self._fetch_vix()
        if vix is None:
            logger.warning("ExitManager: no VIX available, skipping mid-life marks")
            return []

        closed: list[dict] = []
        for t in open_auto:
            decision = self._evaluate(t, spy_close, vix, today)
            if decision is None:
                continue
            exit_px, reason = decision
            note = (
                f"[AUTO-EXIT {today.isoformat()}] {reason} "
                f"SPY=${spy_close:.2f} VIX={vix:.1f} fill=${exit_px:.2f}"
            )
            try:
                self.trades.log_exit(trade_id=t["trade_id"], exit_price=exit_px, notes=note)
            except Exception as e:
                logger.exception(f"ExitManager: log_exit failed for {t.get('trade_id')}: {e}")
                continue
            after = self.trades.get_trade_by_id(t["trade_id"]) or {}
            closed.append({
                "trade_id":    t["trade_id"],
                "strategy":    t.get("strategy") or t.get("trade_type") or "single_leg",
                "reason":      reason,
                "exit_price":  round(exit_px, 2),
                "pnl_dollars": after.get("pnl_dollars"),
                "outcome":     after.get("outcome"),
            })

        if closed:
            logger.info(f"ExitManager: closed {len(closed)} paper trade(s) mid-life")
        return closed

    # ── DECISION ──────────────────────────────────────

    def _evaluate(self, trade: dict, spy: float, vix: float,
                  today: date) -> tuple[float, str] | None:
        """
        Return (exit_price, reason) if the position should close today,
        else None. exit_price already includes the slippage haircut.

        Phase 2b-3: dispatches the exit rule by (strategy, dte_bucket).
        Untagged trades (legacy, no dte_bucket field) default to 45DTE rules.
        45DTE behavior is byte-identical to the original implementation
        (PROFIT_TARGET_PCT_45DTE_*=0.70, DTE_CLOSE_THRESHOLD_45DTE=21).
        """
        legs     = trade.get("legs") or []
        strategy = (trade.get("strategy") or trade.get("trade_type") or "single_leg").lower()
        exp      = self._nearest_expiration(legs)
        if exp is None:
            return None
        dte = (exp - today).days
        if dte < 0:
            return None   # already expired -> ExpiryResolver's job

        # Look up the per-sub-strategy rule.
        rule = _exit_rule_for(strategy, trade.get("dte_bucket"))

        exit_px = self._mark_exit_price(strategy, legs, spy, vix, today, dte)
        pnl     = self._pnl_dollars(strategy, trade.get("entry_price"), exit_px,
                                    trade.get("size", 1))
        max_profit = self._numeric(trade.get("max_profit"))
        max_loss   = self._numeric(trade.get("max_loss"))

        # 1. Profit target — gated by per-sub-strategy threshold.
        if max_profit and max_profit > 0 and pnl is not None:
            if pnl / max_profit >= rule["profit_target_pct"]:
                return exit_px, f"profit target {rule['profit_target_pct']:.0%}"

        # 2. Hard stop — Phase 2b experimental for 45DTE; configured for 0DTE/1-3DTE.
        if rule["stop_pct"] is not None and max_loss and max_loss > 0 and pnl is not None:
            if pnl <= -rule["stop_pct"] * max_loss:
                return exit_px, f"stop {rule['stop_pct']:.0%} of max loss"

        # 3. Time stop — close N DTE before expiry.
        if dte <= rule["dte_close_threshold"]:
            return exit_px, f"time stop {dte}DTE"

        return None

    # ── PRICING ───────────────────────────────────────

    def _mark_exit_price(self, strategy: str, legs: list[dict], spy: float,
                         vix: float, today: date, dte: int) -> float:
        """
        Black-Scholes mark of the spread, converted to the price you'd
        actually transact at to CLOSE, with EXIT_SLIPPAGE applied against us:
          - credit_spread / iron_condor : cost to buy back  (slippage adds)
          - debit_spread / single_leg   : proceeds to sell   (slippage subtracts)
        """
        sigma   = vix / 100.0
        t_years = max(dte, 0) / 365.0
        long_val = short_val = 0.0
        for leg in legs:
            strike = leg.get("strike")
            otype  = (leg.get("type") or leg.get("option_type") or "").lower()
            action = (leg.get("action") or "").upper()
            if strike is None or otype not in ("call", "put"):
                continue
            try:
                k = float(strike)
            except (TypeError, ValueError):
                continue
            price = bs_price(otype, spy, k, t_years, sigma)
            if action == "BUY":
                long_val += price
            elif action == "SELL":
                short_val += price

        s = (strategy or "").lower()
        if s in ("credit_spread", "iron_condor"):
            cost = max(0.0, short_val - long_val)
            return round(cost + EXIT_SLIPPAGE, 2)        # pay more to close
        proceeds = max(0.0, long_val - short_val)
        return round(max(0.0, proceeds - EXIT_SLIPPAGE), 2)  # receive less

    @staticmethod
    def _pnl_dollars(strategy: str, entry, exit_price: float, size) -> float | None:
        """Mirror TradeRecorder._calculate_pnl so 'profit captured' matches
        what a real close would book."""
        try:
            entry = float(entry)
            size  = float(size or 1)
        except (TypeError, ValueError):
            return None
        s = (strategy or "").lower()
        if s in ("credit_spread", "iron_condor"):
            pps = entry - exit_price
        else:  # debit_spread, single_leg
            pps = exit_price - entry
        return round(pps * size * 100, 2)

    @staticmethod
    def _nearest_expiration(legs: list[dict]) -> date | None:
        exps = []
        for leg in legs:
            raw = leg.get("expiration") or leg.get("expiry")
            if not raw:
                continue
            try:
                exps.append(datetime.fromisoformat(str(raw)[:10]).date())
            except ValueError:
                continue
        return min(exps) if exps else None

    @staticmethod
    def _numeric(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # ── DATA ──────────────────────────────────────────

    def _fetch_spy_close(self) -> float | None:
        if self.polygon is None:
            logger.warning("ExitManager: polygon_client not injected")
            return None
        try:
            df = self.polygon.get_bars(
                "SPY", timeframe=config.SWING_PRIMARY_TIMEFRAME, limit=3, days_back=3,
            )
            if df is None or len(df) == 0:
                return None
            return float(df["close"].iloc[-1])
        except Exception as e:
            logger.warning(f"ExitManager SPY fetch failed: {e}")
            return None

    def _fetch_vix(self) -> float | None:
        if self.vix is None:
            return None
        try:
            return float(self.vix.get_current())
        except Exception as e:
            logger.warning(f"ExitManager VIX fetch failed: {e}")
            return None

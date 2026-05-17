"""
learning/expiry_resolver.py -- Close [AUTO-PAPER] positions at expiry.

Runs 16:10 ET (Mon-Fri), 5 min after OutcomeResolver. For each open
[AUTO-PAPER] trade whose nearest leg expiration is on or before today,
compute the intrinsic value of the position at today's SPY close and
call TradeRecorder.log_exit().

Pricing is intrinsic-value-at-expiry only (no time value left at expiry,
by definition). For multi-leg spreads:

    long_val  = sum( max(0, spy - K) for long calls  )
              + sum( max(0, K - spy) for long puts   )
    short_val = sum( max(0, spy - K) for short calls )
              + sum( max(0, K - spy) for short puts  )

Exit price per strategy (matches TradeRecorder._calculate_pnl):

    debit_spread / single_leg  exit_price = long_val - short_val
                                            (what you sold the position for)
    credit_spread / iron_condor exit_price = max(0, short_val - long_val)
                                            (what you pay to buy it back)

This module never edits source — it only updates the journal.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger

from journal.trade_recorder import TradeRecorder
from learning.paper_broker  import AUTO_TAG


_OUTCOME_EMOJI = {"win": "✅", "loss": "❌", "breakeven": "➖"}


def format_expiry_message(closed: list[dict]) -> str:
    """One Pushover/Discord line per closed paper position, plus a header."""
    if not closed:
        return ""
    lines = [f"**Paper expiries closed ({len(closed)})**"]
    for c in closed:
        emoji = _OUTCOME_EMOJI.get(c.get("outcome", ""), "·")
        pnl   = c.get("pnl_dollars")
        pnl_s = f"${pnl:+,.0f}" if isinstance(pnl, (int, float)) else "—"
        lines.append(
            f"{emoji} `{c.get('trade_id','?')}` "
            f"{c.get('strategy','?')} @ exp {c.get('expiration','?')} "
            f"→ exit ${c.get('exit_price',0):.2f} ({pnl_s})"
        )
    return "\n".join(lines)


class ExpiryResolver:
    """Closes [AUTO-PAPER] positions at expiry using intrinsic value."""

    def __init__(
        self,
        polygon_client=None,
        trade_recorder: TradeRecorder | None = None,
    ):
        self.polygon = polygon_client
        self.trades  = trade_recorder or TradeRecorder()

    # ── MAIN ──────────────────────────────────────────

    def resolve_expired(
        self,
        today:     date  | None = None,
        spy_close: float | None = None,
    ) -> list[dict]:
        """
        Walk open [AUTO-PAPER] trades, close any whose nearest leg expiry
        is <= today. Returns a list of {trade_id, strategy, expiration,
        exit_price, pnl_dollars, outcome} for trades that were closed.
        """
        today = today or date.today()
        if spy_close is None:
            spy_close = self._fetch_spy_close()
        if spy_close is None:
            logger.warning("ExpiryResolver: no SPY close available, skipping")
            return []

        all_trades = self.trades.get_all_trades()
        open_auto  = [
            t for t in all_trades
            if t.get("outcome") == "open"
            and AUTO_TAG in (t.get("notes_entry") or "")
        ]
        if not open_auto:
            logger.info("ExpiryResolver: no open AUTO-PAPER trades")
            return []

        closed: list[dict] = []
        for t in open_auto:
            exp = self._nearest_expiration(t.get("legs") or [])
            if exp is None:
                continue
            if exp > today:
                continue   # not expired yet

            strategy = t.get("strategy") or t.get("trade_type") or "single_leg"
            exit_px  = self._exit_price(strategy, t.get("legs") or [], spy_close)

            note = (
                f"[AUTO-EXPIRY {today.isoformat()}] "
                f"SPY=${spy_close:.2f} intrinsic exit=${exit_px:.2f}"
            )
            try:
                self.trades.log_exit(
                    trade_id   = t["trade_id"],
                    exit_price = exit_px,
                    notes      = note,
                )
            except Exception as e:
                logger.exception(f"ExpiryResolver: log_exit failed for {t.get('trade_id')}: {e}")
                continue

            after = self.trades.get_trade_by_id(t["trade_id"]) or {}
            closed.append({
                "trade_id":    t["trade_id"],
                "strategy":    strategy,
                "expiration":  exp.isoformat(),
                "exit_price":  round(exit_px, 2),
                "pnl_dollars": after.get("pnl_dollars"),
                "outcome":     after.get("outcome"),
            })

        if closed:
            logger.info(f"ExpiryResolver: closed {len(closed)} expired paper trade(s)")
        return closed

    # ── PRICING ───────────────────────────────────────

    @staticmethod
    def _intrinsic(legs: list[dict], spy: float) -> tuple[float, float]:
        long_val = short_val = 0.0
        for leg in legs:
            action = (leg.get("action") or "").upper()
            otype  = (leg.get("type") or leg.get("option_type") or "").lower()
            strike = leg.get("strike")
            if strike is None:
                continue
            try:
                k = float(strike)
            except (TypeError, ValueError):
                continue
            if otype == "call":
                v = max(0.0, spy - k)
            elif otype == "put":
                v = max(0.0, k - spy)
            else:
                continue
            if action == "BUY":
                long_val += v
            elif action == "SELL":
                short_val += v
        return long_val, short_val

    @classmethod
    def _exit_price(cls, strategy: str, legs: list[dict], spy: float) -> float:
        long_val, short_val = cls._intrinsic(legs, spy)
        s = (strategy or "").lower()
        if s in ("credit_spread", "iron_condor"):
            return round(max(0.0, short_val - long_val), 2)
        # debit_spread, single_leg, stock-like fall back to long - short
        return round(max(0.0, long_val - short_val), 2)

    @staticmethod
    def _nearest_expiration(legs: list[dict]) -> date | None:
        """Earliest leg expiration (as date), or None if no leg has one."""
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

    # ── DATA ──────────────────────────────────────────

    def _fetch_spy_close(self) -> float | None:
        if self.polygon is None:
            logger.warning("ExpiryResolver: polygon_client not injected")
            return None
        try:
            df = self.polygon.get_bars(
                "SPY",
                timeframe = config.SWING_PRIMARY_TIMEFRAME,
                limit     = 3,
                days_back = 3,
            )
            if df is None or len(df) == 0:
                return None
            return float(df["close"].iloc[-1])
        except Exception as e:
            logger.warning(f"ExpiryResolver SPY fetch failed: {e}")
            return None

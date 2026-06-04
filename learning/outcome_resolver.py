"""
learning/outcome_resolver.py -- Score predictions and snapshot positions at EOD.

Runs at 16:05 ET (5 min after close). For today's unresolved prediction:

  1. Fetch SPY EOD close via PolygonClient.
  2. Compare to the entry SPY price recorded by paper_broker.
  3. Mark the prediction correct / wrong / partial:
        bullish + close > entry          -> correct
        bullish + close < entry          -> wrong
        bearish + close < entry          -> correct
        bearish + close > entry          -> wrong
        neutral + |move| < 0.25%         -> correct
        neutral + |move| >= 0.25%        -> wrong
        skip                              -> skip (not scored)
  4. Snapshot any open [AUTO-PAPER] positions: append a daily MTM note to
     the trade record. Multi-day spreads stay open; the snapshot is what
     lets the reflector see the path.

Tradeable AUTO-PAPER positions held for the regime's recommended DTE
are left open here -- a future expiry-resolver can close them.
"""

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger

from learning.predictions    import PredictionLog
from learning.paper_broker   import is_auto_paper
from journal.trade_recorder  import TradeRecorder


NEUTRAL_TOLERANCE_PCT = 0.25   # |%| inside this = condor still "correct"


# ── NOTIFICATION FORMATTER ────────────────────────────

_OUTCOME_EMOJI = {
    "correct": "✅",
    "wrong":   "❌",
    "partial": "◐",
    "skip":    "—",
}


def format_resolved_message(prediction: dict) -> str:
    """
    One-line+ summary of a resolved prediction, suitable for Pushover/Discord.

    Skip days produce a quiet "no trade today" line so the daily heartbeat
    is consistent — the reflector still gets the richer 19:01 write-up.
    """
    date_str  = prediction.get("date", "?")
    direction = prediction.get("direction", "?")
    outcome   = prediction.get("outcome") or "unresolved"
    emoji     = _OUTCOME_EMOJI.get(outcome, "·")

    if outcome == "skip" or not prediction.get("tradeable"):
        return f"**Prediction {date_str}** — {emoji} skip day (no directional call)"

    entry = prediction.get("entry_spy")
    close = prediction.get("actual_close")
    move  = prediction.get("actual_move_pct")
    move_str = f"{move:+.2f}%" if isinstance(move, (int, float)) else "?"
    entry_str = f"${entry:.2f}" if isinstance(entry, (int, float)) else "?"
    close_str = f"${close:.2f}" if isinstance(close, (int, float)) else "?"

    return (
        f"**Prediction {date_str}** — {emoji} {outcome.upper()} ({direction})\n"
        f"SPY {entry_str} → {close_str} ({move_str})"
    )


class OutcomeResolver:
    """Closes the day's learning loop. Idempotent."""

    def __init__(
        self,
        polygon_client       = None,
        trade_recorder:      TradeRecorder | None = None,
        prediction_log:      PredictionLog | None = None,
    ):
        self.polygon     = polygon_client
        self.trades      = trade_recorder  or TradeRecorder()
        self.predictions = prediction_log  or PredictionLog()

    # ── MAIN ──────────────────────────────────────────

    def resolve_today(self, today: date | None = None) -> dict:
        today        = today or date.today()
        today_str    = today.isoformat()
        prediction   = self.predictions.get(today_str)

        if prediction is None:
            logger.info(f"OutcomeResolver: no prediction for {today_str}")
            return {"date": today_str, "resolved": False, "reason": "no prediction"}

        if prediction.get("resolved"):
            logger.info(f"OutcomeResolver: {today_str} already resolved")
            return {"date": today_str, "resolved": True, "reason": "already resolved"}

        # Fetch the close up front: open [AUTO-PAPER] positions need a daily
        # MTM mark regardless of whether *today's* prediction was a trade or
        # a skip. Previously this only ran on tradeable days, so the open
        # position's MTM logged "no SPY data" on every skip day.
        spy_close = self._fetch_spy_close()

        if not prediction.get("tradeable"):
            # Store the real close (not 0.0) so the skip can be scored:
            # mark_resolved computes actual_move_pct vs the baseline
            # entry_spy, and skip_quality() reads that to decide whether
            # standing down was the right call.
            self.predictions.mark_resolved(today_str, spy_close or 0.0, "skip", today_str)
            self._snapshot_open_paper_trades(today_str, spy_close)
            self._stamp_shadow_directional(today_str, spy_close)
            return {"date": today_str, "resolved": True, "outcome": "skip"}

        if spy_close is None:
            logger.warning(f"OutcomeResolver: could not fetch SPY close for {today_str}")
            return {"date": today_str, "resolved": False, "reason": "no SPY data"}

        direction = prediction.get("direction", "neutral")
        entry     = prediction.get("entry_spy")
        outcome   = self._score(direction, entry, spy_close)

        self.predictions.mark_resolved(today_str, spy_close, outcome, today_str)
        self._snapshot_open_paper_trades(today_str, spy_close)

        return {
            "date":      today_str,
            "resolved":  True,
            "outcome":   outcome,
            "spy_close": spy_close,
            "direction": direction,
        }

    # ── SCORING ───────────────────────────────────────

    @staticmethod
    def _score(direction: str, entry: float | None, close: float) -> str:
        if entry is None:
            return "partial"   # we have a close but no entry to compare to
        move_pct = (close - entry) / entry * 100
        if direction == "bullish":
            return "correct" if move_pct > 0 else "wrong"
        if direction == "bearish":
            return "correct" if move_pct < 0 else "wrong"
        if direction == "neutral":
            return "correct" if abs(move_pct) < NEUTRAL_TOLERANCE_PCT else "wrong"
        return "partial"

    # ── DATA ──────────────────────────────────────────

    def _fetch_spy_close(self) -> float | None:
        if self.polygon is None:
            logger.warning("OutcomeResolver: polygon_client not injected")
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
            logger.warning(f"OutcomeResolver SPY fetch failed: {e}")
            return None

    # ── MTM SNAPSHOT ──────────────────────────────────

    def _snapshot_open_paper_trades(self, today_str: str, spy_close: float | None):
        """
        For each open [AUTO-PAPER] trade, append a one-line MTM note to
        the trade's notes_entry so the reflector can see the path.
        Doesn't close the trade -- expiry-based exit will be added later.
        """
        all_trades = self.trades.get_all_trades()
        open_auto  = [
            t for t in all_trades
            if t.get("outcome") == "open" and is_auto_paper(t)
        ]
        if not open_auto:
            return

        line = f"\n[MTM {today_str}] SPY close ${spy_close}" if spy_close else f"\n[MTM {today_str}] no SPY data"
        for t in all_trades:
            if t in open_auto:
                t["notes_entry"] = (t.get("notes_entry") or "") + line
        self.trades._save(all_trades)  # idempotent overwrite
        logger.info(f"OutcomeResolver: snapshotted {len(open_auto)} open paper trade(s)")

    # ── SHADOW DIRECTIONAL STAMP ──────────────────────

    def _stamp_shadow_directional(self, today_str: str, spy_close: float | None) -> None:
        """On an extension-skip day, score the shadow trade's bullish
        counterfactual (SPY close vs entry_spy). Reuses _score; does not touch
        the real prediction's skip status."""
        if spy_close is None:
            return
        trades = self.trades.get_all_trades()
        changed = False
        for t in trades:
            if t.get("book") != "shadow":
                continue
            if t.get("shadow_directional"):
                continue
            if (t.get("entry_date") or "")[:10] != today_str:
                continue
            entry_spy = t.get("entry_spy")
            if not isinstance(entry_spy, (int, float)):
                continue
            t["shadow_directional"] = self._score("bullish", entry_spy, spy_close)
            changed = True
        if changed:
            self.trades._save(trades)

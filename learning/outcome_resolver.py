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
from datetime import date, datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger

from learning.predictions    import PredictionLog
from learning.paper_broker   import AUTO_TAG
from journal.trade_recorder  import TradeRecorder


NEUTRAL_TOLERANCE_PCT = 0.25   # |%| inside this = condor still "correct"


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

        if not prediction.get("tradeable"):
            self.predictions.mark_resolved(today_str, 0.0, "skip", today_str)
            self._snapshot_open_paper_trades(today_str, spy_close=None)
            return {"date": today_str, "resolved": True, "outcome": "skip"}

        spy_close = self._fetch_spy_close()
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
            if t.get("outcome") == "open" and AUTO_TAG in (t.get("notes_entry") or "")
        ]
        if not open_auto:
            return

        line = f"\n[MTM {today_str}] SPY close ${spy_close}" if spy_close else f"\n[MTM {today_str}] no SPY data"
        for t in all_trades:
            if t in open_auto:
                t["notes_entry"] = (t.get("notes_entry") or "") + line
        self.trades._save(all_trades)  # idempotent overwrite
        logger.info(f"OutcomeResolver: snapshotted {len(open_auto)} open paper trade(s)")

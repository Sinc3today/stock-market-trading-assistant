"""
journal/trade_logger.py — Alert and Trade Logger
Logs every alert that fires to a JSON file.
Also supports manually marking trade outcomes (win/loss/open).

Usage:
    from journal.trade_logger import TradeLogger
    tl = TradeLogger()
    tl.log_alert(alert)
    tl.mark_outcome("AAPL", "2024-01-15 09:32 AM EST", "win", exit_price=182.00)
"""

import json
import os
from datetime import datetime
from loguru import logger
import config


class TradeLogger:
    """
    Persists every alert and trade outcome to JSON log files.
    Supports outcome tracking for performance analysis.
    """

    def __init__(self):
        os.makedirs(config.LOG_DIR, exist_ok=True)
        self.alert_log_path     = os.path.join(config.LOG_DIR, "alerts.json")
        self.watchlist_log_path = os.path.join(config.LOG_DIR, "watchlist_signals.json")

    # ─────────────────────────────────────────
    # LOGGING
    # ─────────────────────────────────────────

    def log_alert(self, alert: dict):
        """
        Append a fired alert to the log.
        Adds outcome fields so they can be filled in later.
        """
        entry = {
            **alert,
            "outcome":    None,    # "win", "loss", "breakeven", "open"
            "exit_price": None,
            "exit_date":  None,
            "pnl_pct":    None,
            "notes":      "",
        }
        alerts = self._load(self.alert_log_path)
        alerts.append(entry)
        self._save(self.alert_log_path, alerts, limit=1000)
        logger.info(
            f"Alert logged: {alert.get('ticker')} | "
            f"Score: {alert.get('final_score')} | "
            f"Dir: {alert.get('direction')}"
        )

    def log_watchlist_entry(self, alert: dict):
        """Log a watchlist-tier signal (below alert threshold)."""
        entries = self._load(self.watchlist_log_path)
        entries.append(alert)
        self._save(self.watchlist_log_path, entries, limit=500)

    # ─────────────────────────────────────────
    # OUTCOME TRACKING
    # ─────────────────────────────────────────

    def mark_outcome(
        self,
        ticker:      str,
        timestamp:   str,
        outcome:     str,
        exit_price:  float = None,
        notes:       str   = "",
    ) -> bool:
        """
        Mark the outcome of a trade after it closes.

        Args:
            ticker:     Stock symbol e.g. "AAPL"
            timestamp:  Alert timestamp to identify the trade
            outcome:    "win", "loss", "breakeven", or "open"
            exit_price: Price at which you exited
            notes:      Any notes about the trade

        Returns:
            True if trade was found and updated, False otherwise.
        """
        alerts  = self._load(self.alert_log_path)
        updated = False

        for alert in alerts:
            if alert.get("ticker") == ticker and \
               timestamp in alert.get("timestamp", ""):

                alert["outcome"]    = outcome
                alert["exit_price"] = exit_price
                alert["exit_date"]  = datetime.now().strftime("%Y-%m-%d %I:%M %p EST")
                alert["notes"]      = notes

                # Calculate P&L percentage
                if exit_price and alert.get("entry"):
                    entry = alert["entry"]
                    if alert.get("direction") == "BULLISH":
                        pnl = ((exit_price - entry) / entry) * 100
                    else:
                        pnl = ((entry - exit_price) / entry) * 100
                    alert["pnl_pct"] = round(pnl, 2)

                updated = True
                break

        if updated:
            self._save(self.alert_log_path, alerts)
            logger.info(f"Outcome marked: {ticker} → {outcome} | Exit: ${exit_price}")
        else:
            logger.warning(f"Trade not found: {ticker} @ {timestamp}")

        return updated

    # ─────────────────────────────────────────
    # RETRIEVAL
    # ─────────────────────────────────────────

    def get_alerts(self, limit: int = 100) -> list:
        """Return the most recent N alerts."""
        return self._load(self.alert_log_path)[-limit:]

    def get_today_alerts(self) -> list:
        today = datetime.now().strftime("%Y-%m-%d")
        return [
            a for a in self._load(self.alert_log_path)
            if today in a.get("timestamp", "")
        ]

    def get_closed_trades(self) -> list:
        """Return all trades with a recorded outcome."""
        return [
            a for a in self._load(self.alert_log_path)
            if a.get("outcome") and a["outcome"] != "open"
        ]

    def get_open_trades(self) -> list:
        """Return all alerts with no outcome yet."""
        return [
            a for a in self._load(self.alert_log_path)
            if not a.get("outcome")
        ]

    # ─────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────

    def _load(self, path: str) -> list:
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            return []

    def _save(self, path: str, data: list, limit: int = 1000):
        data = data[-limit:]
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
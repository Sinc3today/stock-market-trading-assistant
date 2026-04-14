"""
journal/trade_recorder.py — Trade Recorder
Logs actual trades taken. Supports stocks, single leg options,
debit spreads, credit spreads, and iron condors.

Usage:
    from journal.trade_recorder import TradeRecorder
    tr = TradeRecorder()
    trade_id = tr.log_entry(ticker="AAPL", entry_price=170.0, size=10)
    tr.log_exit(trade_id, exit_price=182.0)
"""

import json
import os
import uuid
from datetime import datetime
from loguru import logger
import pytz
import config


# ── Valid strategy types ──────────────────────────────────────
STRATEGY_TYPES = [
    "stock",
    "single_leg",
    "debit_spread",
    "credit_spread",
    "iron_condor",
]


class TradeRecorder:
    """
    Records actual trades including multi-leg options strategies.
    P&L calculated correctly for each strategy type.
    """

    def __init__(self):
        os.makedirs(config.LOG_DIR, exist_ok=True)
        self.trades_path = os.path.join(config.LOG_DIR, "trades.json")

    # ─────────────────────────────────────────
    # ENTRY LOGGING
    # ─────────────────────────────────────────

    def log_entry(
        self,
        ticker:          str,
        entry_price:     float,       # For stocks: share price. For spreads: net debit/credit
        size:            float,       # Shares for stock, contracts for options
        trade_type:      str  = "stock",
        strategy:        str  = None, # debit_spread, credit_spread, iron_condor, single_leg
        direction:       str  = "bullish",
        mode:            str  = "swing",
        legs:            list = None, # List of leg dicts for options spreads
        max_profit:      float = None,
        max_loss:        float = None,
        alert_timestamp: str  = None,
        alert_score:     int  = None,
        notes:           str  = "",
    ) -> str:
        """
        Log a trade entry.

        For stocks:
            entry_price = share price
            size        = number of shares

        For single leg options:
            entry_price = premium paid per share
            size        = number of contracts
            legs        = [{"action": "BUY", "option_type": "CALL",
                            "strike": 170, "expiry": "2024-03-15"}]

        For debit/credit spreads:
            entry_price = net debit paid OR net credit received per share
            size        = number of spreads (contracts)
            legs        = [leg1_dict, leg2_dict]
            max_profit  = max profit per contract (optional, auto-calculated if blank)
            max_loss    = max loss per contract (optional)

        For iron condors:
            entry_price = total net credit received per share
            size        = number of condors
            legs        = [put_long, put_short, call_short, call_long]

        Returns:
            trade_id — 8 character ID e.g. "A3F9B2C1"
        """
        eastern  = pytz.timezone("US/Eastern")
        now_est  = datetime.now(eastern).strftime("%Y-%m-%d %I:%M %p EST")
        trade_id = str(uuid.uuid4())[:8].upper()

        # Derive strategy from trade_type if not explicitly set
        if strategy is None:
            strategy = trade_type if trade_type in STRATEGY_TYPES else "stock"

        # Calculate entry value
        entry_value = self._calculate_entry_value(
            strategy, entry_price, size, max_loss
        )

        trade = {
            # Identity
            "trade_id":    trade_id,
            "ticker":      ticker.upper(),
            "trade_type":  trade_type,
            "strategy":    strategy,
            "direction":   direction.upper(),
            "mode":        mode,

            # Entry
            "entry_price":  round(entry_price, 2),
            "size":         size,
            "entry_date":   now_est,
            "entry_value":  entry_value,

            # Options spread fields
            "legs":        legs or [],
            "max_profit":  max_profit,
            "max_loss":    max_loss,

            # Alert link
            "alert_timestamp": alert_timestamp,
            "alert_score":     alert_score,

            # Exit (filled later)
            "exit_price":  None,
            "exit_date":   None,
            "exit_value":  None,

            # Outcome (filled later)
            "outcome":     "open",
            "pnl_dollars": None,
            "pnl_pct":     None,
            "pnl_per_contract": None,

            # Notes
            "notes_entry": notes,
            "notes_exit":  "",
            "lessons":     "",
        }

        trades = self._load()
        trades.append(trade)
        self._save(trades)

        logger.info(
            f"Trade entry logged: [{trade_id}] "
            f"{ticker.upper()} {strategy} {direction} | "
            f"Entry: ${entry_price} × {size}"
        )
        return trade_id

    # ─────────────────────────────────────────
    # EXIT LOGGING
    # ─────────────────────────────────────────

    def log_exit(
        self,
        trade_id:   str,
        exit_price: float,    # For spreads: net credit received to close (debit spread)
                              #              or net debit paid to close (credit spread)
        notes:      str = "",
    ) -> bool:
        """
        Log the exit of an open trade.
        P&L calculated based on strategy type.

        For debit spreads:
            exit_price = what you sold the spread for
            P&L = (exit_price - entry_price) * size * 100

        For credit spreads:
            exit_price = what you paid to close (buy back)
            P&L = (entry_price - exit_price) * size * 100

        For iron condors:
            exit_price = what you paid to close
            P&L = (entry_price - exit_price) * size * 100
        """
        trades  = self._load()
        updated = False

        for trade in trades:
            if trade.get("trade_id") != trade_id.upper():
                continue

            eastern = pytz.timezone("US/Eastern")
            now_est = datetime.now(eastern).strftime("%Y-%m-%d %I:%M %p EST")

            entry    = trade["entry_price"]
            size     = trade["size"]
            strategy = trade.get("strategy", "stock")
            direction = trade["direction"]

            pnl_per_share, pnl_dollars = self._calculate_pnl(
                strategy, direction, entry, exit_price, size
            )

            # P&L percentage
            cost_basis = self._get_cost_basis(strategy, entry, size, trade.get("max_loss"))
            pnl_pct    = round((pnl_dollars / abs(cost_basis)) * 100, 2) \
                         if cost_basis else 0

            # Outcome
            if pnl_dollars > 0.01:
                outcome = "win"
            elif pnl_dollars < -0.01:
                outcome = "loss"
            else:
                outcome = "breakeven"

            trade["exit_price"]       = round(exit_price, 2)
            trade["exit_date"]        = now_est
            trade["exit_value"]       = round(exit_price * size * (1 if strategy == "stock" else 100), 2)
            trade["pnl_dollars"]      = round(pnl_dollars, 2)
            trade["pnl_pct"]          = pnl_pct
            trade["pnl_per_contract"] = round(pnl_per_share * 100, 2) \
                                        if strategy != "stock" else None
            trade["outcome"]          = outcome
            trade["notes_exit"]       = notes
            updated = True

            logger.info(
                f"Trade exit logged: [{trade_id}] "
                f"{trade['ticker']} {strategy} → {outcome} | "
                f"P&L: ${round(pnl_dollars, 2)} ({pnl_pct}%)"
            )
            break

        if updated:
            self._save(trades)
        else:
            logger.warning(f"Trade not found: {trade_id}")

        return updated

    # ─────────────────────────────────────────
    # P&L CALCULATIONS
    # ─────────────────────────────────────────

    def _calculate_entry_value(
        self, strategy: str, entry_price: float,
        size: float, max_loss: float = None
    ) -> float:
        """What did this trade cost to enter?"""
        if strategy == "stock":
            return round(entry_price * size, 2)
        elif strategy in ("debit_spread", "single_leg"):
            # Debit paid × contracts × 100 shares
            return round(entry_price * size * 100, 2)
        elif strategy in ("credit_spread", "iron_condor"):
            # Credit received (negative cost)
            return round(-entry_price * size * 100, 2)
        return round(entry_price * size, 2)

    def _calculate_pnl(
        self,
        strategy:    str,
        direction:   str,
        entry:       float,
        exit_price:  float,
        size:        float,
    ) -> tuple[float, float]:
        """
        Returns (pnl_per_share, total_pnl_dollars)
        """
        if strategy == "stock":
            if direction == "BULLISH":
                pps = exit_price - entry
            else:
                pps = entry - exit_price
            return pps, round(pps * size, 2)

        elif strategy == "debit_spread":
            # Bought spread for entry_price, sold for exit_price
            pps = exit_price - entry
            return pps, round(pps * size * 100, 2)

        elif strategy in ("credit_spread", "iron_condor"):
            # Sold spread for entry_price, bought back for exit_price
            pps = entry - exit_price
            return pps, round(pps * size * 100, 2)

        elif strategy == "single_leg":
            # Bought option for entry_price, sold for exit_price
            pps = exit_price - entry
            return pps, round(pps * size * 100, 2)

        return 0, 0

    def _get_cost_basis(
        self, strategy: str, entry: float,
        size: float, max_loss: float = None
    ) -> float:
        """Cost basis for P&L % calculation."""
        if strategy == "stock":
            return entry * size
        elif strategy in ("debit_spread", "single_leg"):
            return entry * size * 100
        elif strategy in ("credit_spread", "iron_condor"):
            return max_loss or (entry * size * 100)
        return entry * size

    # ─────────────────────────────────────────
    # RETRIEVAL
    # ─────────────────────────────────────────

    def get_all_trades(self)         -> list: return self._load()
    def get_open_trades(self)        -> list:
        return [t for t in self._load() if t.get("outcome") == "open"]
    def get_closed_trades(self)      -> list:
        return [t for t in self._load() if t.get("outcome") != "open"]
    def get_trade_by_id(self, tid)   -> dict | None:
        for t in self._load():
            if t.get("trade_id") == tid.upper(): return t
        return None
    def get_trades_for_ticker(self, ticker: str) -> list:
        return [t for t in self._load() if t.get("ticker") == ticker.upper()]

    def get_summary_stats(self) -> dict:
        all_trades = self._load()
        closed     = [t for t in all_trades if t.get("outcome") != "open"]
        open_t     = [t for t in all_trades if t.get("outcome") == "open"]

        if not closed:
            return {"total": len(all_trades), "open": len(open_t),
                    "closed": 0, "wins": 0, "losses": 0,
                    "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl_pct": 0.0}

        wins     = [t for t in closed if t.get("outcome") == "win"]
        pnls     = [t["pnl_dollars"] for t in closed if t.get("pnl_dollars") is not None]
        pnl_pcts = [t["pnl_pct"]     for t in closed if t.get("pnl_pct")     is not None]

        return {
            "total":       len(all_trades),
            "open":        len(open_t),
            "closed":      len(closed),
            "wins":        len(wins),
            "losses":      len(closed) - len(wins),
            "win_rate":    round((len(wins) / len(closed)) * 100, 1),
            "total_pnl":   round(sum(pnls), 2),
            "avg_pnl_pct": round(sum(pnl_pcts) / len(pnl_pcts), 2) if pnl_pcts else 0.0,
        }

    def import_from_robinhood(self) -> list:
        """Placeholder — Robinhood import added in future session."""
        logger.info("Robinhood import not yet implemented")
        return []

    # ─────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────

    def _load(self) -> list:
        if not os.path.exists(self.trades_path):
            return []
        try:
            with open(self.trades_path, "r") as f:
                return json.load(f)
        except Exception:
            return []

    def _save(self, trades: list):
        with open(self.trades_path, "w") as f:
            json.dump(trades, f, indent=2)
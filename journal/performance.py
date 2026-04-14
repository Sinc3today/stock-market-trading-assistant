"""
journal/performance.py — Performance Analytics
Calculates win rate, average R/R, score accuracy, and per-tier stats.

Usage:
    from journal.performance import PerformanceTracker
    pt = PerformanceTracker()
    stats = pt.calculate()
"""

from loguru import logger
from journal.trade_logger import TradeLogger


class PerformanceTracker:
    """
    Analyzes closed trade history to measure system performance.
    Answers the key question: is the scoring model actually predicting winners?
    """

    def __init__(self):
        self.trade_logger = TradeLogger()

    def calculate(self) -> dict:
        """
        Calculate full performance statistics.

        Returns dict with:
            total_alerts        — all alerts fired
            total_closed        — trades with recorded outcome
            total_open          — trades still open
            win_rate            — % of closed trades that were wins
            avg_pnl_pct         — average P&L % across closed trades
            avg_rr_ratio        — average R/R ratio of fired alerts
            avg_score           — average confidence score
            by_tier             — stats broken down by tier
            by_direction        — stats broken down by bullish/bearish
            by_mode             — stats broken down by swing/intraday
            score_accuracy      — does higher score = higher win rate?
        """
        all_alerts   = self.trade_logger.get_alerts(limit=1000)
        closed       = self.trade_logger.get_closed_trades()
        open_trades  = self.trade_logger.get_open_trades()

        if not all_alerts:
            return self._empty_stats()

        stats = {
            "total_alerts":  len(all_alerts),
            "total_closed":  len(closed),
            "total_open":    len(open_trades),
            "win_rate":      self._win_rate(closed),
            "avg_pnl_pct":   self._avg_pnl(closed),
            "avg_rr_ratio":  self._avg_rr(all_alerts),
            "avg_score":     self._avg_score(all_alerts),
            "by_tier":       self._stats_by_tier(closed),
            "by_direction":  self._stats_by_direction(closed),
            "by_mode":       self._stats_by_mode(closed),
            "score_accuracy":self._score_accuracy(closed),
        }

        logger.info(
            f"Performance calculated — "
            f"Win rate: {stats['win_rate']}% | "
            f"Avg PnL: {stats['avg_pnl_pct']}% | "
            f"Closed: {stats['total_closed']}"
        )
        return stats

    # ─────────────────────────────────────────
    # CALCULATIONS
    # ─────────────────────────────────────────

    def _win_rate(self, closed: list) -> float:
        if not closed:
            return 0.0
        wins = sum(1 for t in closed if t.get("outcome") == "win")
        return round((wins / len(closed)) * 100, 1)

    def _avg_pnl(self, closed: list) -> float:
        pnls = [t["pnl_pct"] for t in closed if t.get("pnl_pct") is not None]
        return round(sum(pnls) / len(pnls), 2) if pnls else 0.0

    def _avg_rr(self, alerts: list) -> float:
        rrs = [t["rr_ratio"] for t in alerts if t.get("rr_ratio")]
        return round(sum(rrs) / len(rrs), 2) if rrs else 0.0

    def _avg_score(self, alerts: list) -> float:
        scores = [t["final_score"] for t in alerts if t.get("final_score")]
        return round(sum(scores) / len(scores), 1) if scores else 0.0

    def _stats_by_tier(self, closed: list) -> dict:
        """Win rate and count broken down by alert tier."""
        tiers = {}
        for trade in closed:
            tier = trade.get("tier", "unknown")
            if tier not in tiers:
                tiers[tier] = {"total": 0, "wins": 0, "pnls": []}
            tiers[tier]["total"] += 1
            if trade.get("outcome") == "win":
                tiers[tier]["wins"] += 1
            if trade.get("pnl_pct") is not None:
                tiers[tier]["pnls"].append(trade["pnl_pct"])

        result = {}
        for tier, data in tiers.items():
            result[tier] = {
                "total":    data["total"],
                "wins":     data["wins"],
                "win_rate": round((data["wins"] / data["total"]) * 100, 1)
                            if data["total"] > 0 else 0,
                "avg_pnl":  round(sum(data["pnls"]) / len(data["pnls"]), 2)
                            if data["pnls"] else 0,
            }
        return result

    def _stats_by_direction(self, closed: list) -> dict:
        """Win rate broken down by bullish vs bearish."""
        directions = {}
        for trade in closed:
            direction = trade.get("direction", "UNKNOWN")
            if direction not in directions:
                directions[direction] = {"total": 0, "wins": 0}
            directions[direction]["total"] += 1
            if trade.get("outcome") == "win":
                directions[direction]["wins"] += 1

        result = {}
        for direction, data in directions.items():
            result[direction] = {
                "total":    data["total"],
                "wins":     data["wins"],
                "win_rate": round((data["wins"] / data["total"]) * 100, 1)
                            if data["total"] > 0 else 0,
            }
        return result

    def _stats_by_mode(self, closed: list) -> dict:
        """Win rate broken down by swing vs intraday."""
        modes = {}
        for trade in closed:
            mode = trade.get("mode", "Unknown").lower()
            if mode not in modes:
                modes[mode] = {"total": 0, "wins": 0, "pnls": []}
            modes[mode]["total"] += 1
            if trade.get("outcome") == "win":
                modes[mode]["wins"] += 1
            if trade.get("pnl_pct") is not None:
                modes[mode]["pnls"].append(trade["pnl_pct"])

        result = {}
        for mode, data in modes.items():
            result[mode] = {
                "total":    data["total"],
                "wins":     data["wins"],
                "win_rate": round((data["wins"] / data["total"]) * 100, 1)
                            if data["total"] > 0 else 0,
                "avg_pnl":  round(sum(data["pnls"]) / len(data["pnls"]), 2)
                            if data["pnls"] else 0,
            }
        return result

    def _score_accuracy(self, closed: list) -> dict:
        """
        Does a higher score actually correlate with more wins?
        Buckets trades by score range and shows win rate per bucket.
        This is how we validate the scoring model over time.
        """
        buckets = {
            "75-79": {"total": 0, "wins": 0},
            "80-84": {"total": 0, "wins": 0},
            "85-89": {"total": 0, "wins": 0},
            "90-94": {"total": 0, "wins": 0},
            "95-100":{"total": 0, "wins": 0},
        }

        for trade in closed:
            score = trade.get("final_score", 0)
            if score < 75:
                continue
            bucket = (
                "75-79"  if score < 80 else
                "80-84"  if score < 85 else
                "85-89"  if score < 90 else
                "90-94"  if score < 95 else
                "95-100"
            )
            buckets[bucket]["total"] += 1
            if trade.get("outcome") == "win":
                buckets[bucket]["wins"] += 1

        result = {}
        for bucket, data in buckets.items():
            if data["total"] > 0:
                result[bucket] = {
                    "total":    data["total"],
                    "wins":     data["wins"],
                    "win_rate": round((data["wins"] / data["total"]) * 100, 1),
                }
        return result

    def _empty_stats(self) -> dict:
        return {
            "total_alerts": 0, "total_closed": 0, "total_open": 0,
            "win_rate": 0.0, "avg_pnl_pct": 0.0,
            "avg_rr_ratio": 0.0, "avg_score": 0.0,
            "by_tier": {}, "by_direction": {},
            "by_mode": {}, "score_accuracy": {},
        }
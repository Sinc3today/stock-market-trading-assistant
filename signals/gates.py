"""
signals/gates.py — Alert Gate Filters
Hard rules that suppress alerts regardless of score.

Gates (ALL must pass to fire an alert):
    1. Score >= minimum threshold
    2. Risk/Reward >= 2:1
    3. Not within 3 days of earnings
    4. Direction is not neutral

Usage:
    from signals.gates import AlertGates
    gates = AlertGates()
    passed, reason = gates.check(score_result, ticker, entry, stop, target)
"""

from datetime import datetime, timedelta
from loguru import logger
from polygon import RESTClient
import config


class AlertGates:
    """
    Applies all hard gate filters before an alert is allowed to fire.
    Any gate failure suppresses the alert entirely.
    """

    def __init__(self):
        if config.POLYGON_API_KEY:
            self.polygon = RESTClient(api_key=config.POLYGON_API_KEY)
        else:
            self.polygon = None

    def check(
        self,
        score_result: dict,
        ticker:       str,
        entry:        float,
        stop:         float,
        target:       float,
    ) -> tuple[bool, list[str], dict]:
        """
        Run all gate checks.

        Args:
            score_result: Output from SignalScorer.score()
            ticker:       Stock symbol
            entry:        Proposed entry price
            stop:         Stop loss price
            target:       Profit target price

        Returns:
            tuple of:
                passed       — True if ALL gates pass
                failures     — list of failed gate reasons (empty if passed)
                gate_data    — dict with computed values (R/R ratio, etc.)
        """
        failures = []
        gate_data = {}

        # ── Gate 1: Minimum score ────────────────────────────────
        score = score_result.get("final_score", 0)
        if score < config.SCORE_ALERT_MINIMUM:
            failures.append(
                f"Score too low: {score} (need {config.SCORE_ALERT_MINIMUM})"
            )

        # ── Gate 2: Direction not neutral ────────────────────────
        direction = score_result.get("direction", "neutral")
        if direction == "neutral":
            failures.append("Direction is neutral — no clear trade bias")

        # ── Gate 3: Risk/Reward ratio ────────────────────────────
        rr_ratio, rr_valid, rr_msg = self._check_risk_reward(
            direction, entry, stop, target
        )
        gate_data["rr_ratio"] = rr_ratio
        if not rr_valid:
            failures.append(rr_msg)

        # ── Gate 4: Earnings proximity ───────────────────────────
        near_earnings, earnings_msg, earnings_date = self._check_earnings(ticker)
        gate_data["earnings_date"] = earnings_date
        if near_earnings:
            failures.append(earnings_msg)

        passed = len(failures) == 0

        if passed:
            logger.info(f"✅ All gates passed for {ticker} — R/R: {rr_ratio:.2f}:1")
        else:
            logger.info(f"🚫 Gates failed for {ticker}: {failures}")

        return passed, failures, gate_data

    # ─────────────────────────────────────────
    # GATE IMPLEMENTATIONS
    # ─────────────────────────────────────────

    def _check_risk_reward(
        self,
        direction: str,
        entry:     float,
        stop:      float,
        target:    float,
    ) -> tuple[float, bool, str]:
        """
        Calculate R/R ratio and check it meets minimum threshold.

        For bullish trades: target must be above entry, stop below entry.
        For bearish trades: target must be below entry, stop above entry.
        """
        try:
            if direction == "bullish":
                risk   = entry - stop
                reward = target - entry
            else:
                risk   = stop - entry
                reward = entry - target

            if risk <= 0:
                return 0.0, False, f"Invalid stop placement (risk = {risk:.2f})"

            rr_ratio = round(reward / risk, 2)

            if rr_ratio >= config.MIN_RISK_REWARD_RATIO:
                return rr_ratio, True, f"R/R {rr_ratio:.2f}:1 ✅"
            else:
                return rr_ratio, False, (
                    f"R/R too low: {rr_ratio:.2f}:1 "
                    f"(need {config.MIN_RISK_REWARD_RATIO}:1)"
                )

        except Exception as e:
            logger.error(f"R/R calculation error: {e}")
            return 0.0, False, f"R/R calculation failed: {e}"

    def _check_earnings(
        self, ticker: str
    ) -> tuple[bool, str, str | None]:
        """
        Check if earnings are within the block window.
        Returns (is_blocked, message, earnings_date_str).

        Uses Polygon ticker details when available.
        Falls back to 'unknown' gracefully if API fails.
        """
        if self.polygon is None:
            return False, "Earnings check skipped (no API)", None

        try:
            details = self.polygon.get_ticker_details(ticker)
            # Polygon doesn't always have next earnings — handle gracefully
            earnings_date = getattr(details, "next_earnings_date", None)

            if earnings_date is None:
                logger.debug(f"No earnings date found for {ticker}")
                return False, "No earnings date available", None

            # Parse and compare
            if isinstance(earnings_date, str):
                earnings_dt = datetime.strptime(earnings_date, "%Y-%m-%d")
            else:
                earnings_dt = earnings_date

            today = datetime.now()
            days_until = (earnings_dt - today).days

            if abs(days_until) <= config.EARNINGS_BLOCK_DAYS:
                msg = (
                    f"Earnings within {config.EARNINGS_BLOCK_DAYS} days "
                    f"({earnings_date}) — alert suppressed"
                )
                return True, msg, str(earnings_date)
            else:
                return False, f"Earnings clear ({earnings_date})", str(earnings_date)

        except Exception as e:
            # Don't block the alert just because earnings lookup failed
            logger.warning(f"Earnings check failed for {ticker}: {e}")
            return False, "Earnings check unavailable", None
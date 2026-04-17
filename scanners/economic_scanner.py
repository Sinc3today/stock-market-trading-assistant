"""
scanners/economic_scanner.py — Economic Data Scanner
Monitors FRED for new economic releases and fires alerts.

Runs:
    With morning briefing  — adds economic context
    After major releases   — fires immediate Discord alert
    With EOD briefing      — summarizes day's economic events

High impact releases that trigger immediate alerts:
    CPI, NFP, Fed Rate, GDP, PCE, Unemployment
"""

import os
import sys
import requests
from datetime import datetime
from loguru import logger
import pytz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import config
from data.fred_client import FREDClient, TRACKED_SERIES, HIGH_IMPACT_SERIES

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL   = "claude-sonnet-4-20250514"


class EconomicScanner:
    """
    Fetches economic data from FRED and synthesizes
    market impact analysis using Claude AI.
    """

    def __init__(self):
        self.client  = FREDClient()
        self.eastern = pytz.timezone("US/Eastern")

    # ─────────────────────────────────────────
    # MORNING ECONOMIC CONTEXT
    # ─────────────────────────────────────────

    def get_morning_context(self) -> dict:
        """
        Called by morning briefing.
        Returns current economic snapshot + AI interpretation.
        """
        logger.info("📊 Fetching economic context for morning briefing...")
        snapshot = self.client.get_economic_snapshot()
        analysis = self._analyze_with_ai(
            snapshot,
            context="morning",
            question="What is the current economic environment and what should a swing/day trader watch for today?"
        )
        return {
            "snapshot":  snapshot,
            "analysis":  analysis,
            "formatted": self._format_for_briefing(snapshot, analysis),
        }

    # ─────────────────────────────────────────
    # RECENT RELEASE SCANNER
    # ─────────────────────────────────────────

    def scan_for_new_releases(self, days_back: int = 1) -> list:
        """
        Check for economic reports released in the last N days.
        Returns list of release dicts with AI analysis.
        High impact releases include Discord alert message.
        """
        released = self.client.get_recent_releases(days_back=days_back)
        results  = []

        for release in released:
            series_id = release["series_id"]
            impact    = release.get("impact", "MEDIUM")

            # Build AI analysis for each release
            analysis = self._analyze_release_with_ai(release)

            result = {
                **release,
                "ai_analysis":     analysis,
                "is_high_impact":  impact == "HIGH",
                "discord_alert":   self._format_discord_alert(release, analysis)
                                   if impact == "HIGH" else None,
            }
            results.append(result)

            if impact == "HIGH":
                logger.info(
                    f"🚨 HIGH IMPACT RELEASE: {release['name']} = "
                    f"{release['current_value']} (was {release['previous_value']})"
                )

        return results

    # ─────────────────────────────────────────
    # POST TO DISCORD
    # ─────────────────────────────────────────

    def post_economic_alert(self, release: dict):
        """
        Post a high-impact economic alert to Discord.
        Uses same pattern as news_scanner.
        """
        if not release.get("discord_alert"):
            return

        try:
            from alerts.discord_bot import get_bot_loop, bot

            channel_id = getattr(config, "DISCORD_CHANNEL_ID_NEWS", 0) \
                         or config.DISCORD_CHANNEL_ID_STANDARD

            message = release["discord_alert"]

            async def _send():
                channel = bot.get_channel(channel_id)
                if channel:
                    await channel.send(message)
                    logger.info(f"Economic alert posted: {release['name']}")

            import asyncio
            loop = get_bot_loop()
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(_send(), loop)
            else:
                logger.warning("Bot loop not ready — economic alert not posted")

        except Exception as e:
            logger.error(f"Economic alert post error: {type(e).__name__}")

    # ─────────────────────────────────────────
    # AI ANALYSIS
    # ─────────────────────────────────────────

    def _analyze_with_ai(
        self,
        snapshot: dict,
        context:  str,
        question: str,
    ) -> str:
        """AI interpretation of the full economic snapshot."""
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return "AI analysis unavailable — set ANTHROPIC_API_KEY"

        summary = snapshot.get("summary", "No data available")

        prompt = f"""You are a trading assistant analyzing current economic conditions.
The trader uses technical signals (MA/Donchian/Volume/CVD/RSI) and trades 
stocks, ETFs, and options spreads.

CURRENT ECONOMIC DATA:
{summary}

Context: {context} briefing
Question: {question}

Provide a concise analysis (under 200 words) covering:
1. Overall economic tone (expansionary/contractionary/uncertain)
2. Key indicator to watch today and why
3. How current conditions affect your watchlist stocks (tech/growth focus)
4. Any indicator that could override technical signals today

Be direct and actionable. No generic disclaimers."""

        try:
            headers = {
                "Content-Type":      "application/json",
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
            }
            payload = {
                "model":      CLAUDE_MODEL,
                "max_tokens": 400,
                "messages":   [{"role": "user", "content": prompt}],
            }
            resp = requests.post(CLAUDE_API_URL, headers=headers,
                                 json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return "".join(
                b.get("text", "") for b in data.get("content", [])
                if b.get("type") == "text"
            )
        except Exception as e:
            logger.error(f"Economic AI analysis error: {type(e).__name__}")
            return "AI analysis unavailable"

    def _analyze_release_with_ai(self, release: dict) -> str:
        """AI analysis of a specific economic release."""
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return "AI analysis unavailable"

        curr   = release["current_value"]
        prev   = release["previous_value"]
        change = release.get("change")
        chg_str = f" (change: {change:+.3f})" if change else ""

        prompt = f"""You are a trading assistant analyzing a fresh economic release.

RELEASE: {release['name']} ({release['series_id']})
Current:  {curr} {release['unit']}
Previous: {prev} {release['unit']}{chg_str}
Date:     {release['current_date']}
Description: {release['description']}

The trader uses technical signals and trades stocks, ETFs, and options spreads.
Watchlist typically includes tech stocks (AAPL, MSFT, NVDA) and ETFs (SPY, QQQ).

Provide analysis under 150 words covering:
1. Is this beat, miss, or inline with typical trends?
2. Immediate market impact (bullish/bearish/neutral for stocks)
3. Which positions or sectors are most affected
4. Should this override a technical buy signal today?

Be direct. No disclaimers."""

        try:
            headers = {
                "Content-Type":      "application/json",
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
            }
            payload = {
                "model":      CLAUDE_MODEL,
                "max_tokens": 300,
                "messages":   [{"role": "user", "content": prompt}],
            }
            resp = requests.post(CLAUDE_API_URL, headers=headers,
                                 json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return "".join(
                b.get("text", "") for b in data.get("content", [])
                if b.get("type") == "text"
            )
        except Exception as e:
            logger.error(f"Release AI analysis error: {type(e).__name__}")
            return "AI analysis unavailable"

    # ─────────────────────────────────────────
    # FORMATTING
    # ─────────────────────────────────────────

    def _format_for_briefing(self, snapshot: dict, analysis: str) -> str:
        """Format economic data for inclusion in news briefings."""
        lines = [
            "\n🏛️ **ECONOMIC CONDITIONS**",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]

        # Key indicators
        for series_id, data in snapshot.get("high_impact", {}).items():
            val   = data["current_value"]
            date  = data["current_date"]
            chg   = f" ({data['change']:+.3f})" if data.get("change") else ""
            lines.append(
                f"{data['emoji']} **{data['short']}**: "
                f"{val} {data['unit']}{chg} _{date}_"
            )

        # Recent releases
        recent = snapshot.get("recent_releases", [])
        if recent:
            lines.append(f"\n📅 **Recent Releases** (last 7 days):")
            for r in recent[:3]:
                lines.append(
                    f"  • **{r['name']}**: {r['current_value']} "
                    f"(prev: {r['previous_value']}) — {r['current_date']}"
                )

        # AI analysis
        if analysis and "unavailable" not in analysis.lower():
            lines.append(f"\n🤖 **Economic Analysis:**\n{analysis}")

        return "\n".join(lines)

    def _format_discord_alert(self, release: dict, analysis: str) -> str:
        """Format a high-impact release as a Discord alert card."""
        emoji  = release.get("emoji", "📊")
        name   = release["name"]
        curr   = release["current_value"]
        prev   = release["previous_value"]
        unit   = release["unit"]
        date   = release["current_date"]
        change = release.get("change")
        dir_emoji = "📈" if release.get("direction") == "up" else \
                    "📉" if release.get("direction") == "down" else "➡️"

        chg_str = f"{change:+.3f} {unit}" if change else "—"

        lines = [
            f"🚨 **ECONOMIC RELEASE — {name.upper()}**",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"{emoji} **{name}**",
            f"  Current:  **{curr} {unit}**",
            f"  Previous: {prev} {unit}",
            f"  Change:   {dir_emoji} {chg_str}",
            f"  Date:     {date}",
        ]

        if analysis and "unavailable" not in analysis.lower():
            lines.append(f"\n🤖 **Market Impact:**\n{analysis}")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

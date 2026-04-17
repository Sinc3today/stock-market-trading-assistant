"""
scanners/news_scanner.py — News Scanner + AI Briefing
Fetches news for watchlist tickers via Polygon and synthesizes
with Claude AI into actionable briefings.

Schedule:
    7:45 AM EST  Morning briefing
    12:00 PM EST Midday update
    3:45 PM EST  End of day wrap
"""

import json
import os
import sys
import time
import requests
from datetime import datetime, timedelta
from loguru import logger
import pytz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import config

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL   = "claude-sonnet-4-20250514"

LOOKBACK_HOURS = {
    "morning": 14,
    "midday":  5,
    "eod":     8,
}


class NewsScanner:

    def __init__(self):
        self.eastern        = pytz.timezone("US/Eastern")
        self._news_log_path = os.path.join(config.LOG_DIR, "news_briefings.json")
        os.makedirs(config.LOG_DIR, exist_ok=True)

    # ─────────────────────────────────────────
    # MAIN RUN
    # ─────────────────────────────────────────

    def run(self, briefing_type: str = "morning", post_to_discord: bool = True) -> dict:
        now_est = datetime.now(self.eastern).strftime("%I:%M %p EST")
        logger.info(f"📰 {briefing_type.upper()} news briefing starting at {now_est}")

        watchlist   = self._load_watchlist()
        all_tickers = list(set(
            watchlist.get("swing", []) +
            watchlist.get("intraday", [])
        ))

        if not all_tickers:
            logger.warning("Watchlist empty — news scan skipped")
            return {}

        hours_back  = LOOKBACK_HOURS.get(briefing_type, 8)
        ticker_news = {}

        # Fetch news per watchlist ticker with delay
        for ticker in all_tickers:
            time.sleep(1.5)  # Respect free tier rate limit
            articles = self._fetch_ticker_news(ticker, hours_back)
            if articles:
                ticker_news[ticker] = articles
                logger.info(f"News: {len(articles)} articles for {ticker}")
            else:
                logger.debug(f"No recent news for {ticker}")

        # Market-wide news — only fetch tickers NOT already in watchlist
        market_news = self._fetch_market_news(
            hours_back, already_fetched=list(ticker_news.keys())
        )

        # Build briefing
        briefing = self._build_briefing(
            briefing_type, ticker_news, market_news, all_tickers
        )

        # AI synthesis
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            briefing["ai_synthesis"] = self._synthesize_with_ai(
                briefing_type, ticker_news, market_news
            )
        else:
            briefing["ai_synthesis"] = "AI synthesis unavailable — set ANTHROPIC_API_KEY"

        # Format discord message
        discord_msg = self._format_discord_message(briefing)
        briefing["discord_message"] = discord_msg

        # Post to Discord (only if not being called from inside the bot async context)
        if post_to_discord:
            self._post_news_to_discord(discord_msg)

        # Save to log
        self._save_briefing(briefing)

        logger.info(
            f"📰 {briefing_type.upper()} briefing complete — "
            f"{sum(len(v) for v in ticker_news.values())} articles across "
            f"{len(ticker_news)} tickers"
        )
        return briefing

    # ─────────────────────────────────────────
    # NEWS FETCHING
    # ─────────────────────────────────────────

    def _fetch_ticker_news(self, ticker: str, hours_back: int) -> list:
        try:
            since  = datetime.utcnow() - timedelta(hours=hours_back)
            url    = "https://api.polygon.io/v2/reference/news"
            params = {
                "ticker":              ticker,
                "published_utc.gte":   since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit":               10,
                "sort":                "published_utc",
                "order":               "desc",
                "apiKey":              config.POLYGON_API_KEY,
            }
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            articles = []
            for item in data.get("results", []):
                articles.append({
                    "title":       item.get("title", ""),
                    "publisher":   item.get("publisher", {}).get("name", ""),
                    "published":   item.get("published_utc", ""),
                    "url":         item.get("article_url", ""),
                    "tickers":     item.get("tickers", []),
                    "description": item.get("description", "")[:200],
                })
            return articles

        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                logger.warning(f"Rate limit fetching news for {ticker} — skipping")
            else:
                logger.error(f"News fetch error for {ticker}: {e.response.status_code if e.response else 'unknown'}")
            return []
        except Exception as e:
            logger.error(f"News fetch error for {ticker}: {type(e).__name__}")
            return []

    def _fetch_market_news(self, hours_back: int, already_fetched: list = None) -> list:
        """Fetch market-wide news — skips tickers already fetched."""
        already_fetched = already_fetched or []
        market_tickers  = [t for t in ["SPY", "QQQ"] if t not in already_fetched]

        if not market_tickers:
            logger.debug("All market tickers already covered by watchlist")
            return []

        all_articles = []
        seen_titles  = set()

        for ticker in market_tickers:
            time.sleep(1.5)
            articles = self._fetch_ticker_news(ticker, hours_back)
            for article in articles:
                title = article.get("title", "")
                if title not in seen_titles:
                    seen_titles.add(title)
                    all_articles.append(article)

        return all_articles[:10]

    # ─────────────────────────────────────────
    # DISCORD POSTING
    # ─────────────────────────────────────────

    def _post_news_to_discord(self, message: str):
        """
        Post news briefing to Discord.
        Uses get_bot_loop() which stores the event loop on bot connect.
        Required for discord.py 2.x where bot.loop was removed.
        """
        try:
            from alerts.discord_bot import get_bot_loop, bot
            import asyncio

            channel_id = getattr(config, "DISCORD_CHANNEL_ID_NEWS", 0) \
                         or config.DISCORD_CHANNEL_ID_STANDARD

            if not channel_id:
                logger.warning("No Discord channel configured for news")
                return

            chunks = []
            if len(message) <= 1900:
                chunks = [message]
            else:
                current = ""
                for line in message.split("\n"):
                    if len(current) + len(line) + 1 > 1900:
                        if current:
                            chunks.append(current)
                        current = line
                    else:
                        current = current + "\n" + line if current else line
                if current:
                    chunks.append(current)

            async def _send_all():
                channel = bot.get_channel(channel_id)
                if channel:
                    for chunk in chunks:
                        await channel.send(chunk)
                    logger.info(f"News briefing posted to Discord ({len(chunks)} chunk(s))")
                else:
                    logger.error(f"News channel {channel_id} not found — check DISCORD_CHANNEL_ID_NEWS in .env")

            loop = get_bot_loop()
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(_send_all(), loop)
            else:
                logger.warning("Discord bot loop not ready — news not posted")

        except Exception as e:
            logger.error(f"News Discord post error: {type(e).__name__} — {e}")

    # AI SYNTHESIS
    # ─────────────────────────────────────────

    def _synthesize_with_ai(
        self,
        briefing_type: str,
        ticker_news:   dict,
        market_news:   list,
    ) -> str:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return "AI synthesis unavailable"

        ticker_summary = ""
        for ticker, articles in ticker_news.items():
            if articles:
                headlines = "\n".join(
                    f"  - {a['title']} ({a['publisher']})"
                    for a in articles[:3]
                )
                ticker_summary += f"\n{ticker}:\n{headlines}"

        market_summary = ""
        if market_news:
            market_summary = "\nMarket-wide news:\n" + "\n".join(
                f"  - {a['title']}" for a in market_news[:5]
            )

        briefing_context = {
            "morning": "Pre-market briefing — trader is preparing for the day",
            "midday":  "Midday check-in — market is open, trader wants to know what's moving",
            "eod":     "End of day wrap — market is closing, trader wants to review and prepare for tomorrow",
        }.get(briefing_type, "Trading briefing")

        prompt = f"""You are a trading assistant providing a {briefing_type} news briefing.
Context: {briefing_context}

The trader uses a systematic signal engine (MA/Donchian/Volume/CVD/RSI)
and trades US stocks, ETFs, and options spreads.

NEWS DATA:
{ticker_summary}
{market_summary}

Provide a concise {briefing_type} briefing covering:
1. Overall market tone (1 sentence)
2. Most important news per ticker that has news (1-2 sentences each, trading relevance only)
3. Any tickers to watch closely based on news
4. One key risk or opportunity to keep in mind

Keep it under 250 words. Be direct and actionable.
Flag anything that could override a technical signal (earnings, major catalyst)."""

        try:
            headers = {
                "Content-Type":      "application/json",
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
            }
            payload = {
                "model":      CLAUDE_MODEL,
                "max_tokens": 600,
                "messages":   [{"role": "user", "content": prompt}],
            }
            resp = requests.post(CLAUDE_API_URL, headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return "".join(
                b.get("text", "")
                for b in data.get("content", [])
                if b.get("type") == "text"
            )
        except Exception as e:
            logger.error(f"AI synthesis error: {type(e).__name__}")
            return "AI synthesis failed — check logs"

    # ─────────────────────────────────────────
    # BRIEFING BUILDER + FORMATTER
    # ─────────────────────────────────────────

    def _build_briefing(self, briefing_type, ticker_news, market_news, watchlist):
        now_est   = datetime.now(self.eastern)
        timestamp = now_est.strftime("%Y-%m-%d %I:%M %p EST")
        emoji_map = {"morning": "🌅", "midday": "☀️", "eod": "🌆"}
        return {
            "type":               briefing_type,
            "emoji":              emoji_map.get(briefing_type, "📰"),
            "timestamp":          timestamp,
            "watchlist":          watchlist,
            "ticker_news":        ticker_news,
            "market_news":        market_news,
            "tickers_with_news":  list(ticker_news.keys()),
            "total_articles":     sum(len(v) for v in ticker_news.values()),
            "ai_synthesis":       "",
            "discord_message":    "",
        }

    def _format_discord_message(self, briefing: dict) -> str:
        emoji  = briefing["emoji"]
        btype  = briefing["type"].upper()
        ts     = briefing["timestamp"]
        title_map = {"MORNING": "MORNING BRIEFING", "MIDDAY": "MIDDAY UPDATE", "EOD": "END OF DAY WRAP"}
        title  = title_map.get(btype, "NEWS BRIEFING")

        lines = [
            f"{emoji} **{title}** — {ts}",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]

        if briefing["ticker_news"]:
            lines.append(f"\n📰 **Headlines** ({briefing['total_articles']} articles)")
            for ticker, articles in briefing["ticker_news"].items():
                if articles:
                    lines.append(f"\n**{ticker}:**")
                    for a in articles[:2]:
                        pub = f" _{a['publisher']}_" if a.get("publisher") else ""
                        lines.append(f"  • {a['title']}{pub}")
        else:
            lines.append("\n📰 No significant news for your watchlist")

        if briefing.get("ai_synthesis") and "unavailable" not in briefing["ai_synthesis"].lower():
            lines.append(f"\n🤖 **AI Analysis:**\n{briefing['ai_synthesis']}")

        lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    # ─────────────────────────────────────────
    # PERSISTENCE
    # ─────────────────────────────────────────

    def _save_briefing(self, briefing: dict):
        try:
            existing = []
            if os.path.exists(self._news_log_path):
                with open(self._news_log_path, "r") as f:
                    existing = json.load(f)
            existing.append(briefing)
            existing = existing[-90:]
            with open(self._news_log_path, "w") as f:
                json.dump(existing, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save briefing: {e}")

    def get_recent_briefings(self, limit: int = 10) -> list:
        try:
            if not os.path.exists(self._news_log_path):
                return []
            with open(self._news_log_path, "r") as f:
                all_briefings = json.load(f)
            return list(reversed(all_briefings[-limit:]))
        except Exception:
            return []

    # ─────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────

    def _load_watchlist(self) -> dict:
        try:
            with open(config.WATCHLIST_PATH, "r") as f:
                return json.load(f)
        except Exception:
            return {"swing": [], "intraday": []}
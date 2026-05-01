"""
data/nexus_client.py -- Nexus Intelligence Client

Queries nexus.db directly for macro and market intelligence
collected by the Nexus social media pipeline.

READ-ONLY. This client never writes to nexus.db.
The Nexus project owns that database entirely.

Categories available:
    Stock Market        -- individual ticker mentions, earnings, price action
    Economics           -- macro data, Fed, inflation, employment
    Trading Strategies  -- specific setups, risk management, entries/exits
    World News          -- geopolitical events that move markets

Usage:
    from data.nexus_client import NexusClient
    client = NexusClient()

    # Get recent macro context
    context = client.get_market_context(days=7)

    # Get insights for a specific category
    economics = client.query(category="Economics", days=14, limit=10)

    # Get all high-confidence insights (low misinformation score)
    clean     = client.get_trusted(days=30, max_misinfo_score=0.2)

    # Check if Nexus has anything on a specific ticker
    ticker    = client.search_ticker("SPY", days=14)

Run standalone to preview what Nexus has:
    python -m data.nexus_client
"""

from __future__ import annotations

import os
import sys
import sqlite3
from datetime import datetime, timedelta

from loguru import logger

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import config

# ── Nexus DB path ─────────────────────────────────────────────
# Reads from config if set, otherwise uses the known default location.
NEXUS_DB_PATH = getattr(
    config, "NEXUS_DB_PATH",
    r"C:\Users\alexr\Documents\Nexus Project\nexus.db"
)

# ── Categories that are relevant to trading ───────────────────
TRADING_CATEGORIES = [
    "Stock Market",
    "Economics",
    "Trading Strategies",
    "World News",
]


class NexusClient:
    """
    Read-only client for querying the Nexus intelligence database.
    Provides macro and market context to the trading assistant.
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or NEXUS_DB_PATH
        self._check_connection()

    # ─────────────────────────────────────────
    # CONNECTION CHECK
    # ─────────────────────────────────────────

    def _check_connection(self):
        """Verify nexus.db exists and is readable."""
        if not os.path.exists(self.db_path):
            logger.warning(
                f"Nexus DB not found at {self.db_path}. "
                f"Set NEXUS_DB_PATH in .env to override."
            )
            return False
        try:
            conn = sqlite3.connect(self.db_path)
            conn.close()
            logger.debug(f"NexusClient connected: {self.db_path}")
            return True
        except Exception as e:
            logger.error(f"NexusClient connection failed: {e}")
            return False

    def is_available(self) -> bool:
        """Return True if nexus.db is accessible."""
        return os.path.exists(self.db_path)

    # ─────────────────────────────────────────
    # CORE QUERY
    # ─────────────────────────────────────────

    def query(
        self,
        category:          str | None = None,
        days:              int        = 7,
        limit:             int        = 20,
        max_misinfo_score: float      = 0.5,
        min_confidence:    float      = 0.0,
        sentiment:         str | None = None,
    ) -> list[dict]:
        """
        Query Nexus insights with filters.

        Args:
            category:          One of TRADING_CATEGORIES, or None for all
            days:              Look back N days from today
            limit:             Max results to return
            max_misinfo_score: Filter out high-misinfo content (0=clean, 1=suspect)
            min_confidence:    Minimum confidence score (0-1)
            sentiment:         "positive", "negative", "neutral", or None for all

        Returns:
            List of insight dicts with keys:
            id, title, summary, category, sentiment, confidence,
            misinformation_score, source, processed_at, tags
        """
        if not self.is_available():
            logger.warning("NexusClient: database not available")
            return []

        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        # Build query dynamically based on filters
        conditions = ["processed_at >= ?"]
        params     = [cutoff]

        if category:
            conditions.append("category = ?")
            params.append(category)
        else:
            # Default: only trading-relevant categories
            placeholders = ",".join("?" * len(TRADING_CATEGORIES))
            conditions.append(f"category IN ({placeholders})")
            params.extend(TRADING_CATEGORIES)

        if max_misinfo_score < 1.0:
            conditions.append("(misinformation_score IS NULL OR misinformation_score <= ?)")
            params.append(max_misinfo_score)

        if min_confidence > 0.0:
            conditions.append("(confidence IS NULL OR confidence >= ?)")
            params.append(min_confidence)

        if sentiment:
            conditions.append("sentiment = ?")
            params.append(sentiment)

        where  = " AND ".join(conditions)
        sql    = f"""
            SELECT id, title, summary, category, sentiment,
                   confidence, misinformation_score, source,
                   processed_at, tags
            FROM insights
            WHERE {where}
            ORDER BY processed_at DESC
            LIMIT ?
        """
        params.append(limit)

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(sql, params)
            rows   = [dict(row) for row in cursor.fetchall()]
            conn.close()
            logger.debug(
                f"NexusClient query: category={category} days={days} "
                f"→ {len(rows)} results"
            )
            return rows
        except sqlite3.OperationalError as e:
            logger.error(f"NexusClient query failed: {e}")
            logger.info("Check that nexus.db schema has an 'insights' table")
            return []
        except Exception as e:
            logger.error(f"NexusClient unexpected error: {e}")
            return []

    # ─────────────────────────────────────────
    # CONVENIENCE METHODS
    # ─────────────────────────────────────────

    def get_market_context(self, days: int = 7) -> dict:
        """
        Get a structured market context summary from all relevant categories.
        Used by the SPY daily strategy for macro context before placing trades.

        Returns dict with one key per category, each containing top insights.
        """
        context = {}
        for cat in TRADING_CATEGORIES:
            results = self.query(
                category          = cat,
                days              = days,
                limit             = 5,
                max_misinfo_score = 0.3,
            )
            if results:
                context[cat] = results

        total = sum(len(v) for v in context.values())
        logger.info(
            f"Nexus market context: {total} insights across "
            f"{len(context)} categories (last {days} days)"
        )
        return context

    def get_trusted(
        self,
        days:              int   = 30,
        max_misinfo_score: float = 0.2,
        limit:             int   = 50,
    ) -> list[dict]:
        """
        Get high-confidence, low-misinformation insights.
        Most reliable signal for strategy decisions.
        """
        return self.query(
            days              = days,
            max_misinfo_score = max_misinfo_score,
            min_confidence    = 0.7,
            limit             = limit,
        )

    def search_ticker(self, ticker: str, days: int = 14) -> list[dict]:
        """
        Search for insights mentioning a specific ticker symbol.
        Nexus may not have structured ticker fields — searches title + summary.
        """
        if not self.is_available():
            return []

        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        sql    = """
            SELECT id, title, summary, category, sentiment,
                   confidence, misinformation_score, source,
                   processed_at, tags
            FROM insights
            WHERE processed_at >= ?
              AND category IN ({})
              AND (title LIKE ? OR summary LIKE ?)
            ORDER BY processed_at DESC
            LIMIT 20
        """.format(",".join("?" * len(TRADING_CATEGORIES)))

        params = [cutoff] + TRADING_CATEGORIES + [
            f"%{ticker}%", f"%{ticker}%"
        ]

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(sql, params)
            rows   = [dict(row) for row in cursor.fetchall()]
            conn.close()
            logger.debug(f"Nexus ticker search {ticker}: {len(rows)} results")
            return rows
        except Exception as e:
            logger.error(f"NexusClient ticker search failed: {e}")
            return []

    def get_sentiment_summary(self, days: int = 7) -> dict:
        """
        Get a sentiment breakdown across categories.
        Useful for regime context — heavy negative sentiment = caution.

        Returns:
            {
                "Economics":         {"positive": 3, "negative": 7, "neutral": 2},
                "Stock Market":      {"positive": 8, "negative": 2, "neutral": 4},
                ...
                "overall_sentiment": "negative"
            }
        """
        if not self.is_available():
            return {}

        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        sql    = """
            SELECT category, sentiment, COUNT(*) as count
            FROM insights
            WHERE processed_at >= ?
              AND category IN ({})
              AND sentiment IS NOT NULL
            GROUP BY category, sentiment
        """.format(",".join("?" * len(TRADING_CATEGORIES)))

        params = [cutoff] + TRADING_CATEGORIES

        try:
            conn   = sqlite3.connect(self.db_path)
            cursor = conn.execute(sql, params)
            rows   = cursor.fetchall()
            conn.close()
        except Exception as e:
            logger.error(f"NexusClient sentiment summary failed: {e}")
            return {}

        # Structure results
        summary: dict = {}
        totals         = {"positive": 0, "negative": 0, "neutral": 0}

        for category, sentiment, count in rows:
            if category not in summary:
                summary[category] = {"positive": 0, "negative": 0, "neutral": 0}
            if sentiment in summary[category]:
                summary[category][sentiment] = count
                totals[sentiment]           += count

        # Overall sentiment = whichever polarity dominates
        overall = max(totals, key=totals.get)
        summary["overall_sentiment"] = overall
        summary["totals"]            = totals

        return summary

    def export_insights_md(
        self,
        output_path: str,
        days:        int   = 7,
        max_misinfo: float = 0.3,
    ):
        """
        Export recent trusted insights to a markdown file.
        This is how NEXUS_INSIGHTS.md gets generated — call this
        from the Nexus pipeline or a scheduled job.

        Args:
            output_path: Where to write the .md file
            days:        How many days back to include
            max_misinfo: Filter threshold
        """
        context = {}
        for cat in TRADING_CATEGORIES:
            results = self.query(
                category          = cat,
                days              = days,
                max_misinfo_score = max_misinfo,
                limit             = 10,
            )
            if results:
                context[cat] = results

        lines = [
            "# NEXUS_INSIGHTS.md -- Auto-generated",
            f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"# Source: nexus.db | Last {days} days | Max misinfo: {max_misinfo}",
            "# READ-ONLY -- Do not edit manually",
            "",
        ]

        if not context:
            lines.append("_No insights available for this period._")
        else:
            for cat, insights in context.items():
                lines.append(f"## {cat}")
                lines.append("")
                for ins in insights:
                    sentiment = ins.get("sentiment", "neutral")
                    emoji     = {"positive":"📈","negative":"📉","neutral":"➡️"}.get(
                        sentiment, "•"
                    )
                    lines.append(f"### {emoji} {ins.get('title','Untitled')}")
                    lines.append(
                        f"*{ins.get('processed_at','')[:10]} | "
                        f"Sentiment: {sentiment} | "
                        f"Confidence: {ins.get('confidence', 'N/A')}*"
                    )
                    lines.append("")
                    if ins.get("summary"):
                        lines.append(ins["summary"])
                    lines.append("")

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        logger.info(f"NEXUS_INSIGHTS.md exported to {output_path}")


# ─────────────────────────────────────────
# STANDALONE PREVIEW
# ─────────────────────────────────────────

if __name__ == "__main__":
    client = NexusClient()

    if not client.is_available():
        print(f"nexus.db not found at: {NEXUS_DB_PATH}")
        print("Set NEXUS_DB_PATH in your .env to point at the right location")
        sys.exit(1)

    print("\n── Nexus Market Context (last 7 days) ──")
    context = client.get_market_context(days=7)
    if not context:
        print("No recent insights found")
    else:
        for cat, insights in context.items():
            print(f"\n{cat} ({len(insights)} insights):")
            for ins in insights[:3]:
                print(f"  • {ins.get('title','')[:80]}")
                print(f"    Sentiment: {ins.get('sentiment')} | "
                      f"Confidence: {ins.get('confidence','N/A')}")

    print("\n── Sentiment Summary ──")
    sentiment = client.get_sentiment_summary(days=7)
    for cat, counts in sentiment.items():
        if cat not in ("overall_sentiment", "totals"):
            print(f"  {cat}: {counts}")
    print(f"  Overall: {sentiment.get('overall_sentiment', 'unknown')}")

    print("\n── Exporting NEXUS_INSIGHTS.md ──")
    client.export_insights_md("NEXUS_INSIGHTS.md", days=7)
    print("  Done — check NEXUS_INSIGHTS.md in project root")

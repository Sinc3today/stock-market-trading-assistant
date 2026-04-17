"""
tests/test_news.py — Test news scanner
Tests news fetching and briefing structure without requiring AI key.

Run with:
    pytest tests/test_news.py -v
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from scanners.news_scanner import NewsScanner


@pytest.fixture
def scanner():
    return NewsScanner()


def test_news_scanner_initializes(scanner):
    assert scanner is not None
    print("\n✅ NewsScanner initialized")


def test_fetch_ticker_news(scanner):
    """Should return news articles for AAPL."""
    articles = scanner._fetch_ticker_news("AAPL", hours_back=48)
    assert isinstance(articles, list)
    if articles:
        assert "title" in articles[0]
        assert "publisher" in articles[0]
        print(f"\n✅ Got {len(articles)} articles for AAPL")
        for a in articles[:2]:
            print(f"  • {a['title']}")
    else:
        print("\n✅ No recent news for AAPL (valid result)")


def test_fetch_market_news(scanner):
    """Should return market-wide news."""
    articles = scanner._fetch_market_news(hours_back=24)
    assert isinstance(articles, list)
    print(f"\n✅ Got {len(articles)} market-wide articles")


def test_build_briefing_structure(scanner):
    """Briefing dict should have correct keys."""
    mock_ticker_news = {
        "AAPL": [{"title": "Test", "publisher": "Test", "published": "", "url": "", "tickers": [], "description": ""}]
    }
    briefing = scanner._build_briefing("morning", mock_ticker_news, [], ["AAPL"])
    assert "type"          in briefing
    assert "timestamp"     in briefing
    assert "ticker_news"   in briefing
    assert "ai_synthesis"  in briefing
    assert briefing["type"] == "morning"
    print(f"\n✅ Briefing structure correct: {list(briefing.keys())}")


def test_format_discord_message(scanner):
    """Discord message should contain key fields."""
    mock_ticker_news = {
        "AAPL": [{"title": "Apple hits new high", "publisher": "Reuters",
                  "published": "", "url": "", "tickers": ["AAPL"], "description": ""}]
    }
    briefing = scanner._build_briefing("morning", mock_ticker_news, [], ["AAPL"])
    briefing["ai_synthesis"] = "Market looks cautiously bullish."
    msg = scanner._format_discord_message(briefing)
    assert "MORNING"   in msg
    assert "AAPL"      in msg
    assert "Apple"     in msg
    print(f"\n✅ Discord message formatted correctly")
    print(msg[:300])


def test_briefing_types_all_valid(scanner):
    """All three briefing types should work."""
    from scanners.news_scanner import LOOKBACK_HOURS
    for btype in ["morning", "midday", "eod"]:
        assert btype in LOOKBACK_HOURS
        briefing = scanner._build_briefing(btype, {}, [], [])
        assert briefing["type"] == btype
    print("\n✅ All briefing types valid")


def test_save_and_load_briefing(scanner, tmp_path, monkeypatch):
    """Should save and retrieve briefings correctly."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    scanner._news_log_path = os.path.join(str(tmp_path), "news_briefings.json")
    os.makedirs(str(tmp_path), exist_ok=True)

    briefing = scanner._build_briefing("morning", {}, [], ["AAPL"])
    briefing["ai_synthesis"] = "Test synthesis"
    scanner._save_briefing(briefing)

    loaded = scanner.get_recent_briefings(limit=5)
    assert len(loaded) == 1
    assert loaded[0]["type"] == "morning"
    print(f"\n✅ Briefing saved and loaded correctly")

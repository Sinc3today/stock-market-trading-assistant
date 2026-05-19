"""
tests/test_morning_briefer.py -- MorningBriefer happy + fallback paths.

All external dependencies (SPYDailyStrategy, macro_runner, event_calendar,
Claude HTTP) are stubbed so tests are fast and offline.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import signals.morning_briefer as mb_mod
import signals.macro_runner    as mr_mod
from signals.morning_briefer import MorningBriefer


# ─────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────

@pytest.fixture
def iso_logs(tmp_path, monkeypatch):
    """Isolate LOG_DIR + macro_runner state directory."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setattr(mr_mod, "_MACRO_DIR", str(tmp_path / "macro"))
    # Strip the live key so unmocked Claude calls can't accidentally hit production.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return tmp_path


def _base_card(tradeable=True, regime="choppy_low_vol", play="Iron Condor"):
    """Synthetic SPYDailyStrategy output (mirrors PlayCard.asdict)."""
    return {
        "date":             "2026-05-18",
        "tradeable":        tradeable,
        "regime":           regime,
        "play":             play,
        "confidence":       0.85,
        "reasons":          ["ADX 17 (chop)", "VIX 14 (calm)"],
        "metrics":          {"spy_close": 700.0, "vix": 14.0, "ivr": 31,
                              "adx": 17.0, "ma200_dist_%": 1.5},
        "options":          {"strategy": "iron_condor",
                              "max_profit": 250, "max_loss": 750,
                              "rr_ratio": "0.33",
                              "recommended_dte": 14,
                              "exit_rule": "Close at 50% profit"},
        "discord_message":  "🤖 **TODAY'S PLAY — SPY** (2026-05-18)\nRegime: choppy_low_vol",
        "plan_payload":     {"date": "2026-05-18", "ticker": "SPY",
                              "regime": regime, "play": play},
    }


class _StubStrategy:
    def __init__(self, card): self._card = card
    def build_today(self, today=None): return self._card


class _StubEventCalendar:
    def __init__(self, events): self._events = events
    def get_next_events(self, days=14): return self._events


def _seed_macro(tmp_path, vix=None, sector=None):
    """Write macro_runner state files so MorningBriefer reads them."""
    macro_dir = tmp_path / "macro"
    macro_dir.mkdir(exist_ok=True)
    if vix is not None:
        with open(macro_dir / "vix_latest.json", "w") as f:
            json.dump(vix, f)
    if sector is not None:
        with open(macro_dir / "sector_latest.json", "w") as f:
            json.dump(sector, f)


# ─────────────────────────────────────────
# FALLBACK PATH (no Claude)
# ─────────────────────────────────────────

def test_fallback_no_macro_no_events(iso_logs):
    """No Claude key, no macro context, no events -> bare-bones narrative."""
    briefer = MorningBriefer(
        spy_strategy = _StubStrategy(_base_card()),
        api_key      = None,
    )
    brief = briefer.build_today(today=date(2026, 5, 18))
    assert brief["regime"]           == "choppy_low_vol"
    assert brief["tradeable"]        is True
    assert brief["skip_conditions"]  == []
    assert brief["watch_conditions"] == []
    assert "choppy_low_vol" in brief["narrative"]


def test_fallback_skip_card_uses_minimal_narrative(iso_logs):
    briefer = MorningBriefer(
        spy_strategy = _StubStrategy(_base_card(tradeable=False, play="HIGH_VOL_SKIP")),
        api_key      = None,
    )
    brief = briefer.build_today()
    assert brief["tradeable"]      is False
    assert "skip conditions met"   in brief["narrative"].lower()


def test_fallback_vix_stress_adds_skip(iso_logs, tmp_path):
    _seed_macro(tmp_path, vix={"flag": "stress", "ratio": 1.15, "VIX": 18, "VIX3M": 15.7})
    briefer = MorningBriefer(spy_strategy=_StubStrategy(_base_card()), api_key=None)
    brief = briefer.build_today()
    assert any("stress" in s.lower() for s in brief["skip_conditions"])


def test_fallback_today_event_adds_skip(iso_logs):
    cal = _StubEventCalendar([{"event": "FOMC", "days_away": 0, "date": "2026-05-18"}])
    briefer = MorningBriefer(
        spy_strategy   = _StubStrategy(_base_card()),
        event_calendar = cal,
        api_key        = None,
    )
    brief = briefer.build_today()
    assert any("FOMC" in s for s in brief["skip_conditions"])
    # Only events 0-1 days away are kept
    assert any(e.get("event") == "FOMC" for e in brief["macro_context"]["events"])


class _StubEarnings:
    def __init__(self, today_tomorrow): self._items = today_tomorrow
    def get_today_and_tomorrow(self): return self._items


def test_fallback_earnings_today_adds_skip(iso_logs):
    earnings = _StubEarnings([
        {"ticker": "AAPL", "earnings_date": "2026-05-18", "days_away": 0},
    ])
    briefer = MorningBriefer(
        spy_strategy      = _StubStrategy(_base_card()),
        earnings_calendar = earnings,
        api_key           = None,
    )
    brief = briefer.build_today()
    assert any("AAPL" in s and "earnings today" in s for s in brief["skip_conditions"])


def test_fallback_earnings_tomorrow_adds_watch(iso_logs):
    earnings = _StubEarnings([
        {"ticker": "MSFT", "earnings_date": "2026-05-19", "days_away": 1},
    ])
    briefer = MorningBriefer(
        spy_strategy      = _StubStrategy(_base_card()),
        earnings_calendar = earnings,
        api_key           = None,
    )
    brief = briefer.build_today()
    assert any("MSFT" in w for w in brief["watch_conditions"])
    assert brief["macro_context"]["earnings"] == [
        {"ticker": "MSFT", "earnings_date": "2026-05-19", "days_away": 1}
    ]


class _StubEarningsHistory:
    """Mirrors EarningsHistory.annotate_upcoming contract."""
    def __init__(self, stats_by_ticker: dict):
        self._stats = stats_by_ticker
    def annotate_upcoming(self, upcoming: list[dict]) -> list[dict]:
        out = []
        for e in upcoming:
            row = dict(e)
            s = self._stats.get(e.get("ticker"))
            if s:
                row.update(s)
            out.append(row)
        return out


def test_fallback_earnings_skip_mentions_reaction_class(iso_logs):
    """When EarningsHistory is wired, the skip line names the typical
    move size and gap class so the user knows what they're avoiding."""
    earnings = _StubEarnings([
        {"ticker": "TSLA", "earnings_date": "2026-05-18", "days_away": 0},
    ])
    history = _StubEarningsHistory({
        "TSLA": {"mean_abs_move_pct": 6.2, "stdev_move_pct": 3.0,
                 "gap_class": "volatile"},
    })
    briefer = MorningBriefer(
        spy_strategy      = _StubStrategy(_base_card()),
        earnings_calendar = earnings,
        earnings_history  = history,
        api_key           = None,
    )
    brief = briefer.build_today()
    line = next(s for s in brief["skip_conditions"] if "TSLA" in s)
    assert "6.2%" in line
    assert "volatile" in line


def test_macro_context_earnings_carries_reaction_stats(iso_logs):
    """The annotated earnings stats land in macro_context.earnings so the
    Claude prompt and downstream consumers (web /macro, chat) can see them."""
    earnings = _StubEarnings([
        {"ticker": "NVDA", "earnings_date": "2026-05-19", "days_away": 1},
    ])
    history = _StubEarningsHistory({
        "NVDA": {"mean_abs_move_pct": 4.8, "stdev_move_pct": 2.1,
                 "gap_class": "volatile"},
    })
    briefer = MorningBriefer(
        spy_strategy      = _StubStrategy(_base_card()),
        earnings_calendar = earnings,
        earnings_history  = history,
        api_key           = None,
    )
    brief = briefer.build_today()
    nvda = brief["macro_context"]["earnings"][0]
    assert nvda["ticker"]            == "NVDA"
    assert nvda["mean_abs_move_pct"] == 4.8
    assert nvda["gap_class"]         == "volatile"


def test_earnings_history_failure_does_not_break_brief(iso_logs):
    """If annotate_upcoming raises, the briefer falls back to the
    un-annotated list rather than crashing."""
    earnings = _StubEarnings([
        {"ticker": "AAPL", "earnings_date": "2026-05-18", "days_away": 0},
    ])
    class Broken:
        def annotate_upcoming(self, *_a, **_kw):
            raise RuntimeError("yfinance dead")
    briefer = MorningBriefer(
        spy_strategy      = _StubStrategy(_base_card()),
        earnings_calendar = earnings,
        earnings_history  = Broken(),
        api_key           = None,
    )
    brief = briefer.build_today()
    # Still produced a brief, earnings still in macro_context
    assert brief["macro_context"]["earnings"][0]["ticker"] == "AAPL"
    # Fallback skip line still fires (no reaction info)
    assert any("AAPL" in s for s in brief["skip_conditions"])


def test_event_filter_drops_far_future(iso_logs):
    cal = _StubEventCalendar([
        {"event": "FOMC", "days_away": 0},
        {"event": "CPI",  "days_away": 5},   # too far
    ])
    briefer = MorningBriefer(
        spy_strategy   = _StubStrategy(_base_card()),
        event_calendar = cal,
        api_key        = None,
    )
    brief = briefer.build_today()
    events = brief["macro_context"]["events"]
    assert any(e["event"] == "FOMC" for e in events)
    assert not any(e["event"] == "CPI" for e in events)


# ─────────────────────────────────────────
# CLAUDE HAPPY PATH
# ─────────────────────────────────────────

class _FakeClaudeOK:
    """requests.post replacement returning a valid JSON-content reply."""
    def __init__(self, *a, **kw): pass
    def raise_for_status(self):    return None
    def json(self):
        body = {
            "narrative":        "Iron condor still in play. VIX calm, sectors rotating moderately.",
            "skip_conditions":  ["Skip if VIX opens > 17", "Skip if SPY gaps > 0.6%"],
            "watch_conditions": ["Tighten condor if dispersion drops below 1.5"],
        }
        return {"content": [{"type": "text", "text": json.dumps(body)}]}


def test_claude_happy_path_overrides_fallback(iso_logs, monkeypatch, tmp_path):
    _seed_macro(tmp_path,
                vix={"flag": "calm", "ratio": 0.94, "VIX": 14, "VIX3M": 15},
                sector={"signal": "rotating", "dispersion": 2.1,
                        "leaders": [["XLK", 3.2]], "laggards": [["XLE", -2.5]]})
    monkeypatch.setattr("requests.post", lambda *a, **kw: _FakeClaudeOK())

    briefer = MorningBriefer(
        spy_strategy = _StubStrategy(_base_card()),
        api_key      = "sk-test",
    )
    brief = briefer.build_today()
    assert "rotating moderately" in brief["narrative"]
    assert len(brief["skip_conditions"])  == 2
    assert len(brief["watch_conditions"]) == 1
    assert any("VIX opens" in s for s in brief["skip_conditions"])


class _FakeClaudeMalformed:
    def __init__(self, *a, **kw): pass
    def raise_for_status(self): return None
    def json(self):
        return {"content": [{"type": "text", "text": "I cannot help with that."}]}


def test_claude_unparseable_reply_falls_back(iso_logs, monkeypatch):
    monkeypatch.setattr("requests.post", lambda *a, **kw: _FakeClaudeMalformed())
    briefer = MorningBriefer(
        spy_strategy = _StubStrategy(_base_card()),
        api_key      = "sk-test",
    )
    brief = briefer.build_today()
    # Falls back to rule-based synthesis -> still produces a brief
    assert "choppy_low_vol" in brief["narrative"]
    assert isinstance(brief["skip_conditions"], list)


def test_claude_http_error_falls_back(iso_logs, monkeypatch):
    def boom(*a, **kw): raise RuntimeError("connection refused")
    monkeypatch.setattr("requests.post", boom)
    briefer = MorningBriefer(spy_strategy=_StubStrategy(_base_card()), api_key="sk-test")
    brief = briefer.build_today()
    assert brief["narrative"]


# ─────────────────────────────────────────
# PERSISTENCE
# ─────────────────────────────────────────

def test_brief_is_archived_to_disk(iso_logs):
    briefer = MorningBriefer(spy_strategy=_StubStrategy(_base_card()), api_key=None)
    briefer.build_today(today=date(2026, 5, 18))
    archive = iso_logs / "morning_briefs" / "2026-05-18.json"
    assert archive.exists()
    data = json.loads(archive.read_text())
    assert data["regime"]    == "choppy_low_vol"
    assert "narrative"       in data


def test_brief_writes_plan_with_macro_context(iso_logs):
    briefer = MorningBriefer(spy_strategy=_StubStrategy(_base_card()), api_key=None)
    briefer.build_today(today=date(2026, 5, 18))
    from journal.plan_logger import PlanLogger
    plan = PlanLogger().get_plan("2026-05-18")
    assert plan is not None
    assert "narrative"       in plan
    assert "macro_context"   in plan


# ─────────────────────────────────────────
# FORMATTERS
# ─────────────────────────────────────────

def test_pushover_message_is_plain_english_with_action(iso_logs):
    """New format: emoji + plain-English action verb + thesis. No jargon."""
    briefer = MorningBriefer(spy_strategy=_StubStrategy(_base_card()), api_key=None)
    brief = briefer.build_today()
    msg = brief["pushover_message"]
    # Action verb in plain English (not "iron_condor" raw)
    assert "IRON CONDOR" in msg.upper()
    # Emoji prefix for scannability
    assert msg[0] in ("💰", "📈", "🦅", "🎯", "📊", "🛑")
    # No raw jargon that the user explicitly flagged
    for jargon in ("trending_up_calm", "IVR=", "VIX3M", "ADX=", "contango"):
        assert jargon not in msg, f"jargon '{jargon}' leaked into Pushover"


def test_pushover_skip_card_says_no_trade(iso_logs):
    briefer = MorningBriefer(
        spy_strategy=_StubStrategy(_base_card(tradeable=False, play="HIGH_VOL_SKIP")),
        api_key=None,
    )
    msg = briefer.build_today()["pushover_message"]
    assert "Standing down" in msg
    assert msg[0] == "🛑"


def test_pushover_shows_real_strikes_when_legs_have_them(iso_logs):
    """When OptionsChain populated legs with strikes, Pushover shows them."""
    card = _base_card()
    card["options"] = {
        "strategy": "credit_spread",
        "max_profit": 250, "max_loss": 750, "rr_ratio": "0.33",
        "recommended_dte": 14, "exit_rule": "Close at 50%",
        "legs": [
            {"action": "buy",  "type": "put", "strike": 734.0,
             "ticker": "O:SPY260626P00734000", "expiration": "2026-06-26"},
            {"action": "sell", "type": "put", "strike": 739.0,
             "ticker": "O:SPY260626P00739000", "expiration": "2026-06-26"},
        ],
    }
    briefer = MorningBriefer(spy_strategy=_StubStrategy(card), api_key=None)
    msg = briefer.build_today()["pushover_message"]
    assert "Buy SPY $734 put"  in msg
    assert "Sell SPY $739 put" in msg
    # Friendly expiration line
    assert "Expires:" in msg
    assert "Max win:" in msg or "$250" in msg


def test_discord_message_appended_with_thesis_and_macro(iso_logs, tmp_path):
    _seed_macro(tmp_path,
                vix={"flag": "calm", "ratio": 0.94, "VIX": 14, "VIX3M": 15},
                sector={"signal": "rotating", "dispersion": 2.1,
                        "leaders": [], "laggards": []})
    briefer = MorningBriefer(spy_strategy=_StubStrategy(_base_card()), api_key=None)
    brief = briefer.build_today()
    msg = brief["discord_message"]
    assert "TODAY'S PLAY"     in msg       # base content preserved
    assert "Thesis:"          in msg
    assert "VIX TS"           in msg

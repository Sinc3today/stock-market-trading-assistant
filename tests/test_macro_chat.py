"""
tests/test_macro_chat.py -- MacroChat context bundle, history persistence,
and Claude-call wiring (mocked).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import signals.macro_runner as mr_mod
import alerts.macro_chat    as mc_mod
from alerts.macro_chat      import MacroChat
from journal.plan_logger    import PlanLogger
from journal.trade_recorder import TradeRecorder
from learning.knowledge_base import KnowledgeBase, KBEntry
from learning.predictions    import PredictionLog, Prediction


# ─────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────

@pytest.fixture
def iso_logs(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setattr(mr_mod, "_MACRO_DIR", str(tmp_path / "macro"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return tmp_path


def _seed_macro(tmp_path, vix=None, sector=None):
    d = tmp_path / "macro"
    d.mkdir(exist_ok=True)
    if vix is not None:
        with open(d / "vix_latest.json", "w") as f: json.dump(vix, f)
    if sector is not None:
        with open(d / "sector_latest.json", "w") as f: json.dump(sector, f)


def _seed_plan(date_iso):
    PlanLogger().save_plan({
        "date":             date_iso,
        "ticker":           "SPY",
        "regime":           "choppy_low_vol",
        "play":             "Iron Condor",
        "strategy":         "iron_condor",
        "rr_ratio":         "0.33",
        "recommended_dte":  14,
        "max_profit":       250,
        "max_loss":         750,
        "narrative":        "Calm, range-bound, condor in play.",
        "skip_conditions":  ["Skip if VIX > 17 at open"],
        "watch_conditions": ["Tighten if gap > 0.5%"],
    })


# ─────────────────────────────────────────
# Context bundle
# ─────────────────────────────────────────

def test_build_context_empty_when_nothing_seeded(iso_logs):
    ctx = MacroChat().build_context()
    assert ctx["today"]                == date.today().isoformat()
    assert ctx["morning_brief"]        == {}
    assert ctx["vix_term_structure"]   == {}
    assert ctx["sector_breadth"]       == {}
    assert ctx["events_next_48h"]      == []
    assert ctx["kb_recent"]            == []
    assert ctx["recent_trades"]        == []


def test_build_context_aggregates_all_sources(iso_logs, tmp_path):
    _seed_plan(date.today().isoformat())
    _seed_macro(tmp_path,
                vix={"flag": "calm", "ratio": 0.94, "VIX": 14, "VIX3M": 15},
                sector={"signal": "rotating", "dispersion": 2.1,
                        "leaders": [], "laggards": []})
    KnowledgeBase().append(KBEntry(
        date=date.today().isoformat(), category="market_context",
        claim="VIX flipped calm -> cautious", confidence=0.7,
    ))
    TradeRecorder().log_entry("SPY", 700.0, 1, strategy="iron_condor")
    PredictionLog().save(Prediction(
        date=date.today().isoformat(), regime="choppy_low_vol",
        direction="neutral", tradeable=True, entry_spy=700.0,
    ))

    ctx = MacroChat().build_context()
    assert ctx["morning_brief"]["regime"]      == "choppy_low_vol"
    assert ctx["vix_term_structure"]["flag"]    == "calm"
    assert ctx["sector_breadth"]["signal"]      == "rotating"
    assert len(ctx["kb_recent"])                == 1
    assert len(ctx["recent_trades"])            == 1
    # Predictions accuracy may be 0 (no resolved entries) — but the dict should exist
    assert "prediction_accuracy" in ctx


def test_context_summary_is_breadcrumb(iso_logs, tmp_path):
    _seed_plan(date.today().isoformat())
    _seed_macro(tmp_path,
                vix={"flag": "calm", "ratio": 0.94, "VIX": 14, "VIX3M": 15},
                sector={"signal": "rotating", "dispersion": 2.1,
                        "leaders": [], "laggards": []})
    summary = MacroChat().context_summary()
    assert "choppy_low_vol"  in summary
    assert "VIX TS calm"     in summary
    assert "sectors rotating" in summary


def test_event_calendar_filters_to_48h(iso_logs):
    class FakeCal:
        def get_next_events(self, days=14):
            return [
                {"event": "FOMC", "days_away": 0},
                {"event": "CPI",  "days_away": 1},
                {"event": "NFP",  "days_away": 5},
            ]
    ctx = MacroChat(event_calendar=FakeCal()).build_context()
    evts = ctx["events_next_48h"]
    assert any(e["event"] == "FOMC" for e in evts)
    assert any(e["event"] == "CPI"  for e in evts)
    assert not any(e["event"] == "NFP" for e in evts)


def test_earnings_calendar_surfaces_in_context(iso_logs):
    class FakeEarn:
        def get_upcoming(self, days=14):
            return [
                {"ticker": "AAPL", "earnings_date": "2026-05-20", "days_away": 2},
                {"ticker": "MSFT", "earnings_date": "2026-05-22", "days_away": 4},
            ]
    ctx = MacroChat(earnings_calendar=FakeEarn()).build_context()
    ern = ctx["earnings_next_7d"]
    assert len(ern) == 2
    assert ern[0]["ticker"] == "AAPL"

    mc = MacroChat(earnings_calendar=FakeEarn())
    summary = mc.context_summary()
    assert "earnings 2/7d" in summary

    block = mc._format_context_block(ctx)
    assert "EARNINGS NEXT 7D" in block
    assert "AAPL"             in block


def test_context_block_includes_all_sections(iso_logs, tmp_path):
    _seed_plan(date.today().isoformat())
    _seed_macro(tmp_path,
                vix={"flag": "calm", "ratio": 0.94, "VIX": 14, "VIX3M": 15},
                sector={"signal": "rotating", "dispersion": 2.1,
                        "leaders": [["XLK", 4.2]], "laggards": [["XLE", -2.5]]})
    KnowledgeBase().append(KBEntry(
        date=date.today().isoformat(), category="market_context",
        claim="VIX flipped to cautious yesterday", confidence=0.6,
    ))
    mc = MacroChat()
    ctx = mc.build_context()
    block = mc._format_context_block(ctx)
    assert "MORNING BRIEF"   in block
    assert "Iron Condor"     in block
    assert "MACRO"            in block
    assert '"flag": "calm"'   in block
    assert "KNOWLEDGE BASE"   in block
    assert "VIX flipped to cautious" in block


# ─────────────────────────────────────────
# History persistence
# ─────────────────────────────────────────

def test_history_starts_empty(iso_logs):
    assert MacroChat().history() == []


def test_append_and_read_back_history(iso_logs):
    mc = MacroChat()
    mc.append_turn("user",      "what's the play today?")
    mc.append_turn("assistant", "Iron condor SPY 690/710")
    hist = mc.history()
    assert len(hist) == 2
    assert hist[0]["role"]    == "user"
    assert hist[1]["content"] == "Iron condor SPY 690/710"
    # Turn has a timestamp
    assert "ts" in hist[0]


def test_append_rejects_unknown_role(iso_logs):
    with pytest.raises(ValueError):
        MacroChat().append_turn("system", "x")


def test_reset_history_removes_file(iso_logs):
    mc = MacroChat()
    mc.append_turn("user", "x")
    assert mc.history()
    mc.reset_history()
    assert mc.history() == []


def test_history_limit_returns_tail(iso_logs):
    mc = MacroChat()
    for i in range(75):
        mc.append_turn("user", f"msg {i}")
    out = mc.history(limit=10)
    assert len(out) == 10
    assert out[-1]["content"] == "msg 74"


def test_history_skips_corrupt_lines(iso_logs):
    mc = MacroChat()
    mc.append_turn("user", "ok")
    # Append a garbage line
    with open(mc._history_path, "a") as f:
        f.write("not-json\n")
    mc.append_turn("user", "also ok")
    out = mc.history()
    assert len(out) == 2  # corrupt line skipped


# ─────────────────────────────────────────
# ask() — Claude wiring
# ─────────────────────────────────────────

def test_ask_returns_message_when_empty(iso_logs):
    assert "Empty message" in MacroChat().ask("")
    assert "Empty message" in MacroChat().ask("   ")


def test_ask_without_api_key_returns_unavailable(iso_logs):
    out = MacroChat(api_key=None).ask("what's the play today?")
    assert "not configured" in out.lower()


class _FakeAnthropic:
    """Mock anthropic.Anthropic client returning a canned reply."""
    def __init__(self, reply_text="iron condor still in play"):
        self._reply = reply_text
        self.messages = self
        self.AuthenticationError = type("AuthError", (Exception,), {})
    def create(self, **kwargs):
        class _Block:
            def __init__(self, t): self.type, self.text = "text", t
        class _Resp:
            content = [_Block(self._reply)]
        # Capture for test assertions
        self._last_kwargs = kwargs
        return _Resp()


def test_ask_happy_path_appends_both_turns(iso_logs, monkeypatch):
    fake = _FakeAnthropic(reply_text="Yes, the condor still looks workable.")
    # Patch the anthropic module used inside ask()
    import sys, types
    fake_mod = types.SimpleNamespace(
        Anthropic           = lambda api_key=None: fake,
        AuthenticationError = fake.AuthenticationError,
    )
    monkeypatch.setitem(sys.modules, "anthropic", fake_mod)

    mc = MacroChat(api_key="sk-test")
    reply = mc.ask("should I take today's play?")

    assert "still looks workable" in reply
    hist = mc.history()
    assert len(hist) == 2
    assert hist[0]["role"]    == "user"
    assert hist[0]["content"] == "should I take today's play?"
    assert hist[1]["role"]    == "assistant"

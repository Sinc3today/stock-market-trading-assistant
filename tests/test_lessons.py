"""
tests/test_lessons.py — Test the lessons journal

Run with:
    pytest tests/test_lessons.py -v
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from journal.lessons import LessonsJournal, EMOTION_OPTIONS


@pytest.fixture
def journal(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return LessonsJournal()


def _log_sample(journal, trade_id="T001", outcome="win", followed=True,
                emotion="calm", pnl=5.0, entry_q=4, exit_q=4, exec_s=4):
    return journal.log_lesson(
        trade_id=trade_id, ticker="AAPL",
        outcome=outcome, pnl_pct=pnl,
        followed_system=followed,
        entry_quality=entry_q, exit_quality=exit_q,
        emotion_during=emotion,
        what_went_right="Good entry", what_went_wrong="Nothing",
        would_do_differently="Hold longer",
        lesson_summary="Trust the system",
        execution_score=exec_s, alert_score=85,
    )


# ─────────────────────────────────────────
# BASIC LOGGING TESTS
# ─────────────────────────────────────────

def test_log_lesson_returns_true(journal):
    result = _log_sample(journal)
    assert result is True
    print(f"\n✅ Lesson logged successfully")


def test_lesson_saved_correctly(journal):
    _log_sample(journal, trade_id="T001", outcome="win", emotion="calm")
    lesson = journal.get_lessons_for_trade("T001")
    assert lesson is not None
    assert lesson["ticker"]    == "AAPL"
    assert lesson["outcome"]   == "win"
    assert lesson["emotion_during"] == "calm"
    assert lesson["followed_system"] is True
    print(f"\n✅ Lesson saved: {lesson['ticker']} | {lesson['outcome']}")


def test_lesson_clamps_quality_scores(journal):
    """Quality scores should be clamped to 1-5."""
    journal.log_lesson(
        trade_id="T002", ticker="AAPL", outcome="win", pnl_pct=3.0,
        followed_system=True, entry_quality=10, exit_quality=0,
        emotion_during="calm", execution_score=99,
    )
    lesson = journal.get_lessons_for_trade("T002")
    assert lesson["entry_quality"]   == 5
    assert lesson["exit_quality"]    == 1
    assert lesson["execution_score"] == 5
    print(f"\n✅ Quality scores clamped correctly")


def test_flags_generated_on_loss(journal):
    _log_sample(journal, trade_id="T003", outcome="loss",
                followed=False, emotion="fomo", pnl=-6.0,
                entry_q=2, exit_q=2, exec_s=2)
    lesson = journal.get_lessons_for_trade("T003")
    flags  = lesson["flags"]
    assert "system_override"  in flags
    assert "emotional_loss"   in flags
    assert "reactive_trade"   in flags
    assert "override_loss"    in flags
    assert "large_loss"       in flags
    print(f"\n✅ Flags generated: {flags}")


def test_flags_generated_on_win(journal):
    _log_sample(journal, trade_id="T004", outcome="win",
                followed=True, emotion="calm", pnl=4.0)
    lesson = journal.get_lessons_for_trade("T004")
    flags  = lesson["flags"]
    assert "system_win"      in flags
    assert "disciplined_win" in flags
    print(f"\n✅ Win flags: {flags}")


# ─────────────────────────────────────────
# PATTERN ANALYSIS TESTS
# ─────────────────────────────────────────

def test_patterns_empty_with_no_data(journal):
    patterns = journal.get_patterns()
    assert patterns["total_lessons"] == 0
    assert patterns["insights"]      == []
    print(f"\n✅ Empty patterns handled")


def test_patterns_requires_3_trades(journal):
    _log_sample(journal, "T001", "win")
    _log_sample(journal, "T002", "loss")
    patterns = journal.get_patterns()
    assert "Log more trades" in patterns["insights"][0]
    print(f"\n✅ Needs 3 trades for insights")


def test_system_adherence_tracked(journal):
    _log_sample(journal, "T001", "win",  followed=True)
    _log_sample(journal, "T002", "win",  followed=True)
    _log_sample(journal, "T003", "loss", followed=False)
    patterns = journal.get_patterns()
    assert patterns["followed_system_count"] == 2
    assert patterns["overrode_system_count"] == 1
    print(f"\n✅ System adherence: "
          f"followed={patterns['followed_system_count']} "
          f"override={patterns['overrode_system_count']}")


def test_emotion_counts_tracked(journal):
    _log_sample(journal, "T001", "win",  emotion="calm")
    _log_sample(journal, "T002", "loss", emotion="fomo")
    _log_sample(journal, "T003", "win",  emotion="calm")
    patterns = journal.get_patterns()
    assert patterns["emotion_counts"]["calm"] == 2
    assert patterns["emotion_counts"]["fomo"] == 1
    print(f"\n✅ Emotions: {patterns['emotion_counts']}")


def test_win_rate_by_system_adherence(journal):
    # 3 followed = 2 wins, 1 loss = 66.7%
    _log_sample(journal, "T001", "win",  followed=True)
    _log_sample(journal, "T002", "win",  followed=True)
    _log_sample(journal, "T003", "loss", followed=True)
    # 2 overrides = 0 wins = 0%
    _log_sample(journal, "T004", "loss", followed=False)
    _log_sample(journal, "T005", "loss", followed=False)

    patterns = journal.get_patterns()
    assert patterns["followed_win_rate"] > patterns["override_win_rate"]
    print(f"\n✅ System win rate {patterns['followed_win_rate']}% "
          f"vs override {patterns['override_win_rate']}%")


def test_insights_generated(journal):
    # Log enough trades to trigger insights
    for i in range(4):
        _log_sample(journal, f"T{i:03d}", "win", followed=True, emotion="calm")
    _log_sample(journal, "T004", "loss", followed=False, emotion="fomo")

    patterns = journal.get_patterns()
    assert len(patterns["insights"]) > 0
    print(f"\n✅ Insights generated:")
    for insight in patterns["insights"]:
        print(f"   {insight}")


def test_get_recent_lessons(journal):
    for i in range(5):
        _log_sample(journal, f"T{i:03d}", "win")
    recent = journal.get_recent_lessons(limit=3)
    assert len(recent) == 3
    print(f"\n✅ Recent lessons returned: {len(recent)}")
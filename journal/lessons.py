"""
journal/lessons.py — Lessons Learned System
Structured post-trade debrief tied to every closed trade.
Tracks patterns, emotional flags, and execution quality over time.

Usage:
    from journal.lessons import LessonsJournal
    lj = LessonsJournal()
    lj.log_lesson(trade_id, debrief_data)
    patterns = lj.get_patterns()
"""

import json
import os
from datetime import datetime
from collections import Counter
from loguru import logger
import pytz
import config


# ── Debrief question keys ────────────────────────────────────
# These are the structured fields in every lesson entry
DEBRIEF_FIELDS = [
    "followed_system",       # bool  — did you take the trade based on the alert?
    "entry_quality",         # 1-5   — how clean was your entry?
    "exit_quality",          # 1-5   — did you exit at the right time?
    "emotion_during",        # str   — what emotion did you feel? (fear/greed/calm/fomo/revenge)
    "what_went_right",       # str   — free text
    "what_went_wrong",       # str   — free text
    "would_do_differently",  # str   — free text
    "lesson_summary",        # str   — one sentence takeaway
    "execution_score",       # 1-5   — overall execution rating
]

EMOTION_OPTIONS = ["calm", "fear", "greed", "fomo", "revenge", "impatient", "confident", "anxious"]


class LessonsJournal:
    """
    Records and analyzes post-trade lessons.
    Tracks patterns to help you improve over time.
    """

    def __init__(self):
        os.makedirs(config.LOG_DIR, exist_ok=True)
        self.lessons_path = os.path.join(config.LOG_DIR, "lessons.json")

    # ─────────────────────────────────────────
    # LOGGING
    # ─────────────────────────────────────────

    def log_lesson(
        self,
        trade_id:            str,
        ticker:              str,
        outcome:             str,
        pnl_pct:             float,
        followed_system:     bool,
        entry_quality:       int,
        exit_quality:        int,
        emotion_during:      str,
        what_went_right:     str = "",
        what_went_wrong:     str = "",
        would_do_differently:str = "",
        lesson_summary:      str = "",
        execution_score:     int = 3,
        alert_score:         int = None,
    ) -> bool:
        """
        Log a structured lesson for a closed trade.

        Args:
            trade_id:             Links to trade_recorder entry
            ticker:               Stock symbol
            outcome:              "win", "loss", "breakeven"
            pnl_pct:              P&L percentage from trade_recorder
            followed_system:      True if you took the trade from an alert
            entry_quality:        1-5 (1=terrible, 5=perfect)
            exit_quality:         1-5
            emotion_during:       One of EMOTION_OPTIONS
            what_went_right:      Free text
            what_went_wrong:      Free text
            would_do_differently: Free text
            lesson_summary:       One sentence takeaway
            execution_score:      1-5 overall
            alert_score:          System confidence score at time of alert

        Returns:
            True if saved successfully
        """
        eastern = pytz.timezone("US/Eastern")
        now_est = datetime.now(eastern).strftime("%Y-%m-%d %I:%M %p EST")

        # Validate ranges
        entry_quality    = max(1, min(5, entry_quality))
        exit_quality     = max(1, min(5, exit_quality))
        execution_score  = max(1, min(5, execution_score))
        emotion_during   = emotion_during.lower().strip()

        lesson = {
            # Identity
            "trade_id":   trade_id.upper(),
            "ticker":     ticker.upper(),
            "logged_at":  now_est,
            "outcome":    outcome,
            "pnl_pct":    pnl_pct,
            "alert_score":alert_score,

            # System adherence
            "followed_system": followed_system,

            # Quality ratings
            "entry_quality":  entry_quality,
            "exit_quality":   exit_quality,
            "execution_score":execution_score,

            # Emotional state
            "emotion_during": emotion_during,

            # Free text
            "what_went_right":      what_went_right.strip(),
            "what_went_wrong":      what_went_wrong.strip(),
            "would_do_differently": would_do_differently.strip(),
            "lesson_summary":       lesson_summary.strip(),

            # Auto-flags for pattern detection
            "flags": self._generate_flags(
                followed_system, emotion_during,
                entry_quality, exit_quality,
                outcome, pnl_pct
            ),
        }

        lessons = self._load()
        lessons.append(lesson)
        self._save(lessons)

        logger.info(
            f"Lesson logged: [{trade_id}] {ticker} | "
            f"Outcome: {outcome} | "
            f"Execution: {execution_score}/5 | "
            f"Followed system: {followed_system}"
        )
        return True

    # ─────────────────────────────────────────
    # PATTERN ANALYSIS
    # ─────────────────────────────────────────

    def get_patterns(self) -> dict:
        """
        Analyze all lessons to find recurring patterns.
        This is the core value of the lessons system — 
        finding what YOU consistently do right and wrong.
        """
        lessons = self._load()
        if not lessons:
            return self._empty_patterns()

        closed = [l for l in lessons if l.get("outcome") in ("win", "loss", "breakeven")]
        wins   = [l for l in closed if l.get("outcome") == "win"]
        losses = [l for l in closed if l.get("outcome") == "loss"]

        # ── System adherence analysis ────────────────────────────
        followed     = [l for l in closed if l.get("followed_system")]
        not_followed = [l for l in closed if not l.get("followed_system")]

        followed_win_rate = self._win_rate(followed)
        override_win_rate = self._win_rate(not_followed)

        # ── Emotion patterns ─────────────────────────────────────
        all_emotions    = [l["emotion_during"] for l in closed if l.get("emotion_during")]
        emotion_counts  = Counter(all_emotions)

        # Emotion on winning vs losing trades
        win_emotions  = Counter(l["emotion_during"] for l in wins  if l.get("emotion_during"))
        loss_emotions = Counter(l["emotion_during"] for l in losses if l.get("emotion_during"))

        # ── Execution quality ────────────────────────────────────
        exec_scores = [l["execution_score"] for l in closed if l.get("execution_score")]
        avg_execution = round(sum(exec_scores) / len(exec_scores), 1) if exec_scores else 0

        entry_scores = [l["entry_quality"] for l in closed if l.get("entry_quality")]
        avg_entry = round(sum(entry_scores) / len(entry_scores), 1) if entry_scores else 0

        exit_scores = [l["exit_quality"] for l in closed if l.get("exit_quality")]
        avg_exit = round(sum(exit_scores) / len(exit_scores), 1) if exit_scores else 0

        # ── Score vs outcome ─────────────────────────────────────
        high_score_trades = [l for l in closed if (l.get("alert_score") or 0) >= 85]
        low_score_trades  = [l for l in closed if (l.get("alert_score") or 0) < 85
                             and l.get("alert_score") is not None]

        # ── Flag summary ─────────────────────────────────────────
        all_flags = []
        for l in closed:
            all_flags.extend(l.get("flags", []))
        flag_counts = Counter(all_flags)

        # ── Key insights ─────────────────────────────────────────
        insights = self._generate_insights(
            followed_win_rate, override_win_rate,
            win_emotions, loss_emotions,
            avg_execution, avg_entry, avg_exit,
            flag_counts, len(closed)
        )

        return {
            "total_lessons":      len(lessons),
            "total_analyzed":     len(closed),

            # System adherence
            "followed_system_count":   len(followed),
            "overrode_system_count":   len(not_followed),
            "followed_win_rate":       followed_win_rate,
            "override_win_rate":       override_win_rate,

            # Emotions
            "most_common_emotion":     emotion_counts.most_common(1)[0][0]
                                       if emotion_counts else "unknown",
            "emotion_counts":          dict(emotion_counts),
            "win_emotions":            dict(win_emotions.most_common(3)),
            "loss_emotions":           dict(loss_emotions.most_common(3)),

            # Execution
            "avg_execution_score":     avg_execution,
            "avg_entry_quality":       avg_entry,
            "avg_exit_quality":        avg_exit,

            # Score alignment
            "high_score_win_rate":     self._win_rate(high_score_trades),
            "low_score_win_rate":      self._win_rate(low_score_trades),

            # Flags
            "top_flags":               dict(flag_counts.most_common(5)),

            # Insights
            "insights":                insights,
        }

    def get_lessons_for_trade(self, trade_id: str) -> dict | None:
        """Get the lesson entry for a specific trade."""
        for lesson in self._load():
            if lesson.get("trade_id") == trade_id.upper():
                return lesson
        return None

    def get_recent_lessons(self, limit: int = 10) -> list:
        return self._load()[-limit:]

    # ─────────────────────────────────────────
    # FLAG GENERATION
    # ─────────────────────────────────────────

    def _generate_flags(
        self,
        followed_system: bool,
        emotion:         str,
        entry_quality:   int,
        exit_quality:    int,
        outcome:         str,
        pnl_pct:         float,
    ) -> list[str]:
        """
        Auto-generate pattern flags from debrief data.
        These accumulate to reveal consistent behaviors.
        """
        flags = []

        if not followed_system:
            flags.append("system_override")

        if emotion in ("fomo", "revenge", "greed") and outcome == "loss":
            flags.append("emotional_loss")

        if emotion in ("fomo", "revenge"):
            flags.append("reactive_trade")

        if entry_quality <= 2:
            flags.append("poor_entry")

        if exit_quality <= 2 and outcome == "loss":
            flags.append("late_exit")

        if exit_quality <= 2 and outcome == "win":
            flags.append("early_exit")

        if not followed_system and outcome == "loss":
            flags.append("override_loss")

        if not followed_system and outcome == "win":
            flags.append("override_win")

        if followed_system and outcome == "win":
            flags.append("system_win")

        if emotion == "calm" and outcome == "win":
            flags.append("disciplined_win")

        if pnl_pct and pnl_pct < -5:
            flags.append("large_loss")

        return flags

    # ─────────────────────────────────────────
    # INSIGHT GENERATION
    # ─────────────────────────────────────────

    def _generate_insights(
        self,
        followed_win_rate:  float,
        override_win_rate:  float,
        win_emotions:       dict,
        loss_emotions:      dict,
        avg_execution:      float,
        avg_entry:          float,
        avg_exit:           float,
        flag_counts:        Counter,
        total:              int,
    ) -> list[str]:
        """
        Generate plain-English insights from pattern data.
        These are shown on the dashboard and fed to the AI advisor.
        """
        insights = []
        if total < 3:
            return ["Log more trades to unlock pattern insights (need at least 3)"]

        # System adherence insight
        if followed_win_rate > override_win_rate + 10:
            insights.append(
                f"✅ You win more when following the system "
                f"({followed_win_rate}% vs {override_win_rate}% when overriding)"
            )
        elif override_win_rate > followed_win_rate + 10:
            insights.append(
                f"⚠️ Your overrides are outperforming the system "
                f"({override_win_rate}% vs {followed_win_rate}%) — worth reviewing why"
            )

        # Emotion insights
        top_loss_emotion = max(loss_emotions, key=loss_emotions.get) \
                           if loss_emotions else None
        if top_loss_emotion in ("fomo", "revenge", "greed"):
            insights.append(
                f"⚠️ '{top_loss_emotion.upper()}' is your most common emotion on losing trades — "
                f"watch for this state before entering"
            )

        top_win_emotion = max(win_emotions, key=win_emotions.get) \
                          if win_emotions else None
        if top_win_emotion == "calm":
            insights.append("✅ Your best trades happen when you feel calm — protect that state")

        # Execution insights
        if avg_exit < 2.5:
            insights.append(
                f"⚠️ Your average exit quality is {avg_exit}/5 — "
                f"exits are your biggest area to improve"
            )
        if avg_entry < 2.5:
            insights.append(
                f"⚠️ Your average entry quality is {avg_entry}/5 — "
                f"you may be chasing entries"
            )

        # Flag-based insights
        if flag_counts.get("early_exit", 0) >= 2:
            insights.append(
                f"⚠️ You've exited winners early {flag_counts['early_exit']} times — "
                f"consider letting winners run longer"
            )
        if flag_counts.get("late_exit", 0) >= 2:
            insights.append(
                f"⚠️ You've held losers too long {flag_counts['late_exit']} times — "
                f"honor your stop losses"
            )
        if flag_counts.get("system_override", 0) >= 3:
            insights.append(
                f"📊 You've overridden the system {flag_counts['system_override']} times — "
                f"track if this helps or hurts your results"
            )

        return insights if insights else ["Keep logging trades to unlock insights"]

    # ─────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────

    def _win_rate(self, lessons: list) -> float:
        if not lessons:
            return 0.0
        wins = sum(1 for l in lessons if l.get("outcome") == "win")
        return round((wins / len(lessons)) * 100, 1)

    def _load(self) -> list:
        if not os.path.exists(self.lessons_path):
            return []
        try:
            with open(self.lessons_path, "r") as f:
                return json.load(f)
        except Exception:
            return []

    def _save(self, lessons: list):
        with open(self.lessons_path, "w") as f:
            json.dump(lessons[-500:], f, indent=2)

    def _empty_patterns(self) -> dict:
        return {
            "total_lessons": 0, "total_analyzed": 0,
            "followed_system_count": 0, "overrode_system_count": 0,
            "followed_win_rate": 0.0, "override_win_rate": 0.0,
            "most_common_emotion": "unknown", "emotion_counts": {},
            "win_emotions": {}, "loss_emotions": {},
            "avg_execution_score": 0.0, "avg_entry_quality": 0.0,
            "avg_exit_quality": 0.0,
            "high_score_win_rate": 0.0, "low_score_win_rate": 0.0,
            "top_flags": {}, "insights": [],
        }
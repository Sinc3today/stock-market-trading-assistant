"""
learning/knowledge_base.py -- Append-only structured KB.

Each entry is a small piece of evidence the assistant has learned from
its own predictions, paper trades, and reflections. Entries accumulate
forever (newest at the bottom) so later sessions can scan the whole
history and find patterns.

File layout:
    logs/learning/knowledge.jsonl       one JSON object per line
    logs/learning/KNOWLEDGE.md          human-readable rollup, regenerated
                                        on each save (last 50 entries)

Categories (free-form, but these are the conventional ones):
    regime_accuracy   - was the regime call confirmed by close?
    gate_quality      - did the score / R/R gates filter correctly?
    sizing            - was 1 contract appropriate for the move?
    exit_timing       - did the exit rule match the realised path?
    market_context    - macro / news / vol that influenced the day
    hypothesis        - a proposed change pending backtest
    backtest_result   - outcome of running a hypothesis
    edge_case         - off-hours learning finding
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timedelta
from typing import Iterable

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger


VALID_CATEGORIES = {
    "regime_accuracy",
    "gate_quality",
    "sizing",
    "exit_timing",
    "market_context",
    "hypothesis",
    "backtest_result",
    "edge_case",
    "other",
}


@dataclass
class KBEntry:
    """One observation. Confidence is the assistant's own self-rating 0-1."""

    date:       str
    category:   str
    claim:      str
    evidence:   str            = ""
    confidence: float          = 0.5
    source:     str            = "reflector"   # reflector / off_hours / hypothesis / manual
    tags:       list[str]      = field(default_factory=list)
    strategy:   str | None     = None   # e.g. "iron_condor", "bull_debit", "put_debit_spread"
    dte_bucket: str | None     = None   # "0DTE" / "1-3DTE" / "45DTE"
    book:       str | None     = None   # "disciplined" / "learning"
    id:         str            = field(default_factory=lambda: uuid.uuid4().hex[:10])

    def __post_init__(self):
        if self.category not in VALID_CATEGORIES:
            logger.warning(
                f"KBEntry category '{self.category}' not in standard set; "
                f"keeping but consider one of {sorted(VALID_CATEGORIES)}"
            )
        self.confidence = max(0.0, min(1.0, float(self.confidence)))


class KnowledgeBase:
    """JSONL-backed KB. Cheap to append, cheap to scan."""

    def __init__(self):
        os.makedirs(os.path.join(config.LOG_DIR, "learning"), exist_ok=True)

    @property
    def _path(self) -> str:
        return os.path.join(config.LOG_DIR, "learning", "knowledge.jsonl")

    @property
    def _md_path(self) -> str:
        return os.path.join(config.LOG_DIR, "learning", "KNOWLEDGE.md")

    # ── WRITE ─────────────────────────────────────────

    def append(self, entry: KBEntry) -> str:
        line = json.dumps(asdict(entry), separators=(",", ":"))
        with open(self._path, "a") as f:
            f.write(line + "\n")
        self._rewrite_markdown()
        logger.info(f"KB += [{entry.category}] {entry.claim[:60]}")
        return entry.id

    def append_many(self, entries: Iterable[KBEntry]) -> list[str]:
        ids = []
        for e in entries:
            ids.append(self.append(e))
        return ids

    # ── READ ──────────────────────────────────────────

    def all(self) -> list[dict]:
        if not os.path.exists(self._path):
            return []
        out = []
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning(f"Skipping corrupt KB line: {line[:80]}")
        return out

    def recent(self, days: int = 30) -> list[dict]:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        return [e for e in self.all() if e.get("date", "") >= cutoff]

    def by_category(self, category: str, days: int | None = None) -> list[dict]:
        pool = self.recent(days) if days else self.all()
        return [e for e in pool if e.get("category") == category]

    def search(self, *, strategy: str | None = None,
               dte_bucket: str | None = None,
               book: str | None = None,
               category: str | None = None,
               days: int | None = None) -> list[dict]:
        """Filter KB entries by optional tag values. Entries that lack a tag
        are EXCLUDED from filters that specify that tag — old (untagged)
        entries don't participate in strategy/book/dte_bucket searches.

        No-filter call returns all entries.
        """
        rows = self.recent(days=days) if days is not None else self.all()
        if strategy is not None:
            rows = [r for r in rows if r.get("strategy") == strategy]
        if dte_bucket is not None:
            rows = [r for r in rows if r.get("dte_bucket") == dte_bucket]
        if book is not None:
            rows = [r for r in rows if r.get("book") == book]
        if category is not None:
            rows = [r for r in rows if r.get("category") == category]
        return rows

    def stats(self) -> dict:
        entries = self.all()
        cat_counts: dict[str, int] = {}
        for e in entries:
            c = e.get("category", "other")
            cat_counts[c] = cat_counts.get(c, 0) + 1
        return {
            "total":      len(entries),
            "categories": cat_counts,
            "first_date": entries[0]["date"]  if entries else None,
            "last_date":  entries[-1]["date"] if entries else None,
        }

    # ── MARKDOWN ROLLUP ───────────────────────────────

    def _rewrite_markdown(self, last_n: int = 50):
        entries = self.all()[-last_n:]
        lines = [
            "# Trading Assistant - Knowledge Base",
            "",
            f"_Auto-generated {datetime.now().isoformat(timespec='seconds')}._",
            f"_Showing last {len(entries)} of {len(self.all())} entries._",
            "",
        ]
        for e in reversed(entries):
            lines.append(
                f"## {e.get('date')} | {e.get('category')} "
                f"(conf {e.get('confidence', 0):.2f}, src {e.get('source','?')})"
            )
            lines.append("")
            lines.append(f"**{e.get('claim','')}**")
            lines.append("")
            if e.get("evidence"):
                lines.append(f"_Evidence:_ {e['evidence']}")
                lines.append("")
            if e.get("tags"):
                lines.append(f"_Tags:_ `{' '.join(e['tags'])}`")
                lines.append("")
            lines.append("---")
            lines.append("")
        try:
            with open(self._md_path, "w") as f:
                f.write("\n".join(lines))
        except OSError as ex:
            logger.warning(f"KB markdown rewrite failed: {ex}")

"""
alerts/alert_store.py -- SQLite persistence for alerts, journal, and chat.

Single source of truth for the per-alert web app:
    - alerts            stores every scanner / SPY-daily alert ever fired
    - journal_entries   trader's manual notes + outcome per alert
    - chat_messages     full assistant chat history per alert

DB lives at logs/alert_store.db (config.LOG_DIR/alert_store.db).
The schema is created on first import; subsequent imports are idempotent.

Public API:
    save_alert(alert_dict)              -> alert_id  (8-char UUID)
    get_alert(alert_id)                 -> dict | None
    get_recent_alerts(limit=20)         -> list[dict]
    save_journal_entry(alert_id, entry) -> bool
    get_journal_entries(alert_id)       -> list[dict]
    save_chat_message(alert_id, role, content)
    get_chat_history(alert_id)          -> list[{"role": ..., "content": ...}]
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from loguru import logger

import config


# Allow tests / callers to override the DB path via env var.
DB_PATH = os.getenv(
    "ALERT_STORE_DB",
    os.path.join(config.LOG_DIR, "alert_store.db"),
)

_lock = threading.Lock()


# ─────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    alert_id    TEXT PRIMARY KEY,
    ticker      TEXT,
    regime      TEXT,
    play        TEXT,
    direction   TEXT,
    vix         REAL,
    ivr         REAL,
    adx         REAL,
    confidence  REAL,
    entry       REAL,
    stop        REAL,
    target      REAL,
    rr_ratio    TEXT,
    strategy    TEXT,
    metrics     TEXT,
    full_alert  TEXT,
    created_at  TEXT,
    source      TEXT
);

CREATE TABLE IF NOT EXISTS journal_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id        TEXT NOT NULL,
    took_trade      INTEGER,
    direction_agree INTEGER,
    notes           TEXT,
    outcome         TEXT,
    pnl             REAL,
    created_at      TEXT,
    FOREIGN KEY (alert_id) REFERENCES alerts(alert_id)
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id    TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT,
    FOREIGN KEY (alert_id) REFERENCES alerts(alert_id)
);

CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts(created_at);
CREATE INDEX IF NOT EXISTS idx_journal_alert ON journal_entries(alert_id);
CREATE INDEX IF NOT EXISTS idx_chat_alert ON chat_messages(alert_id, id);
"""


def _connect() -> sqlite3.Connection:
    """Open a connection with row-dict access enabled."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _init_schema() -> None:
    with _lock, _connect() as conn:
        conn.executescript(_SCHEMA)


_init_schema()


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_alert_id() -> str:
    return uuid.uuid4().hex[:8]


def _coerce_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _strip_non_serializable(d: dict) -> dict:
    """Drop values that json.dumps can't handle (e.g. dataclass _spy_setup)."""
    clean: dict[str, Any] = {}
    for k, v in d.items():
        try:
            json.dumps(v, default=str)
            clean[k] = v
        except (TypeError, ValueError):
            clean[k] = str(v)
    return clean


def _row_to_alert(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    if out.get("metrics"):
        try:
            out["metrics"] = json.loads(out["metrics"])
        except (TypeError, ValueError):
            pass
    if out.get("full_alert"):
        try:
            out["full_alert"] = json.loads(out["full_alert"])
        except (TypeError, ValueError):
            pass
    return out


# ─────────────────────────────────────────
# ALERTS
# ─────────────────────────────────────────

def save_alert(alert: dict) -> str:
    """
    Persist an alert dict and return the new alert_id.

    Reuses alert['alert_id'] if already set; otherwise generates an 8-char UUID.
    The full alert dict is round-tripped to JSON in `full_alert` so any field
    not explicitly columnar can still be retrieved later.
    """
    alert_id = alert.get("alert_id") or _new_alert_id()
    alert["alert_id"] = alert_id

    payload = _strip_non_serializable(alert)
    metrics = payload.get("metrics")
    if isinstance(metrics, dict):
        metrics_json = json.dumps(metrics, default=str)
    elif metrics is None:
        metrics_json = None
    else:
        metrics_json = json.dumps(metrics, default=str)

    row = (
        alert_id,
        payload.get("ticker"),
        payload.get("regime"),
        payload.get("play"),
        payload.get("direction"),
        _coerce_float(payload.get("vix")),
        _coerce_float(payload.get("ivr")),
        _coerce_float(payload.get("adx")),
        _coerce_float(payload.get("confidence")),
        _coerce_float(payload.get("entry")),
        _coerce_float(payload.get("stop")),
        _coerce_float(payload.get("target")),
        str(payload.get("rr_ratio")) if payload.get("rr_ratio") is not None else None,
        payload.get("strategy"),
        metrics_json,
        json.dumps(payload, default=str),
        payload.get("created_at") or _now_iso(),
        payload.get("source"),
    )

    try:
        with _lock, _connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO alerts
                    (alert_id, ticker, regime, play, direction, vix, ivr, adx,
                     confidence, entry, stop, target, rr_ratio, strategy,
                     metrics, full_alert, created_at, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
    except sqlite3.Error as e:
        logger.error(f"alert_store.save_alert({alert_id}) failed: {e}")
        raise

    return alert_id


def get_alert(alert_id: str) -> dict | None:
    """Return the alert row as a dict (with metrics/full_alert deserialized)."""
    with _lock, _connect() as conn:
        cur = conn.execute("SELECT * FROM alerts WHERE alert_id = ?", (alert_id,))
        return _row_to_alert(cur.fetchone())


def get_recent_alerts(limit: int = 20) -> list[dict]:
    """Most-recent-first list for the index page."""
    with _lock, _connect() as conn:
        cur = conn.execute(
            "SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?",
            (int(limit),),
        )
        return [_row_to_alert(r) for r in cur.fetchall()]


# ─────────────────────────────────────────
# JOURNAL
# ─────────────────────────────────────────

def save_journal_entry(alert_id: str, entry: dict) -> bool:
    """Insert one journal entry tied to an alert."""
    try:
        with _lock, _connect() as conn:
            conn.execute(
                """
                INSERT INTO journal_entries
                    (alert_id, took_trade, direction_agree, notes,
                     outcome, pnl, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert_id,
                    1 if entry.get("took_trade") else 0,
                    1 if entry.get("direction_agree") else 0,
                    entry.get("notes") or "",
                    entry.get("outcome") or "open",
                    _coerce_float(entry.get("pnl")),
                    entry.get("created_at") or _now_iso(),
                ),
            )
        return True
    except sqlite3.Error as e:
        logger.error(f"alert_store.save_journal_entry({alert_id}) failed: {e}")
        return False


def get_journal_entries(alert_id: str) -> list[dict]:
    """All journal entries for one alert, oldest first."""
    with _lock, _connect() as conn:
        cur = conn.execute(
            "SELECT * FROM journal_entries WHERE alert_id = ? ORDER BY id ASC",
            (alert_id,),
        )
        return [dict(r) for r in cur.fetchall()]


# ─────────────────────────────────────────
# CHAT
# ─────────────────────────────────────────

def save_chat_message(alert_id: str, role: str, content: str) -> None:
    """Append one chat message (user or assistant) to history."""
    if role not in ("user", "assistant"):
        raise ValueError(f"role must be 'user' or 'assistant', got {role!r}")
    try:
        with _lock, _connect() as conn:
            conn.execute(
                """
                INSERT INTO chat_messages (alert_id, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (alert_id, role, content, _now_iso()),
            )
    except sqlite3.Error as e:
        logger.error(f"alert_store.save_chat_message({alert_id}) failed: {e}")


def get_chat_history(alert_id: str) -> list[dict]:
    """Full ordered chat history as [{'role': ..., 'content': ...}, ...]."""
    with _lock, _connect() as conn:
        cur = conn.execute(
            """
            SELECT role, content
              FROM chat_messages
             WHERE alert_id = ?
             ORDER BY id ASC
            """,
            (alert_id,),
        )
        return [{"role": r["role"], "content": r["content"]} for r in cur.fetchall()]

# Notification Curation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pushover pushes ONLY actionable-play events (disciplined position opened, target/stop hit, expiry close); everything else is recorded silently; Discord removed from the live path.

**Architecture:** `Notifier` becomes Pushover-only with `play()` (push, priority 1) and `log()` (record, no push); `.alert()`/`.message()` become silent `log()` wrappers so push is opt-in. The scheduler threads a `play_fn` to the lifecycle jobs; the intraday scanner pushes `play()` only on a disciplined open. `main.py` drops the Discord bot thread/imports.

**Tech Stack:** Python 3.11, pytest, loguru. Touches `alerts/notifier.py`, `learning/scheduler.py`, `scanners/intraday_scanner.py`, `scanners/news_scanner.py`, `main.py`.

**Spec:** `docs/superpowers/specs/2026-06-02-notification-curation-design.md`

---

## Reference facts (verified — do not re-derive)

- **`alerts/notifier.py`**: `Notifier(pushover, discord_alert_fn, discord_message_fn)`. `.alert(alert, discord_message)` persists to `alert_store.save_alert(alert)` → `alert_id`, then `pushover.send_alert(alert, alert_id)`, then `discord_alert_fn`. `.message(raw)` calls `discord_message_fn` then `pushover.send_message(title, body, priority=-1)`. Imports: `from alerts import alert_store`, `from alerts.pushover_client import PushoverClient, strip_discord_markdown, extract_pushover_title`.
- **`alerts/pushover_client.py`**: `send(title, message, url=None, url_title=None, priority=0) -> bool`. `send_alert(alert, alert_id)` uses `PRIORITY_MAP[alert["tier"]]` + builds the deep-link URL. `send_message(title, body, priority=-1, url=None)`. `_build_alert_url(alert_id)` builds the deep link.
- **`learning/scheduler.py`**: `register_learning_jobs(scheduler, polygon_client, post_fn=None, ...)`. Jobs:
  - `job_paper_broker(...)` → `PaperBroker().execute_today()` (the 45DTE daily play; always book="disciplined"). Returns `{prediction_date, trade_id, recorded}`. **Currently does NOT notify.**
  - `job_exit_manager(polygon_client, vix_client, post_fn, dte_buckets)` → `closed = ExitManager(...).run(...)`; `if closed and post_fn: post_fn(format_exit_message(closed))`.
  - `job_expiry_resolver(polygon_client, post_fn)` → `closed = ExpiryResolver(...).resolve_expired()`; `if closed and post_fn: post_fn(format_expiry_message(closed))`.
  - `job_outcome_resolver`, `job_reflector`, `job_hypothesis_runner` → use `post_fn` (stay silent/log).
  - Jobs are registered with `kwargs={... "post_fn": post_fn ...}`.
- **`main.py`**: `notifier = Notifier(pushover, discord_alert_fn=post_alert_sync, discord_message_fn=post_message_sync)`; `swing_scanner.set_discord_fn(notifier.alert)`, `intraday_scanner.set_discord_fn(notifier.alert)`, `premarket/options_flow.set_discord_fn(notifier.message)`. `start_discord()` runs `run_bot` in a daemon thread (`discord_thread.start()`). Imports `from alerts.discord_bot import post_message_sync, scanner_status, post_alert_sync`. `register_learning_jobs(...)` and `register_spy_jobs(...)` are called with `post_fn=notifier.message`.
- **`scanners/intraday_scanner.py`**: the Phase-3 block sets `enriched["book"] = _assign_book_for_enriched(enriched)` then `result = broker.execute_signal(enriched)` (result `{trade_id, recorded}`). The scanner has a discord fn via `set_discord_fn`.
- **`scanners/news_scanner.py`**: `_post_news_to_discord(message)` imports `from alerts.discord_bot import bot` and uses `bot.loop`.

**Pre-flight:**
```bash
cd /home/nexus/Projects/stock-market-trading-assistant
source .venv/bin/activate
pytest tests/ -k "notifier or pushover or scheduler or intraday_scanner or news" -p no:cacheprovider -q | tail -3
git status --short   # clean; git checkout -b notification-curation
```

---

## Task 1: `Notifier` → Pushover-only with `play()` / `log()`

**Files:**
- Modify: `alerts/notifier.py`
- Test: `tests/test_notifier.py` (new — check first; create with the sys.path header if absent)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_notifier.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from unittest import mock
from alerts.notifier import Notifier


class _FakePushover:
    def __init__(self): self.sends = []
    def send(self, title, message, url=None, url_title=None, priority=0):
        self.sends.append({"title": title, "message": message, "url": url, "priority": priority})
        return True


def test_play_pushes_high_priority_and_persists(monkeypatch):
    px = _FakePushover()
    saved = {}
    monkeypatch.setattr("alerts.notifier.alert_store.save_alert", lambda a: saved.setdefault("id", "AID42") or "AID42")
    n = Notifier(px)
    n.play({"ticker": "SPY", "tier": "high_conviction"}, title="SPY play", body="opened iron_condor")
    assert len(px.sends) == 1
    assert px.sends[0]["priority"] == 1            # high — makes sound
    assert px.sends[0]["title"] == "SPY play"
    assert saved.get("id") == "AID42"              # persisted for the deep link


def test_play_without_alert_still_pushes(monkeypatch):
    px = _FakePushover()
    n = Notifier(px)
    n.play(title="Target hit", body="F6C4 +$80")   # lifecycle event, no alert dict
    assert len(px.sends) == 1 and px.sends[0]["priority"] == 1


def test_log_does_not_push(monkeypatch):
    px = _FakePushover()
    monkeypatch.setattr("alerts.notifier.alert_store.save_alert", lambda a: "X")
    n = Notifier(px)
    n.log({"ticker": "SPY", "tier": "standard"})
    n.log("morning briefing ...")
    assert px.sends == []                          # the anti-congestion guarantee


def test_alert_and_message_route_to_log_no_push(monkeypatch):
    px = _FakePushover()
    monkeypatch.setattr("alerts.notifier.alert_store.save_alert", lambda a: "X")
    n = Notifier(px)
    n.alert({"ticker": "SPY", "tier": "high_conviction"}, "full card")
    n.message("UOA flow ...")
    assert px.sends == []                          # legacy methods no longer push
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_notifier.py -q`
Expected: FAIL — `Notifier.__init__()` still requires the Discord args / `play`/`log` undefined.

- [ ] **Step 3: Write minimal implementation**

Replace the body of `alerts/notifier.py` (keep the module docstring updated):
```python
"""
alerts/notifier.py -- Pushover-only notification router.

Two intents:
    play(alert=None, *, title, body)  -> push (priority 1) + persist; the ONLY
                                          path that reaches the phone. Used for
                                          actionable plays (disciplined open,
                                          target/stop hit, expiry close).
    log(message_or_alert)             -> record only (persist alerts to the
                                          store, logger.info); NO push.

.alert()/.message() are kept as silent wrappers over log() so existing call
sites keep working without pushing — push is opt-in. Discord was removed
2026-06-02 (see the spec).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from loguru import logger

from alerts import alert_store
from alerts.pushover_client import (
    PushoverClient,
    strip_discord_markdown,
    extract_pushover_title,
    _build_alert_url,
)

PLAY_PRIORITY = 1   # Pushover high — makes sound


class Notifier:
    """Pushover-only router. play() pushes; log() is silent."""

    def __init__(self, pushover: PushoverClient):
        self.pushover = pushover

    # ── PLAY: actionable-play push (the only thing that reaches the phone) ──

    def play(self, alert: dict | None = None, *, title: str, body: str) -> None:
        url = None
        if alert is not None:
            try:
                alert_id = alert_store.save_alert(alert)
                if alert_id:
                    url, _ = _build_alert_url(alert_id)
            except Exception as e:
                logger.error(f"Notifier.play: alert_store.save_alert failed: {e}")
        try:
            self.pushover.send(title=title, message=body, url=url, priority=PLAY_PRIORITY)
        except Exception as e:
            logger.error(f"Notifier.play: pushover send failed: {e}")

    # ── LOG: record only, NO push ──────────────────────────────────────────

    def log(self, message_or_alert) -> None:
        if isinstance(message_or_alert, dict):
            try:
                alert_store.save_alert(message_or_alert)
            except Exception as e:
                logger.error(f"Notifier.log: alert_store.save_alert failed: {e}")
            logger.info(f"notify(log) alert: {message_or_alert.get('ticker','?')} "
                        f"{message_or_alert.get('strategy','')} tier={message_or_alert.get('tier','')}")
        else:
            logger.info(f"notify(log): {str(message_or_alert)[:200]}")

    # ── Legacy aliases — now SILENT (route to log, never push) ──────────────

    def alert(self, alert: dict, discord_message: str = "") -> None:
        self.log(alert)

    def message(self, raw_message: str) -> None:
        self.log(raw_message)
```
(`_build_alert_url` is already defined in `pushover_client.py`; `strip_discord_markdown`/`extract_pushover_title` imports are retained for compatibility even if unused now — verify they exist; drop them from the import if a linter flags unused.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_notifier.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add alerts/notifier.py tests/test_notifier.py
git commit -m "feat: Notifier becomes Pushover-only with play()/log(); legacy methods silent"
```

---

## Task 2: Scheduler — thread `play_fn` to the lifecycle jobs

**Files:**
- Modify: `learning/scheduler.py`
- Test: `tests/test_scheduler_play_routing.py` (new)

> **Implementer:** read `learning/scheduler.py` first. Add a `play_fn=None` param to `register_learning_jobs` and thread it (via `kwargs`) into `job_exit_manager`, `job_expiry_resolver`, and `job_paper_broker`. Those three call `play_fn(...)` for the urgent events; `job_outcome_resolver`/`job_reflector`/`job_hypothesis_runner` keep using `post_fn` (silent). When `play_fn is None`, fall back to a no-op so tests/other callers don't break.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_scheduler_play_routing.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from unittest import mock
import learning.scheduler as sch


def test_exit_manager_job_uses_play_fn(monkeypatch):
    plays = []
    monkeypatch.setattr(sch, "ExitManager", mock.Mock(return_value=mock.Mock(
        run=mock.Mock(return_value=[{"trade_id": "T1", "outcome": "win", "pnl_dollars": 80.0}]))))
    monkeypatch.setattr(sch, "format_exit_message", lambda closed: "target hit T1 +$80")
    monkeypatch.setattr(sch, "is_trading_day", lambda *_: True, raising=False)
    sch.job_exit_manager(polygon_client=mock.Mock(), vix_client=mock.Mock(),
                         post_fn=lambda m: plays.append(("post", m)),
                         play_fn=lambda **kw: plays.append(("play", kw.get("body"))),
                         dte_buckets=["45DTE"])
    assert ("play", "target hit T1 +$80") in plays
    assert not any(tag == "post" for tag, _ in plays)   # lifecycle goes to play, not post


def test_expiry_job_uses_play_fn(monkeypatch):
    plays = []
    monkeypatch.setattr(sch, "ExpiryResolver", mock.Mock(return_value=mock.Mock(
        resolve_expired=mock.Mock(return_value=[{"trade_id": "T2"}]))))
    monkeypatch.setattr(sch, "format_expiry_message", lambda closed: "expiry closed T2")
    monkeypatch.setattr(sch, "is_trading_day", lambda *_: True, raising=False)
    sch.job_expiry_resolver(polygon_client=mock.Mock(),
                            post_fn=lambda m: plays.append(("post", m)),
                            play_fn=lambda **kw: plays.append(("play", kw.get("body"))))
    assert ("play", "expiry closed T2") in plays
```

(Note: if the job functions guard on `is_trading_day`/`config.is_trading_day`, the test monkeypatches it; read the actual guard and patch the right symbol. If `ExitManager.run`'s method name differs, match the real one.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scheduler_play_routing.py -q`
Expected: FAIL — `job_exit_manager()` got an unexpected keyword argument `play_fn`.

- [ ] **Step 3: Write minimal implementation**

In `learning/scheduler.py`:
All `play_fn` calls use keyword `title`/`body` (so `play_fn = notifier.play` works
directly — `notifier.play(alert=None, *, title, body)`).
- `job_exit_manager(..., post_fn=None, play_fn=None, dte_buckets=...)`: replace the notify line with
  ```python
  if closed and play_fn:
      try:
          play_fn(title="⚠️ Exit — target/stop hit", body=format_exit_message(closed))
      except Exception as e:
          logger.warning(f"exit_manager play notify failed: {e}")
  ```
- `job_expiry_resolver(..., post_fn=None, play_fn=None)`: same pattern:
  ```python
  if closed and play_fn:
      try:
          play_fn(title="⏱️ Expiry close", body=format_expiry_message(closed))
      except Exception as e:
          logger.warning(f"expiry play notify failed: {e}")
  ```
- `job_paper_broker(..., play_fn=None)`: after `result = PaperBroker().execute_today()`, add
  ```python
  if play_fn and result.get("recorded"):
      try:
          play_fn(title="📈 Daily play opened",
                  body=f"45DTE disciplined play opened — {result.get('trade_id')}")
      except Exception as e:
          logger.warning(f"paper_broker play notify failed: {e}")
  ```
  (the 45DTE daily play is always the disciplined book).
- `register_learning_jobs(scheduler, polygon_client, post_fn=None, play_fn=None, ...)`: add `play_fn` to the kwargs of the paper_broker, exit_manager (both 45DTE and intraday registrations), and expiry_resolver job registrations. Leave outcome/reflector/hypothesis kwargs unchanged (post_fn only).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scheduler_play_routing.py tests/ -k scheduler -q`
Expected: PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add learning/scheduler.py tests/test_scheduler_play_routing.py
git commit -m "feat: scheduler threads play_fn to lifecycle jobs (open/target/stop/expiry push)"
```

---

## Task 3: Intraday scanner — `play()` only on a disciplined open

**Files:**
- Modify: `scanners/intraday_scanner.py`
- Test: `tests/test_intraday_scanner_play.py` (new)

> **Implementer:** read the Phase-3 block. Add a module-level `_PLAY_FN` hook with a `set_play_fn(fn)` setter (mirror the existing `set_discord_fn` pattern). After `result = broker.execute_signal(enriched)`, if the entry recorded AND `enriched["book"] == "disciplined"`, call the play hook; learning-book opens do NOT push. Keep everything inside the existing gate + try/except.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intraday_scanner_play.py
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from scanners.intraday_scanner import _maybe_play_on_open, set_play_fn


def test_disciplined_open_pushes_play():
    plays = []
    set_play_fn(lambda **kw: plays.append(kw))
    _maybe_play_on_open({"strategy": "iron_condor", "dte_bucket": "1-3DTE", "book": "disciplined"},
                        {"trade_id": "T9", "recorded": True})
    assert len(plays) == 1 and "T9" in plays[0]["body"]


def test_learning_open_does_not_push():
    plays = []
    set_play_fn(lambda **kw: plays.append(kw))
    _maybe_play_on_open({"strategy": "iron_condor", "dte_bucket": "0DTE", "book": "learning"},
                        {"trade_id": "T10", "recorded": True})
    assert plays == []


def test_unrecorded_does_not_push():
    plays = []
    set_play_fn(lambda **kw: plays.append(kw))
    _maybe_play_on_open({"book": "disciplined"}, {"trade_id": None, "recorded": False})
    assert plays == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_intraday_scanner_play.py -q`
Expected: FAIL — `cannot import name '_maybe_play_on_open'` / `set_play_fn`.

- [ ] **Step 3: Write minimal implementation**

Add near the top of `scanners/intraday_scanner.py` (module level):
```python
_PLAY_FN = None

def set_play_fn(fn):
    """Register the actionable-play push hook (notifier.play)."""
    global _PLAY_FN
    _PLAY_FN = fn


def _maybe_play_on_open(enriched: dict, result: dict) -> None:
    """Push a play() ONLY when a disciplined position actually opened.
    Learning-book (sandbox) opens stay silent."""
    if not (_PLAY_FN and result.get("recorded") and enriched.get("book") == "disciplined"):
        return
    try:
        _PLAY_FN(title=f"📈 Intraday play opened — {enriched.get('strategy')}/{enriched.get('dte_bucket')}",
                 body=f"{result.get('trade_id')} {enriched.get('strategy')} "
                      f"@ {enriched.get('dte_bucket')} (entry {enriched.get('entry_price')})")
    except Exception as e:
        from loguru import logger
        logger.warning(f"intraday play notify failed: {e}")
```
Then in the Phase-3 loop, immediately after `result = broker.execute_signal(enriched)`:
```python
                    _maybe_play_on_open(enriched, result)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_intraday_scanner_play.py -q && python -c "import scanners.intraday_scanner"`
Expected: PASS (3 passed) + clean import.

- [ ] **Step 5: Commit**

```bash
git add scanners/intraday_scanner.py tests/test_intraday_scanner_play.py
git commit -m "feat: intraday scanner pushes play() only on a disciplined open"
```

---

## Task 4: `main.py` — remove Discord, wire `Notifier(pushover)` + `play_fn`; neuter news Discord

**Files:**
- Modify: `main.py`, `scanners/news_scanner.py`
- Test: `tests/test_no_discord_wiring.py` (new) + import smoke

> **Implementer:** read `main.py` fully first. This is integration surgery — use the real symbol names. Make these changes:
> 1. **Notifier construction:** `notifier = Notifier(pushover)` (drop `discord_alert_fn`/`discord_message_fn`).
> 2. **Scanner wiring:** keep `swing_scanner.set_discord_fn(notifier.alert)` etc. (now silent log). ADD `intraday_scanner.set_play_fn(notifier.play)`.
> 3. **register_learning_jobs / register_spy_jobs:** pass `play_fn=notifier.play` in addition to the existing `post_fn=notifier.message`.
> 4. **Remove the Discord bot:** delete the `start_discord()` function, the `discord_thread = threading.Thread(...); discord_thread.start()` lines, and the `from alerts.discord_bot import post_message_sync, scanner_status, post_alert_sync` import. Remove any `_bot_ready`/channel-diagnostic references. If `scanner_status` is used elsewhere in main, replace with a local no-op/inline status.
> 5. **news_scanner:** make `_post_news_to_discord` a no-op (or route its message through the scanner's existing notifier/log path); remove `from alerts.discord_bot import bot`. News content is still persisted to `news_briefings.json`.
> If removing an import breaks another reference in main, trace it and replace with the silent path — do NOT re-add Discord. If a reference can't be cleanly resolved, report BLOCKED.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_no_discord_wiring.py
import os, sys, ast
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _src(path):
    with open(os.path.join(os.path.dirname(__file__), "..", path)) as f:
        return f.read()


def test_main_does_not_import_or_start_discord_bot():
    src = _src("main.py")
    assert "discord_bot" not in src, "main.py must not import alerts.discord_bot"
    assert "start_discord" not in src, "main.py must not start the Discord bot"


def test_main_constructs_pushover_only_notifier():
    src = _src("main.py")
    assert "discord_alert_fn" not in src and "discord_message_fn" not in src
    assert "set_play_fn(notifier.play)" in src
    assert "play_fn=notifier.play" in src


def test_news_scanner_does_not_import_discord_bot():
    src = _src("scanners/news_scanner.py")
    assert "from alerts.discord_bot import bot" not in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_no_discord_wiring.py -q`
Expected: FAIL — `main.py` still imports `discord_bot` / starts it.

- [ ] **Step 3: Make the changes** (per the implementer note above — edit `main.py` and `scanners/news_scanner.py`).

- [ ] **Step 4: Run test + import smoke**

Run: `pytest tests/test_no_discord_wiring.py -q && python -c "import main" 2>&1 | tail -3`
Expected: PASS; `import main` clean (no Discord import error). If `import main` triggers side effects, instead run `python -m py_compile main.py scanners/news_scanner.py` and assert clean compile.

- [ ] **Step 5: Commit**

```bash
git add main.py scanners/news_scanner.py tests/test_no_discord_wiring.py
git commit -m "feat: remove Discord from live path; wire Pushover-only Notifier + play_fn"
```

---

## Final verification

- [ ] **Offline gate:**

Run:
```bash
pytest tests/ -k "notifier or scheduler or intraday_scanner or news or pushover or no_discord" -p no:cacheprovider -q | tail -5
```
Expected: all green (the pre-existing live-FRED tests are not in this selection).

- [ ] **Update BUILD_LOG.md** (Pushover urgent-only, Discord removed, the urgent set, deploy = restart).

- [ ] **Deploy:** restart `smta.service`; confirm startup has no Discord lines and the bot thread is gone; a disciplined open / target / expiry produces a high-priority Pushover and nothing else does.

---

## Self-review (completed by author)

- **Spec coverage:** play()/log() + Pushover-only + silent legacy aliases (T1); play_fn threaded to disciplined-open + target/stop + expiry (T2 paper_broker/exit/expiry, T3 intraday disciplined open); Discord fully removed from live path + news neutered (T4); non-urgent → log/persist (T1 log + existing source persistence). All spec sections map to a task.
- **Placeholder scan:** none — complete code in each step; T2/T4 carry "read the surrounding code" notes because they edit large existing files, but inserted code is complete.
- **Type consistency:** `notifier.play(alert=None, *, title, body)` and `notifier.log(x)` consistent T1→T4. **All** `play_fn` call sites use keyword `title`/`body` — the scheduler lifecycle jobs (T2), the intraday scanner (T3), so `play_fn = notifier.play` is wired **directly** (no adapter); `main.py` passes `play_fn=notifier.play` and `set_play_fn(notifier.play)`. T2's tests use `play_fn=lambda **kw: ...` and assert on `kw["body"]`, matching. T4 asserts the literal `play_fn=notifier.play`. Consistent end-to-end.

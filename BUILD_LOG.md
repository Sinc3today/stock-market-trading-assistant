# BUILD_LOG.md — Claude Code Session History
# Append a new entry after every Claude Code session.
# Format: ## YYYY-MM-DD | [what was done] | [test result]

---

## 2026-05-16 (late night) | Codebase audit sweep — delete dead code, extract config, add tests

**Why:** After the cross-alert views landed, user asked for "a clean
sweep of the code and what needs to be optimized." Spawned an Explore
agent to audit the whole repo. The audit's HIGH findings were
overstated for a single-user bot (the "N+1 in TradeRecorder" is ~10ms
per page load) but it surfaced a handful of genuine cleanups worth
doing.

**What changed (2 commits):**

**Commit `872226a` — dead-code deletes**

- `alerts/dashboard.py` (1597 lines) — Streamlit dashboard never
  started by main.py. Superseded for mobile by the FastAPI
  `/trades` `/journal` `/chats` views landed earlier tonight.
  Confirmed no referrers in code or markdown.
- `write_backtest.py`, `write_init.py` — one-shot scaffolding scripts
  that wrote files which are now committed. No further purpose.
- `main.py` — dropped the "Dashboard runs separately" docstring hint
  and the "python -m streamlit run alerts/dashboard.py" startup-log
  line, since both pointed at the deleted file.

**Commit `83fd3b3` — config extraction + logging + tests**

- `config.py` — new constants per CLAUDE.md rule #9:
    - `POLYGON_RATE_LIMIT_SEC = 1.5`
    - `POLYGON_TIMEOUT_SEC    = 10`
    - `NEWS_ARTICLES_LIMIT    = 10`
- `scanners/news_scanner.py` — replaced 4 hardcoded magic numbers
  (`time.sleep(1.5)` ×2, `limit=10`, `timeout=10`, `[:10]`) with
  the new config constants.
- Silent JSON-load failures now log a warning instead of swallowing
  the exception. Missing-file behavior unchanged. Touched:
    - `journal/trade_recorder.py:_load`
    - `journal/lessons.py:_load`
    - `alerts/ai_advisor.py:get_history`
- `tests/test_learning_off_hours.py` (NEW, 6 tests) — covers replay
  with no near-misses, no CSV, no API key fallback, Claude JSON parse,
  malformed Claude reply, HTTP error swallowed. Fixture uses
  `monkeypatch.delenv("ANTHROPIC_API_KEY")` so we can't accidentally
  hit the live API (caught this when the first run did).
- `tests/test_learning_scheduler.py` (NEW, 9 tests) — verifies
  `register_learning_jobs` adds the 6 expected jobs with correct
  kwargs (polygon_client/post_fn on the outcome_resolver job,
  post_fn on the reflector job), and each `job_*` wrapper catches
  exceptions from its underlying module.

**Audit findings deliberately NOT acted on:**

- Helper extraction for JSON load + EST timestamp formatting
  duplicated across ~10 files. Premature abstraction for a
  single-user bot — would touch too many files for too little ROI.
- TradeRecorder N+1 file reads (re-loads trades.json on every getter).
  Agent flagged HIGH but real impact is ~10ms per /trades page load.
- Adding the missing tests for `data/`, `signals/`, `scanners/` modules.
  Real gap but not "cleanup" — backlog for a dedicated session.

**Test result:** 261 passed, 4 deselected, ~140s (was 246).
Net file-count change: -3 files, ~2000 lines deleted, ~270 added.

**Open follow-ups (updated order):**

1. ~~Live "prediction resolved" Pushover~~ ✅
2. Promotion workflow CLI (`python -m learning.promote <hyp_id>`)
3. ~~Web app cross-alert views~~ ✅ (`/learning` + `/hypotheses`
   pages still pending, depend on #2)
4. Expiry-based exit for `[AUTO-PAPER]` positions
5. VIX wiring in off-hours replay
6. Add `ruff` to a `pyproject.toml [tool.ruff]` block with
   E701/E702/E741/E402/E712 disabled so future lint runs only flag
   real issues
7. Backlog: add tests for `data/`, `signals/`, `scanners/` modules
   with no current coverage (~10 modules)

---

## 2026-05-16 (night) | Cross-alert /trades /journal /chats in FastAPI app

**Why:** After bookmarking the dashboard to the iPhone home screen, user
realized the page only shows recent alerts — they remembered building a
"whole app" with trades/journal/chat browsers. That richer app is the
Streamlit `alerts/dashboard.py` (1597 lines) which was never wired into
`main.py` and is desktop-shaped anyway. Decided to put mobile-native
aggregated views into the FastAPI app instead of starting Streamlit on
a second port — one URL, one bookmark, phone-first.

**What changed:**

- `alerts/alert_store.py` — two new helpers:
    - `get_all_journal_entries(limit=50)` — LEFT JOIN onto alerts so
      each journal row carries ticker/regime/direction without N+1.
    - `get_alerts_with_chat(limit=50)` — GROUP BY alert with
      msg_count, last_msg_at, last_msg subquery for previews.

- `alerts/web_app.py`:
    - `_NAV_CSS` chunk + `_render_nav(active)` + `_render_page()`
      shared wrapper. Sticky top bar with `[Alerts] [Trades] [Journal]
      [Chats]`. Horizontally scrollable on narrow screens.
    - `_render_trades(trades)` — newest first, status pill
      (OPEN/WIN/LOSS/BE), `+$xxx.xx` P&L with red/green color,
      `AUTO-PAPER` badge for `[AUTO-PAPER]`-tagged entries.
    - `_render_journal(entries)` — cross-alert feed, each card links
      back to `/alerts/{id}`.
    - `_render_chats(threads)` — alerts with ≥1 message, sorted by
      last message, shows count + last reply preview.
    - Three new routes: `GET /trades`, `GET /journal`, `GET /chats`.
    - Nav also injected into the existing `/alerts/{id}` detail page.

- `tests/test_web_app.py` — +7 tests:
    - Nav appears on index
    - /trades empty + populated (AAPL +$120 win round-trip)
    - /journal empty + populated (links back to alert)
    - /chats empty + populated (renders msg count + last reply preview)
    - Fixture extended: `monkeypatch.setattr(config, "LOG_DIR", ...)`
      so TradeRecorder's `trades.json` lands in tmp_path per test.

**Test result:** 246 passed, 4 deselected, ~144s (was 239).
**Commit:** `d26a4d1`.

**Runtime:** main.py restarted; verified all four routes return 200
from loopback and from `http://nexus-nucbox-k8-plus:8002/...`.
Bookmarked home-screen icon still works — same URL, just with the
nav bar across the top now.

**Open follow-ups (updated order):**

1. ~~Live "prediction resolved" Pushover~~ ✅
2. Promotion workflow CLI (`python -m learning.promote <hyp_id>`)
3. ~~Web app cross-alert views~~ ✅ (trades/journal/chats done; the
   originally-planned `/learning` + `/hypotheses` pages depend on the
   promotion CLI existing first)
4. Expiry-based exit for `[AUTO-PAPER]` positions
5. VIX wiring in off-hours replay
6. Add `ruff` to dev deps + tiny `[tool.ruff]` block disabling
   E701/E702/E741/E402/E712 so lint runs only flag what we care about
7. (new) Decide fate of `alerts/dashboard.py` — Streamlit dashboard is
   now superseded for mobile by the FastAPI views. Either:
   (a) keep as desktop-only deep-dive (document start command in
       README), or (b) delete and let the FastAPI app be the one UI.

---

## 2026-05-16 (late evening) | Migrated dashboard to Tailscale + lint sweep

**Why:** After bringing the Cloudflare tunnel up end-to-end (works fine),
realized the web app has zero auth — anyone with the public URL could
read the journal/alerts and burn Anthropic credits via `/chat`. Weighed
Tailscale-only vs Cloudflare Access. For a single-user dashboard on
devices already on the tailnet (Linux box, iPhone, Windows laptop),
Tailscale removes the OAuth/email-code redirect that Cloudflare Access
would add to every Pushover notification tap.

**What changed:**

- **`.env` (not committed — gitignored):**
    - `WEB_SERVER_HOST` 127.0.0.1 → 0.0.0.0
    - `PUSHOVER_BASE_URL` https://alerts.nexus-lab.work
      → http://nexus-nucbox-k8-plus:8002

- **`cloudflare/README.md` — rewritten as PARKED notes** (commit `b8acd35`).
  Status, why-Tailscale, when-to-un-park triggers, exact revival steps
  including the reminder to put Cloudflare Access in front of `/chat`
  before re-exposing publicly. Tunnel config and credentials kept
  in place — revival is `cloudflared tunnel run` + an `.env` flip.

- **Runtime state:**
    - `cloudflared` process stopped (was PID 162000).
    - Orphan `uvicorn` on :8000 from earlier session killed (PID 114782).
    - `main.py` restarted → web app now binds `0.0.0.0:8002`.
    - Verified all three paths return HTTP 200 on `/health`:
      `127.0.0.1`, tailnet IP `100.81.82.116`, MagicDNS hostname
      `nexus-nucbox-k8-plus`.

- **Lint sweep with ruff** (commit `e3e3f35`).
    - 71 auto-fixed: 56 redundant f-string prefixes, 9 unused imports,
      6 multi-imports-on-one-line.
    - 7 F841 unused-variable removals applied manually after confirming
      each was genuinely dead (none were missed assertions or planned
      side effects). Files touched: `alerts/dashboard.py`,
      `data/ivr_client.py`, `scanners/economic_scanner.py`, three
      test files.
    - Deliberately left untouched: 41 E701, 36 E702, 29 E741, 13 E402,
      7 E712. These are deliberate stylistic choices throughout the
      codebase (inline `if x: return`, `sys.path.insert(...); import`
      pattern, terse variable names). Not bugs.

**Test result:** 239 passed, 4 deselected (integration), ~140s.
Same count as before lint sweep — no behavioral change.

**Commits this session (cumulative):**
- `326348f` chore: move cloudflared tunnel to Linux host (port 8002)
- `527d492` feat: live "prediction resolved" notification at 16:05 ET
- `98120eb` docs: session log for resolved-prediction Pushover + tunnel move
- `b8acd35` docs: park Cloudflare tunnel notes — Tailscale chosen
- `e3e3f35` chore: lint sweep with ruff

**Access from any tailnet device:**
- Mobile-friendly URL: `http://nexus-nucbox-k8-plus:8002/`
- Per-alert deep links (what Pushover embeds):
  `http://nexus-nucbox-k8-plus:8002/alerts/{id}`
- Tailscale must be on. iOS may need to re-enable Tailscale after
  battery saver kills it — first tap after sleep can be slow.

**Open for next session (unchanged order):**

1. ~~Live "prediction resolved" Pushover~~ ✅ earlier this session
2. Promotion workflow CLI (`python -m learning.promote <hyp_id>`)
3. Web app `/learning` + `/hypotheses` routes
4. Expiry-based exit for `[AUTO-PAPER]` positions
5. VIX wiring in off-hours replay
6. (new) Add `pip install ruff` to dev deps so the lint sweep is
   repeatable. Maybe a tiny `pyproject.toml` `[tool.ruff]` block
   disabling E701/E702/E741/E402/E712 so future `ruff check` runs
   only flag the things we actually care about.

---

## 2026-05-16 (evening, laptop pickup) | Live prediction-resolved Pushover + tunnel moved to Linux

**Session length:** ~30 min after returning to the Linux box.

**Context:** Resumed from the morning handoff (commit `5b6a376`). User
killed the Windows-side `cloudflared` service from an admin PowerShell
so this Linux host could take over `alerts.nexus-lab.work`. Picked
Phase 2 follow-up #4 (live "prediction resolved" notification) as the
quick win.

**What changed:**

- `cloudflare/tunnel_config.yml` — credentials path moved to
  `/home/nexus/.cloudflared/…` and backend bumped from `:8000` → `:8002`
  to match `WEB_SERVER_PORT` in `.env`. Linux box is now the canonical
  tunnel host. (commit `326348f`)

- `learning/outcome_resolver.py` — new `format_resolved_message(prediction)`
  helper. Pure function; returns a 1-2-line Pushover/Discord summary
  (✅ CORRECT / ❌ WRONG / — skip) with SPY entry → close + % move.
  Skip days still emit a quiet heartbeat line.

- `learning/scheduler.py` — `job_outcome_resolver` now accepts `post_fn`
  and pings it with the formatted message right after a successful
  resolve. `register_learning_jobs` already had `post_fn` in scope from
  `main.py` (`notifier.message`), so the wiring is one kwarg deep.
  Notification failure is caught and logged so it can't break the job.

- `tests/test_learning_outcome_resolver.py` — 5 new tests:
  formatter output for correct/wrong/skip + scheduler job pings
  post_fn on success and skips it when there's no prediction.

**Test result:** 239 passed, 4 deselected (integration), ~141s
(was 234 before — no regressions, +5 new).

**Commits:**
- `326348f` chore: move cloudflared tunnel to Linux host (port 8002)
- `527d492` feat: live "prediction resolved" notification at 16:05 ET

**What this closes:** Phase 2 follow-up #4. Day-of feedback no longer
waits for the 19:01 reflector — the user gets a Pushover at 16:05 ET
saying whether today's directional call landed.

**Open for next session (in original handoff order):**

1. ~~Live "prediction resolved" Pushover~~ ✅ done this session
2. Promotion workflow CLI (`python -m learning.promote <hyp_id>`)
3. Web app `/learning` + `/hypotheses` routes
4. Expiry-based exit for `[AUTO-PAPER]` positions
5. VIX wiring in off-hours replay

Recommend tackling #2 (promotion CLI) next — accepted hypotheses
currently pile up in `logs/learning/hypotheses/` with no path to
production. Without #2, the weekly Saturday loop produces output
nobody acts on.

---

## 2026-05-16 | Self-learning loop scaffold (paper exec + reflection + hypothesis + backtest)

**Why:** Goal is an assistant that keeps building skill on its own when the
user can't trade or journal daily. The bot now generates its own predictions,
scores them itself overnight, reflects via Claude, and proposes one
backtestable improvement each week.

**What was built — new `learning/` package:**

- `learning/knowledge_base.py` — Append-only JSONL at
  `logs/learning/knowledge.jsonl` with categories `regime_accuracy`,
  `gate_quality`, `sizing`, `exit_timing`, `market_context`, `hypothesis`,
  `backtest_result`, `edge_case`. Re-generates a `KNOWLEDGE.md` rollup
  (last 50 entries, newest first) on every append. `KBEntry` dataclass
  clamps confidence to [0,1] and warns on non-standard categories.

- `learning/predictions.py` — `PredictionLog` writing one row per day to
  `logs/learning/predictions.jsonl`. Each prediction has regime, direction
  (bullish/bearish/neutral/skip), entry SPY, target, stop, plus a
  resolution block (`outcome` = correct/wrong/partial/skip,
  `actual_close`, `actual_move_pct`). Idempotent per date.
  `accuracy(n=60)` aggregates rolling directional accuracy %.

- `learning/paper_broker.py` — Runs at 09:16 ET. Reads today's plan from
  `PlanLogger`, logs a `Prediction`, and (if tradeable) records a paper
  position via `TradeRecorder` tagged `[AUTO-PAPER]` so it's distinct
  from real fills. Always size = 1 contract. Marks plan executed.
  Skip days still produce a prediction so skip-quality is learned too.

- `learning/outcome_resolver.py` — Runs at 16:05 ET. Fetches SPY EOD via
  injected `PolygonClient`, scores today's directional prediction
  (bullish/bearish: sign of move; neutral: |move| < 0.25%). Appends an
  `[MTM YYYY-MM-DD] SPY close $X` line to every open `[AUTO-PAPER]`
  trade so multi-day spreads accumulate a price path for the reflector.
  Idempotent — running twice doesn't double-resolve.

- `learning/reflector.py` — Runs at 19:01 ET. Bundles today's prediction
  + plan + open paper positions + last 14d KB + 30d accuracy into a
  Claude (Sonnet 4.5) call. Asks for a strict JSON reply with
  `summary`, `narrative`, and 1-3 `kb_entries`. Persists:
    - `logs/learning/reflections/YYYY-MM-DD.md` (narrative + context)
    - 1-3 new rows appended to KB
  If JSON parse fails, raw reply is still saved to the markdown so
  nothing is lost; KB simply isn't updated. Pushes summary via the
  notifier (Pushover + Discord).

- `learning/hypothesis_engine.py` — Runs Saturday 10:00 ET. Reads last
  30 days of KB + plans + accuracy and asks Claude for ONE concrete
  tunable change. Targets are constrained to a `TUNABLE_PARAMS`
  whitelist:
    - `signals.regime_detector.ADX_TREND_MIN`  (15.0 — 35.0)
    - `signals.regime_detector.VIX_CALM_MAX`   (12.0 — 22.0)
    - `config.SCORE_ALERT_MINIMUM`             (30 — 75)
    - `config.SCORE_HIGH_CONVICTION`           (55 — 90)
    - `config.MIN_RISK_REWARD_RATIO`           (1.0 — 3.0)
    - `config.IC_RANGE_THRESHOLD_PCT`          (1.5 — 4.0)
  Returns `status: "propose"` or `status: "none"` (with rationale).
  Validates module/var against whitelist and value against range —
  out-of-bounds or off-whitelist proposals are silently rejected.
  Stores spec at `logs/learning/hypotheses/hyp_YYYY-MM-DD_xxxx.json`.

- `learning/hypothesis_runner.py` — Runs Saturday 11:00 ET. Iterates
  pending hypothesis specs, monkey-patches the targeted module var,
  re-runs the 5-year SPY backtest (`backtests.spy_daily_backtest`,
  `--source local`), compares baseline vs modified deltas, and writes
  back to the spec:
    `sharpe_delta >= +0.10 AND pnl_delta > 0`     -> accepted
    `sharpe_delta <= -0.10 OR pnl_delta <= -250`  -> rejected
    else                                          -> inconclusive
  Original value restored in `finally` so a crash can't leak the
  override. Each result also appended to KB as `backtest_result`.
  **Accepted ≠ live** — promotion is a deliberate human step.

- `learning/off_hours_learner.py` — Runs Sunday 10:00 ET. Replays
  the last 60 days of SPY history through the *current* regime
  detector, flags "near-miss" days (ADX or VIX within 10% of
  threshold AND next-day move went against the directional call),
  asks Claude to find shared patterns, appends 1-3 `edge_case` /
  `market_context` KB entries. Always writes a JSON report at
  `logs/learning/off_hours/YYYY-MM-DD.json` whether or not Claude
  is reachable.

- `learning/scheduler.py` — `register_learning_jobs(scheduler,
  polygon_client, post_fn)` adds all six jobs onto the existing
  APScheduler. Each job wrapped in try/except so one failure can't
  crash the bot. Wired into `main.py` after the SPY daily jobs.

**Tests (34 new, all passing):**

- `tests/test_learning_kb.py` — 8 tests for KB + PredictionLog
  (append, recent, by_category, stats, confidence clamping,
  prediction idempotency, accuracy aggregation).
- `tests/test_learning_paper_broker.py` — 5 tests for paper broker
  (tradeable / skip / from-plan / no-plan / plan idempotency).
- `tests/test_learning_outcome_resolver.py` — 9 tests covering all
  direction × outcome combinations, skip days, idempotency, and
  MTM snapshotting against open positions.
- `tests/test_learning_reflector.py` — 3 tests with mocked
  `_call_claude` for happy path, malformed JSON, and missing API key
  (with `monkeypatch.delenv` to avoid hitting the live key in env).
- `tests/test_learning_hypothesis.py` — 9 tests for engine
  (valid / out-of-range / off-whitelist / status="none") and runner
  (accept / reject / inconclusive / re-run-skip / non-whitelist-error).

**File layout:**

```
learning/
  __init__.py
  knowledge_base.py
  predictions.py
  paper_broker.py
  outcome_resolver.py
  reflector.py
  hypothesis_engine.py
  hypothesis_runner.py
  off_hours_learner.py
  scheduler.py

logs/learning/
  knowledge.jsonl
  KNOWLEDGE.md
  predictions.jsonl
  reflections/YYYY-MM-DD.md
  hypotheses/hyp_*.json
  off_hours/YYYY-MM-DD.json
```

**Schedule summary (added to existing scheduler):**

```
09:16 ET (Mon-Fri)  learning.paper_broker.execute_today()
16:05 ET (Mon-Fri)  learning.outcome_resolver.resolve_today()
19:01 ET (Mon-Fri)  learning.reflector.reflect_today()
Sat 10:00 ET        learning.hypothesis_engine.propose_weekly()
Sat 11:00 ET        learning.hypothesis_runner.run_pending()
Sun 10:00 ET        learning.off_hours_learner.run()
```

**Known follow-ups for next session:**

1. **Web app surface** — `alerts/web_app.py` needs new routes:
   `/learning` (KB browser, prediction accuracy chart, recent
   reflections), `/hypotheses` (approve/reject pending accepted
   hypotheses to actually edit `config.py` / `regime_detector.py`).
2. **Expiry-based exit** — `outcome_resolver` only snapshots MTM on
   open `[AUTO-PAPER]` positions; nothing closes them at expiry yet.
   Add an `expiry_resolver` that closes positions when DTE hits 0
   using the realized SPY path and the spread's payoff function.
3. **Promotion workflow** — Accepted hypotheses sit in
   `logs/learning/hypotheses/` until a human acts. Need a CLI
   (`python -m learning.promote <hyp_id>`) that writes the change
   to source with a generated commit, plus a Pushover ping on
   accept so the user knows there's something to review.
4. **Live notification when a prediction is resolved** — currently
   only the daily reflection at 19:01 surfaces it. A short
   "prediction X today resolved Y" Pushover at 16:06 would close
   the day-of feedback loop.
5. **VIX in off-hours replay** — currently hardcoded to 16.0 because
   we don't have historical VIX in the local CSV. Wire the CBOE CSV
   loader (already in `data/vix_client.py`) into the replay.

**Session handoff (transferring to laptop):**

- All work committed in `6591605`. Pull on the laptop:
  `git pull origin main`
- First thing to verify the loop is alive end-to-end on the laptop:
  `pytest tests/test_learning_*.py -v` (expect 34 passing)
- Then `python main.py` will start the bot with the six new learning
  jobs already wired into the scheduler.
- The first paper trade + prediction will land at 09:16 ET the next
  weekday; the first reflection at 19:01 ET that same day.
  `logs/learning/` will populate from there. Nothing to do but watch.
- Pick the Phase 2 follow-up to attack first — recommendation is #3
  (promotion workflow), since without it accepted hypotheses just pile
  up. #4 (live "resolved" Pushover) is the easiest quick win.

---

## 2026-04-30 | Per-alert web app (FastAPI + SQLite + Claude chat + journal)

**What was built:**

- `alerts/alert_store.py` — SQLite store at `logs/alert_store.db` with three
  tables: `alerts`, `journal_entries`, `chat_messages`. Idempotent schema
  init on import; WAL mode; thread-locked writes. 8-char UUID alert IDs.
  Public API: `save_alert / get_alert / get_recent_alerts /
  save_journal_entry / get_journal_entries / save_chat_message /
  get_chat_history`.

- `alerts/web_app.py` — FastAPI app, single-string HTML (no Jinja2, no static
  files). Mobile-friendly dark UI. Routes:
  `GET /health`, `GET /` (recent alerts list),
  `GET /alerts/{id}` (per-alert detail page with three sections:
  Alert Details, Chat, Journal), `POST /alerts/{id}/chat`,
  `GET|POST /alerts/{id}/journal`. Chat persisted to DB so history
  survives across sessions. Anthropic SDK with prompt caching on the
  trading-coach preamble; per-alert context appended uncached.

- `alerts/notifier.py` — migrated from JSON-file persistence to
  `alert_store.save_alert()`. Save happens before the Pushover send so the
  deep link URL embedded in the notification resolves to a real DB row.

- `alerts/pushover_client.py` — `_build_alert_url` now uses
  `config.PUSHOVER_BASE_URL` and the plural `/alerts/{id}` route, with
  url_title `"View Trade + Chat"` (was `DASHBOARD_BASE_URL` and
  singular `/alert/{id}`).

- `main.py` — replaced the in-thread `start_web_server()` (which imported
  `web.app` and ran uvicorn programmatically) with a `subprocess.Popen`
  block that runs `uvicorn alerts.web_app:app` as a separate process,
  using config-driven `WEB_SERVER_HOST`/`WEB_SERVER_PORT`. Process is
  terminated cleanly in the `KeyboardInterrupt` shutdown path.

- `cloudflare/tunnel_config.yml` + `cloudflare/README.md` — tunnel config
  scoped to `alerts.nexus-lab.work` with ingress to `localhost:8000`,
  plus full setup instructions.

- `tests/test_web_app.py` — 6 TestClient tests against an isolated tmp
  SQLite DB (via `ALERT_STORE_DB` env var), with `_ask_claude` patched
  out so no Anthropic key is required.

**Removed (Option A — clean replacement, not coexistence):**

- `web/app.py`, `web/templates/alert.html`, `web/static/style.css`,
  `web/__init__.py` — superseded by `alerts/web_app.py`.
- `config.py` `DASHBOARD_BASE_URL` — replaced by `PUSHOVER_BASE_URL`
  added in the prior commit. Single source of truth for the public host.
- `install_hooks.py` + `post-commit` (root) + `.git/hooks/post-commit`
  — the auto-overwriting BUILD_LOG hook flagged in the previous session.
  Killed it so curated session logs survive commits going forward.

**Test count:** 200 passed, 4 deselected (integration), ~118s.
Was 194 before (no regressions); 6 new web app tests added.

**Known follow-ups:**

1. **Cloudflare tunnel ID** is still `TUNNEL_ID_PLACEHOLDER` in
   `cloudflare/tunnel_config.yml`. Run the steps in `cloudflare/README.md`
   (auth, create tunnel, route DNS) and replace the placeholder with the
   real UUID before the `https://alerts.nexus-lab.work/alerts/<id>`
   links go live end-to-end.
2. `.env` still has a `DASHBOARD_BASE_URL` line. Harmless (nothing reads
   it now) but worth deleting on the next pass.
3. `pushover_client.send()` still doesn't accept a `sound` kwarg — would
   let production code route different sounds per alert tier (e.g.
   `cashregister` for high-conviction). Easy follow-up.

---

## 2026-04-30 | Pushover live test + PUSHOVER_BASE_URL config

**What was built:**

- `tests/test_pushover_live.py` — standalone (non-pytest) script that
  POSTs directly to the Pushover REST API with `priority=1`,
  `sound=cashregister`, and a clickable `url` + `url_title`. Bypasses
  `PushoverClient.send()` because that method doesn't accept a `sound`
  kwarg yet.
- `config.py` — added `PUSHOVER_BASE_URL` (default
  `https://alerts.nexus-lab.work`) for building per-alert deep links.

**Test result:** Live notification confirmed received on device with
correct title, body, link, and cash-register sound.

**Commit:** `433a494`.

---

## 2026-04-30 | Weekend guard + options flow wiring + cleanup pass

**What was changed:**

- **Weekend / market-hours guards** — verified already present in
  working tree, committed: `swing_scanner.py` weekday check, `intraday_scanner.py`
  `weekday() >= 5` short-circuit in `is_market_hours()`, `main.py`
  `IntervalTrigger(start_date=next_open)` so intraday scan doesn't fire
  before 9:30 AM ET on first start.
- **Options flow scanner wired into main.py** at 09:35 ET via the
  `Notifier` router (Pushover primary + Discord secondary).
- **Cleanup pass:** removed duplicate imports in `main.py`; deleted stray
  `scheduler/scheduler__init__.py` typo file; pyflakes-driven removal of
  16 unused imports across 16 files; AST scan + closed 20 missing
  docstrings on classes / public methods.

**Tests:** 194 passed, 4 deselected (integration). No assertions modified.

**Issues flagged for follow-up:**

1. `install_hooks.py` + `post-commit` install a hook that auto-overwrites
   BUILD_LOG.md from `git log` — directly conflicts with the curated
   manual format. *Resolved in the next session (see entry above).*
2. `.claude/settings.local.json` is now gitignored under `.claude/`.
3. `TRADING_ASSISTANT.md` still says "157+ tests passing" — outdated;
   real number was 194 then, 200 now.

**Commit:** `29eb630`.

---

## 2026-04-30 | Scaffolding — CLAUDE.md, TRADING_ASSISTANT.md, BUILD_LOG.md, STRATEGY_LOG.md

**What was set up:**
- Created project scaffold files to bridge Claude.ai and Claude Code sessions
- CLAUDE.md: full architecture map, tuned thresholds, standing rules
- TRADING_ASSISTANT.md: current project state, active decisions, parking lot
- BUILD_LOG.md: this file
- STRATEGY_LOG.md: synced to Google Drive for Claude.ai continuity

**Current test baseline:**
- 157 tests passing (run: `pytest tests/ -v -m "not integration" --tb=short`)

**Git status at session end:**
- All previous work committed and pushed to main
- Scaffold files added in this session

**Next session should:**
1. Run `/init` in Claude Code to index the codebase against CLAUDE.md
2. Apply weekend guard fix to swing_scanner.py and intraday_scanner.py
3. Fix IntervalTrigger start_date in main.py
4. Wire options_flow_scanner.py into main.py scheduler
5. Clean up duplicate imports and missing __init__.py files
6. Run full test suite to confirm 157+ still passing

---

## 2026-04-29 | SPY Daily Strategy — full integration into main.py

**What was built:**
- Fixed main.py: removed duplicate imports, added PolygonClient import,
  wired VIXClient + IVRClient + EventCalendar into register_spy_jobs()
- Bot now starts with SPY daily jobs registered (09:15, 16:30, 19:00 ET)
- Confirmed Discord alerts posting correctly (manual test passed)

**Tests:** 157/157 passing

---

## 2026-04-28 | Backtest harness + threshold tuning

**What was built:**
- backtests/spy_daily_backtest.py: 5-year SPY replay engine
- download_spy.py + write_backtest.py: yfinance data pipeline (Windows-safe)
- backtests/spy_history.csv: 1255 bars (2021-2026)

**Backtest results after tuning:**
- ADX_TREND_MIN raised 20 -> 25
- VIX_CALM_MAX tightened 18 -> 17
- TRENDING_HIGH_VOL set to not tradeable
- Final: Win rate 50.3% | Sharpe 1.73 | P&L +$11,550 over 5 years
- Iron condor: 74.1% win rate, +$9,540 (core edge confirmed)

**Tests:** 157/157 passing

---

## 2026-04-27 | Event calendar + VIX/IVR clients + Discord race condition fix

**What was built:**
- data/vix_client.py: VIX via Polygon or CBOE CSV fallback
- data/ivr_client.py: IV Rank via options chain or VIX proxy
- data/event_calendar.py: FOMC, CPI, NFP, OPEX — auto-computed, weekly cache
- alerts/discord_bot.py: fixed race condition (_bot_ready gate),
  fixed channel cache miss, added post_message_sync()

**Tests:** 180/180 passing (157 existing + 23 new event calendar tests)

---

## 2026-04-26 | SPY daily regime strategy stack

**What was built:**
- signals/regime_detector.py: 6-regime ADX+VIX+IVR classifier
- signals/spy_daily_strategy.py: regime -> OptionsLayer orchestrator
- scheduler/spy_daily_scheduler.py: APScheduler jobs (09:15/16:30/19:00 ET)
- journal/plan_logger.py: planned trade log (separate from trade_recorder)

**Tests:** 157/157 passing

---

## 2026-04-25 | Options flow scanner (UOA)

**What was built:**
- scanners/options_flow_scanner.py: unusual options activity scanner
  Detects VOL/OI spikes, OTM monsters, high IV event bets
  Works on Polygon free tier (prev-day data, no real-time feed)
- tests/test_options_flow_scanner.py: 18/18 passing

**Status:** Built and tested. NOT yet wired into main.py scheduler.
Wire-in is next Claude Code session task.

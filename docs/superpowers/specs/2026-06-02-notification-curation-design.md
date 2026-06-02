# Notification Curation — Pushover urgent-only, Discord removed — Design

**Date:** 2026-06-02
**Status:** Design approved; ready for implementation plan.
**Branch (anticipated):** `notification-curation`

## Problem & Goal

Pushover is congested: the `Notifier` router fans **every** event to both Pushover
and Discord, from many sites (scanners, scheduler, learning loop, briefings,
reflections). Informational `.message()` events all push (priority −1) and
standard scan alerts push at priority 0, so the actionable "a play to make/manage
now" signals are buried. The user wants Pushover **curated to only urgent,
actionable-play signals** (to start making profitable plays) and **Discord
removed entirely**.

**Goal:** push only the curated urgent set; everything else is recorded
(dashboard + logs) but silent; remove Discord.

## Key Decisions

1. **Urgent = actionable plays only** (user): a disciplined play opening, or a
   play needing management (target/stop/expiry). Everything else → no push.
2. **Discord removed entirely** (user): no bot thread, token, `_bot_ready` gate,
   or channel config in the live path.
3. **Push is opt-in, not the default** (architecture): silence is safe; a future
   scanner can't re-congest by accident. Only sites that call `play()` push.

## Architecture

### `alerts/notifier.py` — Pushover-only, two intents

- `Notifier(pushover)` — constructor drops the two Discord fn args.
- **`play(alert: dict | None = None, *, title: str, body: str)`** — the
  actionable-play path: if an `alert` dict is given, persist it to `alert_store`
  (so the Pushover deep link resolves to the alert page); then
  `pushover.send(title, body, url=<deep link>, priority=1)` (high, sound).
- **`log(message_or_alert)`** — record-only: persist alerts to `alert_store`;
  `logger.info` the event; **no Pushover, no Discord**.
- `.alert(alert, discord_message)` and `.message(raw_message)` are **kept as thin
  wrappers that call `log()`** (signatures unchanged so existing call sites keep
  working) — they no longer push. This makes silence the default; only explicit
  `play()` calls reach the phone.

### `main.py` — remove Discord

- Delete the Discord bot startup: the `start_discord` thread, token load,
  `_bot_ready` gate wait, channel diagnostics.
- Construct `Notifier(pushover)` (no Discord fns). Scanner wiring
  (`set_discord_fn(notifier.alert)` / `notifier.message`) stays — those now route
  to `log()` (silent), which is correct for standard alerts / flow.

### `alerts/discord_bot.py` — dormant, unwired

- Left in the repo but **not imported or started** anywhere in the live path
  (low-risk vs a hard delete now; deletion is a noted follow-up).
- `scanners/news_scanner._post_news_to_discord` is neutered — news flows through
  `log()`; remove its `from alerts.discord_bot import bot` usage.

## The urgent set (the only `play()` call sites)

| Event | Source | Route |
|---|---|---|
| Disciplined position opened (45DTE daily play + intraday entries clearing the disciplined bar) | `paper_broker.execute` / `execute_signal` (book=="disciplined") | **`play()`** |
| Learning-book (sandbox) position opened | `execute_signal` (book=="learning") | `log()` |
| Profit-target hit / stop hit | `exit_manager` | **`play()`** |
| Expiry close | `expiry_resolver` | **`play()`** |
| Prediction scored / EOD MTM snapshot | `outcome_resolver` | `log()` |
| Morning/midday/EOD briefings | `news_scanner` | `log()` |
| Daily reflection | `reflector` | `log()` |
| UOA flow / economic releases / standard & low-conviction scans | flow/economic/swing scanners | `log()` |

**Wiring note (for the plan):** the learning-loop jobs (`exit_manager`,
`expiry_resolver`) and `paper_broker` currently receive only `post_fn =
notifier.message` (which is now silent). To let them push, the scheduler must
pass them a handle to `notifier.play` (e.g. a `play_fn` param alongside the
existing `post_fn`), or pass the `Notifier` itself. The plan resolves the exact
injection; the call sites decide push (`play`) vs silent (`log`/`message`) per
the table above.

**Consolidation:** the "high-conviction routed ENTRY signal" is pushed **when a
disciplined position opens** (the routed entry materializing) — collapsing
"scan alert" + "position opened" into a single push. Standalone high-conviction
scan alerts that don't open a disciplined position (cap-gated, or sandbox) →
`log()`. **A push therefore means: the bot made a disciplined play (enter), or a
play needs managing (target/stop/expiry) — nothing else.**

## Data flow

```
disciplined open / target / stop / expiry → notifier.play(...) → alert_store + Pushover(priority=1)
everything else → notifier.log(...) → alert_store (alerts) / logger.info → dashboard + logs, NO push
Discord: not in the path at all
```

## Non-urgent persistence (nothing lost)

- Alerts via `log()` → `alert_store.db` → rendered by the web dashboard.
- Plain-message content is already persisted at the source (`news_briefings.json`,
  reflection MD files, `spy_daily_plans.json`, etc.); `log()` adds a `logger.info`
  record. Dashboard + source files + `app.log` are the review surface.
- No new notifications store (YAGNI). Optional follow-up: a "muted feed" dashboard
  tab.

## Error handling

- `play()` / `log()` wrap Pushover + `alert_store` writes in try/except — a
  notification failure never crashes the caller; trading proceeds regardless.
- Removing Discord deletes the `_bot_ready` race-condition surface entirely.

## Testing (TDD)

- `play()` calls `pushover.send` at priority 1 AND persists the alert; never
  touches Discord (constructor has no Discord fn).
- `log()` persists/records but **does not** call `pushover.send` (the core
  anti-congestion guarantee).
- `.alert()` / `.message()` route to `log()` — regression guard that they no
  longer push.
- Call-site routing (inject a fake notifier capturing `play` vs `log`):
  disciplined open → `play`; learning-book open → `log`; target/stop/expiry →
  `play`; briefing/reflection/outcome → `log`.
- Startup smoke: `main` constructs `Notifier(pushover)` (no Discord args); no
  `discord_bot` import in the live path; bot thread not started.

## Follow-ups (noted, not in this spec)

- Hard-delete `alerts/discord_bot.py` + `post_*_sync` once Discord-less operation
  is confirmed comfortable.
- Optional "muted notifications" dashboard tab to skim non-urgent events in one place.

# Tradier auto-execution migration — pre-built plan

**Status: PARKED.** This is the documented transition path so the switch is
fast execution, not fresh design, when it's earned. **Do not build any of this
until the gate below is cleared.**

## The gate (why this is parked, not started)

Automated placement is a *reward for a proven live edge*, never a shortcut to
one. Wiring a bot to place real orders before we know the strategy makes money
on real fills just loses money faster and unattended. The precondition, same
discipline the bot applies to its own hypotheses:

> A live-proving sleeve (real 1-lot fills, manual on Robinhood) confirms the
> paper condor win rate (~70%+) survives real slippage over ~20–30 closed
> trades across at least one regime change — **then** Tradier.

As of 2026-07-18 we have **one** real fill. The gate is not close. Everything
below waits.

## Why Tradier (recap)

Robinhood has no supported trading API; `robin_stocks` order calls violate ToS
and risk account lockout — so RH stays **read-only** (see `learning/rh_sync.py`,
whose test bans the order functions). Alpaca was an early data-layer note and
its options-order support is thinner. Tradier is a real brokerage with a
documented multi-leg options order API and a full paper **sandbox**, which is
exactly what a defined-risk condor/spread book needs.

## Prerequisites (account side — the human, one-time)

1. **Open a Tradier Brokerage account** (the brokerage product, not just a
   Tradier-powered third-party app). Fund it with the live-proving sleeve only.
2. **Options approval level.** Iron condors and vertical spreads are
   defined-risk multi-leg strategies — these need spread-trading approval
   (commonly "Level 3" tier; exact label is Tradier's). Apply for it at account
   open; without it the multileg endpoint rejects our orders.
3. **Tokens.** For a *single personal account* we do **not** need the partner
   OAuth flow — a **personal Access Token** from the Tradier developer portal is
   enough (Bearer token, per-account). The partner/OAuth Authorization-Code flow
   is only for apps placing orders on *other people's* accounts, which we are
   not. Sandbox uses its own static bearer token. Store both in `.env`
   (`TRADIER_TOKEN`, `TRADIER_SANDBOX_TOKEN`, `TRADIER_ACCOUNT_ID`) — never
   committed, same as every other secret.

## API surface we'd use

Base: `https://api.tradier.com/v1` (live) / `https://sandbox.tradier.com/v1`
(paper). All calls: `Authorization: Bearer <token>`, `Accept: application/json`.

| Need | Endpoint |
|---|---|
| Balances / buying power | `GET /accounts/{id}/balances` |
| Open positions | `GET /accounts/{id}/positions` |
| Option chain (strikes, greeks) | `GET /markets/options/chains` |
| **Place condor/spread** | `POST /accounts/{id}/orders` `class=multileg` |
| Order status | `GET /accounts/{id}/orders/{order_id}` |
| Cancel | `DELETE /accounts/{id}/orders/{order_id}` |

Rate limits (per token, per minute): trade endpoints **60/min**, market data &
accounts **120/min**. Our cadence (a couple of opens/day, 15-min position polls)
is nowhere near these.

## Order mapping — our legs → Tradier multileg

`signals.condor_calc.build_condor()` already emits exactly what we need. Its
`legs` are `{action: BUY|SELL, option_type: CALL|PUT, strike, expiry}`. The
translation is mechanical:

**OCC option symbol** = root + `YYMMDD` + `C|P` + strike×1000 zero-padded to 8.
- `SPY` 639 CALL exp 2026-07-31 → `SPY260731C00639000`
- `SPY` 617 PUT  exp 2026-07-31 → `SPY260731P00617000`

**Side** (opening a new structure): `BUY → buy_to_open`, `SELL → sell_to_open`.
(Closing later: `sell_to_close` / `buy_to_close`.)

**Multileg params** — legs indexed `[0..3]`, one per condor leg:
```
class      = multileg
symbol     = SPY               # underlying root
type       = credit            # condor collects a net credit
duration   = day
price      = <net credit>      # limit = build_condor()['credit']
option_symbol[0] = SPY260731C00644000   side[0] = buy_to_open    quantity[0] = N
option_symbol[1] = SPY260731C00639000   side[1] = sell_to_open   quantity[1] = N
option_symbol[2] = SPY260731P00612000   side[2] = buy_to_open    quantity[2] = N
option_symbol[3] = SPY260731P00617000   side[3] = sell_to_open   quantity[3] = N
```
A vertical spread is the same with 2 legs; a butterfly maps to a 3-strike /
4-leg (`copilot_log.build_live_trade_kwargs` already produces the leg list).

**Preview before submit.** The endpoint accepts `preview=true`, which returns
cost, margin requirement and commission **without placing**. This is our
built-in parity gate: submit a preview, assert Tradier's max-loss/margin matches
`build_condor()['max_loss']` within tolerance, and only then re-POST with
`preview=false`. A mismatch means our model and the broker disagree — abort, log.

## Architecture

Introduce a thin broker abstraction so execution is swappable and testable:

```
brokers/
  base.py            Broker protocol: get_positions, preview_order, place_order,
                     cancel_order, get_order  (raises on any close/modify of
                     positions we didn't open)
  tradier_client.py  REST impl; sandbox/live chosen by TRADIER_LIVE flag
  order_mapper.py    build_condor()/butterfly legs -> multileg params + OCC sym
                     (pure, fully unit-testable, no network)
```

`order_mapper.py` is pure functions — the OCC-symbol builder and the leg→params
transform get exhaustive unit tests with zero network, the same way the rh_sync
pure functions are tested today.

### Safety model — rh_sync inverted, with hard rails

`rh_sync` proves the pattern: strict capability limits enforced by tests. This
module is the dangerous inverse (it *can* place), so the rails are heavier:

1. **`TRADIER_LIVE` env flag, default `False`.** Everything runs against the
   **sandbox** until a human flips one flag. Sandbox-first is the default state,
   not a testing afterthought.
2. **Preview-parity gate** (above) mandatory before any live submit.
3. **Whitelist of order classes**: `multileg`/`option` defined-risk only.
   Naked/undefined-risk structures are rejected in code, not by policy.
4. **Reuse the gates we already trust** — no new risk logic invented:
   - `config.within_entry_window()` on every open (09:45–15:00 ET).
   - `MAX_DAILY_DISCIPLINED_OPENS = 2`, `MIN_ENTRY_SPACING_MIN = 90`,
     `CONCENTRATION_GUARD_PCT = 1.5`, `DISCIPLINED_BUCKET_SLOTS`.
   - Per-order max-risk cap (dollar ceiling on `max_loss × N`).
5. **Never touch positions we didn't open.** `place_order` only opens; a close
   path only closes trades with our own `trade_id` provenance. No blanket
   cancel/modify — mirrors the rh_sync read-only guard's spirit.
6. **Kill switch**: `TRADIER_LIVE=False` (or `ENFORCE_ENTRY_WINDOW=False`)
   halts all opens immediately; exits/management always remain allowed.
7. **Guard test** (like rh_sync's order-ban test, inverted): assert the client
   *cannot* place anything but whitelisted defined-risk classes, and *cannot*
   place while `TRADIER_LIVE` is False against the live host.

## Phased rollout (once the gate is cleared)

1. **Sandbox parity** — run the real signal flow against `sandbox.tradier.com`
   for ~2 weeks. Every open previews + submits on paper; assert
   Tradier's numbers match our model. No real money.
2. **Live, human-in-the-loop** — `TRADIER_LIVE=True`, but the bot only
   *previews* and pushes an approve alert (the copilot approve flow we already
   have); the human taps approve, *then* the bot submits. 1-lot. This is
   auto-execution with a manual trigger — the safest first live step.
3. **Full auto** — drop the manual approve for the proven bucket(s) only
   (likely short-DTE condors first, since that's what's holding up), keeping all
   the pacing/risk gates. Directional/45DTE stay manual-approve until each earns
   the same proof independently.

## Testing plan

- `order_mapper` pure unit tests: OCC symbol edge cases (padding, puts, weird
  strikes), condor/spread/butterfly leg→params, side mapping open vs close.
- `tradier_client` against sandbox behind an `integration` marker (needs the
  sandbox token; excluded from the fast suite, same convention as other
  integration tests).
- Guard test: whitelist + `TRADIER_LIVE` false-blocks-live.
- Preview-parity test: model `max_loss` vs sandbox preview margin.

## Effort / file estimate

New: `brokers/base.py`, `brokers/tradier_client.py`, `brokers/order_mapper.py`,
`tests/test_order_mapper.py`, `tests/test_tradier_client.py` (integration),
`tests/test_tradier_guards.py`. Touch: `config.py` (flags + token names),
`.env`/`.gitignore`, the copilot approve path to call `place_order` on approve.
Roughly a **2–3 day build** once specced, most of it in mapper tests and the
sandbox parity loop — deliberately front-loaded on safety.

## Open risks / things to decide at build time

- **Assignment / pin risk** on short strikes near expiry — auto-close rules
  before expiry (our 21-DTE / 70%-target management already covers most of it).
- **Partial fills** on multileg — how we reconcile a partially-filled condor
  (Tradier fills multileg as a package, but confirm behavior in sandbox).
- **PDT rule** if the sleeve is < $25k and we ever round-trip same-day (the
  short-DTE bucket mostly holds overnight, so likely fine — verify).
- **Tax lots / 1099** — Tradier issues its own; the journal stays the P&L
  source of truth, Tradier is the execution + reconciliation feed.

---
*Verified against Tradier docs 2026-07-18. Re-check the endpoint shapes and
options-approval label at build time — APIs drift.*

Sources: [Place Multileg Order](https://documentation.tradier.com/brokerage-api/trading/place-multileg-order),
[Getting Started](https://docs.tradier.com/docs/getting-started),
[OAuth Authentication](https://docs.tradier.com/docs/authentication),
[Rate Limiting](https://docs.tradier.com/docs/rate-limiting).

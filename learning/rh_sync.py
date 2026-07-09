"""learning/rh_sync.py -- Robinhood READ-ONLY position sync.

Pulls the user's open option positions from Robinhood (robin_stocks) so the
copilot tracks their real trades without manual logging. The bot can't read RH
otherwise (no API), so this is the bridge — but it is STRICTLY READ-ONLY:

    *** THIS MODULE MUST NEVER PLACE, MODIFY, OR CANCEL AN ORDER. ***
    Only position/instrument READ functions are allowed. Placement stays manual
    on Robinhood by design (see the copilot direction). robin_stocks order
    functions are deliberately never imported or referenced here.

Flow: fetch_open_legs() (I/O) -> group_into_positions() -> reconcile() against the
live book. Reconcile MATCHES a position the user already logged (same strikes +
expiry) so sync updates it instead of duplicating it; unmatched positions are
created as source="rh-sync". The pure functions are unit-tested; the robin_stocks
fetch + login are validated by the user via the dry-run CLI.

CREDENTIALS: never committed, never logged. Interactive login stores only the
robin_stocks token (~/.tokens/robinhood.pickle); re-run `login` when it expires.
"""
from __future__ import annotations

import os

from loguru import logger


# ── PURE: mapping + grouping + reconcile ────────────────────────────────────

def normalize_leg(position: dict, instrument: dict) -> dict:
    """Combine an RH leg position + its instrument into our leg shape.
    side 'short' -> SELL, 'long' -> BUY."""
    side = (position.get("type") or "").lower()
    cp = (instrument.get("type") or "").lower()
    return {
        "action": "SELL" if side.startswith("s") else "BUY",
        "option_type": "CALL" if cp.startswith("c") else "PUT",
        "strike": float(instrument.get("strike_price") or 0),
        "expiry": str(instrument.get("expiration_date") or "")[:10],
        "quantity": float(position.get("quantity") or 0),
        "avg_price": float(position.get("average_price") or 0),
        "chain_symbol": position.get("chain_symbol") or instrument.get("chain_symbol") or "SPY",
        "option_id": position.get("option_id"),
    }


def _infer_strategy(legs: list[dict]) -> str:
    calls = [l for l in legs if l["option_type"] == "CALL"]
    puts = [l for l in legs if l["option_type"] == "PUT"]
    if len(calls) == 2 and len(puts) == 2:
        return "iron_condor"
    if len(legs) == 2 and len({l["option_type"] for l in legs}) == 1:
        return "debit_spread"   # vertical; debit vs credit refined when logged
    if len(legs) == 1:
        return "single_leg"
    return "custom"


def group_into_positions(legs: list[dict]) -> list[dict]:
    """Group open legs into positions by (chain_symbol, expiry). Each becomes a
    trade-shaped dict for reconcile/display."""
    groups: dict[tuple, list[dict]] = {}
    for leg in legs:
        key = (leg.get("chain_symbol", "SPY"), leg.get("expiry", ""))
        groups.setdefault(key, []).append(leg)

    positions = []
    for (symbol, expiry), grp in groups.items():
        qtys = [int(l["quantity"]) for l in grp if l.get("quantity")]
        size = max(set(qtys), key=qtys.count) if qtys else 1
        clean = [{"action": l["action"], "option_type": l["option_type"],
                  "strike": l["strike"], "expiry": l["expiry"]} for l in grp]
        # net entry from leg avg fills. RH's average_price is SIGNED and
        # PER-CONTRACT (shorts negative = credit received, longs positive =
        # debit paid), so the net credit/debit per share is |Σ| / 100.
        # Validated against the user's real July condor: Σ = -155 -> $1.55.
        net = abs(sum(l["avg_price"] for l in grp)) / 100.0
        positions.append({
            "ticker": symbol,
            "expiry": expiry,
            "strategy": _infer_strategy(grp),
            "size": size,
            "legs": clean,
            "entry_price": round(net, 2),
            "source": "rh-sync",
        })
    return positions


def _strike_key(legs: list[dict]) -> frozenset:
    return frozenset((l.get("option_type", "").upper()[:1], round(float(l.get("strike") or 0), 1))
                     for l in legs)


def _trade_expiry(trade: dict) -> str:
    for leg in (trade.get("legs") or []):
        e = leg.get("expiry") or leg.get("expiration")
        if e:
            return str(e)[:10]
    return trade.get("legs_expiry") or ""


def reconcile(positions: list[dict], existing_live: list[dict]) -> list[dict]:
    """Match each RH position to an open live trade by (ticker, expiry, strike
    set). Matched -> 'match' (update in place, keep the user's confirmed fill);
    unmatched RH position -> 'create'; open live trade with NO matching RH
    position -> 'close' (the user closed it on Robinhood — audit T1.3: without
    this the watchdog guards a phantom forever)."""
    open_live = [t for t in existing_live
                 if t.get("book") == "live" and t.get("outcome", "open") == "open"]
    plan = []
    matched_ids = set()
    for pos in positions:
        key = _strike_key(pos["legs"])
        match = next((t for t in open_live
                      if t.get("ticker") == pos["ticker"]
                      and _trade_expiry(t) == pos["expiry"]
                      and _strike_key(t.get("legs") or []) == key), None)
        if match:
            matched_ids.add(match.get("trade_id"))
            plan.append({"action": "match", "trade_id": match.get("trade_id"),
                         "position": pos})
        else:
            plan.append({"action": "create", "position": pos})
    for t in open_live:
        if t.get("trade_id") not in matched_ids:
            plan.append({"action": "close", "trade_id": t.get("trade_id"),
                         "trade": t})
    return plan


# ── I/O: robin_stocks read-only fetch + login (validated via dry-run) ───────

def _load_session():
    """Re-load the stored RH token into THIS process. robin_stocks' logged-in
    state is per-process — login() silently reuses the saved pickle (no MFA) when
    it's still valid. Raises a clear error if there's no valid session yet."""
    import os
    import robin_stocks.robinhood as r
    pickle_path = os.path.expanduser("~/.tokens/robinhood.pickle")
    if not os.path.isfile(pickle_path):
        raise RuntimeError("No stored RH session — run `python -m learning.rh_sync login` first")
    try:
        r.login(store_session=True)            # reloads + validates the pickle
    except Exception as e:
        raise RuntimeError(f"Stored RH session invalid/expired — re-run `login`: {e}") from e


def fetch_open_legs() -> list[dict]:
    """Fetch + normalize all open option legs from RH (READ-ONLY). Requires a
    valid stored session (run `login` first)."""
    import robin_stocks.robinhood as r
    _load_session()
    legs = []
    for p in (r.options.get_open_option_positions() or []):
        if float(p.get("quantity") or 0) == 0:
            continue
        oid = p.get("option_id") or p.get("option", "").rstrip("/").split("/")[-1]
        inst = r.options.get_option_instrument_data_by_id(oid) or {}
        legs.append(normalize_leg({**p, "option_id": oid}, inst))
    return legs


def login_interactive() -> bool:
    """One-time interactive login (prompts for credentials + MFA in YOUR
    terminal). Stores only the robin_stocks token. Never logs credentials."""
    import getpass
    import os
    import robin_stocks.robinhood as r
    user = os.getenv("RH_USERNAME") or input("RH email/username: ").strip()
    pw = os.getenv("RH_PASSWORD") or getpass.getpass("RH password: ")
    mfa = input("RH MFA code (blank if none): ").strip() or None
    r.login(username=user, password=pw, mfa_code=mfa, store_session=True)
    logger.info("rh_sync: login stored a session token (read-only use)")
    return True


def _stamp_path() -> str:
    import config
    return os.path.join(config.LOG_DIR, "rh_sync_last_success")


def stamp_success() -> None:
    """Record a successful sync so loop_health can verify the TOKEN WORKS, not
    just that the pickle file exists (audit T1.3: file-exists false-passed a
    week of expired-token failures)."""
    from datetime import date
    from atomic_io import atomic_write_text
    atomic_write_text(_stamp_path(), date.today().isoformat() + "\n")


def last_success_date():
    from datetime import date
    try:
        return date.fromisoformat(open(_stamp_path()).read().strip()[:10])
    except Exception:
        return None


def _close_estimate(trade: dict):
    """Best-effort cost-to-close (per share) for a position the user closed on
    RH — we don't know their real fill, so mark at the current NBBO mid."""
    try:
        from data.market_quotes import fetch_leg_quotes, position_mtm
        legs = fetch_leg_quotes(trade.get("ticker", "SPY"), trade.get("legs") or [])
        if any(l.get("mid") is None for l in legs):
            return None
        # cost to close a credit structure = value of the shorts minus longs now
        cost = sum((-l["mid"] if (l.get("action") or "").upper().startswith("B") else l["mid"])
                   for l in legs)
        return round(max(0.0, cost), 2)
    except Exception:
        return None


def sync(dry_run: bool = True):
    """Fetch RH positions -> reconcile against the live book. dry_run prints the
    plan and writes nothing; otherwise applies creates AND closes (a live trade
    missing from RH = the user closed it there)."""
    from journal.trade_recorder import TradeRecorder
    rec = TradeRecorder()
    positions = group_into_positions(fetch_open_legs())
    plan = reconcile(positions, rec.get_open_trades())
    for step in plan:
        if step["action"] == "close":
            t = step["trade"]
            logger.info(f"[sync] CLOSED-ON-RH {step['trade_id']} "
                        f"{t.get('strategy')} — no longer open on Robinhood")
            if not dry_run:
                est = _close_estimate(t)
                exit_px = est if est is not None else float(t.get("entry_price") or 0)
                note = ("[RH-SYNC] detected closed on Robinhood; exit marked at "
                        + ("current mid" if est is not None else
                           "entry (scratch — no quotes; correct on /copilot if needed)"))
                rec.log_exit(step["trade_id"], exit_price=exit_px, notes=note,
                             exit_reason="closed_on_rh")
            continue
        pos = step["position"]
        strikes = sorted(l["strike"] for l in pos["legs"])
        if step["action"] == "match":
            logger.info(f"[sync] MATCH {step['trade_id']} {pos['ticker']} "
                        f"{pos['strategy']} {pos['expiry']} {strikes} (already logged)")
        else:
            logger.info(f"[sync] NEW {pos['ticker']} {pos['strategy']} "
                        f"{pos['expiry']} {strikes} x{pos['size']}")
            if not dry_run:
                from alerts.copilot_log import build_live_trade_kwargs
                form = {"ticker": pos["ticker"], "expiry": pos["expiry"],
                        "contracts": str(pos["size"]),
                        "entry_price": str(pos.get("entry_price", "") or "")}
                for leg in pos["legs"]:
                    slot = ("bc" if leg["action"] == "BUY" else "sc") if leg["option_type"] == "CALL" \
                        else ("bp" if leg["action"] == "BUY" else "sp")
                    form[slot] = str(leg["strike"])
                kwargs = build_live_trade_kwargs(form)
                kwargs["source"] = "rh-sync"
                kwargs["notes"] = "[LIVE] synced read-only from Robinhood"
                rec.log_entry(**kwargs)
    if not dry_run:
        stamp_success()
    return plan


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sync"
    if cmd == "login":
        login_interactive()
    elif cmd == "sync":
        dry = "--apply" not in sys.argv
        sync(dry_run=dry)
        print("DRY-RUN (no writes) — re-run with --apply to log new positions"
              if dry else "Applied.")
    else:
        print("usage: python -m learning.rh_sync [login|sync [--apply]]")

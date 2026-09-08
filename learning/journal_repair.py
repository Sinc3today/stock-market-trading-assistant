"""learning/journal_repair.py -- repair the records the old P&L engine broke.

One-off remediation for docs/FORWARD_TEST_AUDIT.md A1/B1, kept in the tree
because it is idempotent, tested, and documents exactly what was changed.

Two populations, deliberately treated differently:

  RESCORE        Real entry and exit prices, but the P&L was written as $0
                 because the strategy name ("put_debit_spread") matched no
                 branch in _calculate_pnl. Pure arithmetic; recoverable.

  MARK_UNSCORED  The 0DTE records whose recorded exit price is FICTION — the
                 marking model discarded intraday time, priced the spread at
                 intrinsic, and "stopped" it out minutes after entry. The
                 number in exit_price never existed in the market, so the P&L
                 is not recoverable. The honest repair is to say so.

Rescoring the second group would launder a modelling bug into a plausible
looking number, which is the opposite of the point.

Run:
    .venv/bin/python -m learning.journal_repair            # dry run
    .venv/bin/python -m learning.journal_repair --apply    # writes, with backup
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger

from journal.trade_recorder import _pnl_convention
from learning import forward_scorecard as fs

RESCORE = "rescore"
MARK_UNSCORED = "mark_unscored"
NORMALISE = "normalise"          # entry_value units + commission backfill
RESCORE_FROM_RISK = "rescore_from_risk"   # derive a fabricated entry price
REPAIR_TAG = "[REPAIRED 2026-09-06]"
ENTRY_REPAIR_TAG = "[ENTRY REPAIRED 2026-09-07]"


def _trades_path() -> str:
    return os.path.join(config.LOG_DIR, "trades.json")


def _recompute(trade: dict) -> float | None:
    """True P&L from the recorded prices, using the shared convention."""
    entry, exit_price = trade.get("entry_price"), trade.get("exit_price")
    if entry is None or exit_price is None:
        return None
    convention = _pnl_convention(trade.get("strategy"))
    if convention is None:
        return None
    try:
        entry, exit_price = float(entry), float(exit_price)
        size = float(trade.get("size") or 1)
    except (TypeError, ValueError):
        return None
    pps = (entry - exit_price) if convention == "credit" else (exit_price - entry)
    return round(pps * 100 * size, 2)


def _outcome_for(pnl: float) -> str:
    if pnl > 0.01:
        return "win"
    if pnl < -0.01:
        return "loss"
    return "breakeven"


def _is_phantom_fill(trade: dict) -> bool:
    """Exit price is fiction: a 'stop' recorded at exactly $0.00.

    A stop books a partial loss, so a zero fill means the mark was broken, not
    that the trade closed there.
    """
    exit_price = trade.get("exit_price")
    notes = (trade.get("notes_exit") or "").lower()
    return exit_price is not None and float(exit_price) == 0.0 and "stop" in notes


def plan_normalisations(trades: list[dict]) -> list[dict]:
    """Field-level fixes that do not change any P&L verdict (audit A3/A4).

    * entry_value stored per-share instead of dollars (missing the x100).
    * commission / pnl_net absent on records written before fees were modelled.

    Kept separate from plan_repairs because these are bookkeeping corrections,
    not re-judgements of a trade's outcome.
    """
    from journal.trade_recorder import TradeRecorder, round_trip_commission
    rec = TradeRecorder.__new__(TradeRecorder)
    plan: list[dict] = []
    for t in trades:
        before, after = {}, {}
        ep, size = t.get("entry_price"), t.get("size") or 1
        strategy = t.get("strategy")
        if ep is not None and strategy:
            try:
                want = rec._calculate_entry_value(strategy, float(ep), float(size),
                                                  t.get("max_loss"))
            except (TypeError, ValueError):
                want = None
            have = t.get("entry_value")
            if want is not None and (have is None or abs(float(have) - want) > 0.01):
                before["entry_value"], after["entry_value"] = have, want

        # Backfill fees on closed, scored trades only — an unscored record has
        # no gross to net off.
        if fs.is_closed(t) and t.get("pnl_dollars") is not None:
            if t.get("commission") is None or t.get("pnl_net") is None:
                fee = round_trip_commission(strategy, t.get("legs"), size)
                before["commission"] = t.get("commission")
                after["commission"] = fee
                after["pnl_net"] = round(float(t["pnl_dollars"]) - fee, 2)

        if after:
            plan.append({"trade_id": t.get("trade_id"), "action": NORMALISE,
                         "reason": "entry_value units / commission backfill",
                         "before": before, "after": after})
    return plan


# Structures where max_profit (credit) / max_loss (debit) IS the premium, so an
# entry price can be derived from them. Excludes broken_wing, whose max_profit
# is the peak at the body (upper_wing + credit), and anything whose risk fields
# encode something other than the premium.
_RISK_EQUALS_PREMIUM = {
    "iron_condor", "credit_spread", "debit_spread",
    "put_debit_spread", "call_debit_spread",
    "put_credit_spread", "call_credit_spread",
}


def _derived_entry(trade: dict) -> float | None:
    """Entry price implied by the (real) risk fields, or None if they are
    themselves corrupt.

    A credit structure stores its credit as max_profit; a debit structure
    stores its debit as max_loss. Both must be positive — `max_profit: -1437`
    is structurally impossible and means the record is unrecoverable, not that
    we should pick the other field.
    """
    strategy = (trade.get("strategy") or "").lower()
    if strategy not in _RISK_EQUALS_PREMIUM:
        # A broken-wing butterfly's max_profit is (upper_wing + credit) x 100 —
        # its structural peak at the body, not the premium taken in. Deriving
        # an entry from it inflates every BWB by exactly the upper wing ($3.00).
        # Caught in the dry-run diff before it corrupted 18 correct records:
        # `max_profit` means different things for different structures, which
        # is the same field-name-shared-meaning-not trap as everything else.
        return None
    conv = _pnl_convention(strategy)
    mp, ml = trade.get("max_profit"), trade.get("max_loss")
    if conv is None or mp is None or ml is None:
        return None
    try:
        mp, ml, size = float(mp), float(ml), float(trade.get("size") or 1)
    except (TypeError, ValueError):
        return None
    if mp <= 0 or ml <= 0 or size <= 0:
        return None
    basis = mp if conv == "credit" else ml
    entry = basis / 100.0 / size
    return round(entry, 2) if entry > 0 else None


def plan_entry_repairs(trades: list[dict]) -> list[dict]:
    """Records whose entry_price is the fabricated $1.00 placeholder.

    paper_broker returned a hardcoded 1.00 when it could not read the premium
    (fixed 2026-09-07). max_profit / max_loss came from the structure builder
    and are real, so the true entry is recoverable — except where the risk
    fields are corrupt too, which is marked rather than guessed at.
    """
    plan: list[dict] = []
    for t in trades:
        if t.get("outcome") == "void" or ENTRY_REPAIR_TAG in (t.get("notes_exit") or ""):
            continue
        strategy = (t.get("strategy") or "").lower()
        conv = _pnl_convention(strategy)
        ep = t.get("entry_price")
        if conv is None or ep is None:
            continue
        # Only structures whose risk fields ARE the premium can be judged here.
        # Anything else (broken_wing) is left completely alone — we cannot tell
        # a good entry from a bad one, so we must not touch it either way.
        if strategy not in _RISK_EQUALS_PREMIUM:
            continue
        mp, ml = t.get("max_profit"), t.get("max_loss")
        if mp is None or ml is None:
            continue                              # nothing to judge against

        want = _derived_entry(t)
        if want is not None and abs(float(ep) - want) <= 0.02:
            continue                              # already consistent

        tid = t.get("trade_id")
        if want is None:
            # Risk fields are present but impossible. Only a record that also
            # carries the known placeholder is safe to call corrupt.
            if t.get("pnl_dollars") is None or round(float(ep), 2) != 1.00:
                continue
            plan.append({
                "trade_id": tid, "action": MARK_UNSCORED,
                "reason": ("entry price was a placeholder AND the risk fields "
                           f"are impossible (max_profit={t.get('max_profit')}, "
                           f"max_loss={t.get('max_loss')})"),
                "before": {"entry_price": ep, "pnl_dollars": t.get("pnl_dollars")},
                "after": {"pnl_dollars": None, "outcome": "unscored"},
            })
            continue

        after = {"entry_price": want}
        # Only recompute P&L once the trade has actually closed.
        xp = t.get("exit_price")
        if is_closed(t := t) and xp is not None and t.get("pnl_dollars") is not None:
            pps = (want - float(xp)) if conv == "credit" else (float(xp) - want)
            pnl = round(pps * 100 * float(t.get("size") or 1), 2)
            after["pnl_dollars"] = pnl
            after["outcome"] = _outcome_for(pnl)
        plan.append({
            "trade_id": tid, "action": RESCORE_FROM_RISK,
            "reason": (f"entry price {ep} was a placeholder; derived {want} "
                       f"from {'max_profit' if conv == 'credit' else 'max_loss'}"),
            "before": {"entry_price": ep, "pnl_dollars": t.get("pnl_dollars")},
            "after": after,
        })
    return plan


def is_closed(trade: dict) -> bool:
    return fs.is_closed(trade)


def plan_repairs(trades: list[dict]) -> list[dict]:
    """What we would change, and why. Read-only."""
    plan: list[dict] = []
    for t in trades:
        if not fs.is_closed(t) or t.get("outcome") == "void":
            continue
        if fs.integrity(t) not in (fs.UNSCORED, fs.SUSPECT_FILL):
            continue                       # already trustworthy — leave it
        if t.get("outcome") == "unscored" and t.get("pnl_dollars") is None:
            continue                       # already repaired — idempotent

        if _is_phantom_fill(t):
            plan.append({
                "trade_id": t.get("trade_id"), "action": MARK_UNSCORED,
                "reason": ("0DTE mark discarded intraday time value; the "
                           "recorded exit price never existed"),
                "before": {"pnl_dollars": t.get("pnl_dollars"),
                           "outcome": t.get("outcome")},
                "after": {"pnl_dollars": None, "outcome": "unscored"},
            })
            continue

        pnl = _recompute(t)
        if pnl is None:
            plan.append({
                "trade_id": t.get("trade_id"), "action": MARK_UNSCORED,
                "reason": "no recoverable price or convention",
                "before": {"pnl_dollars": t.get("pnl_dollars"),
                           "outcome": t.get("outcome")},
                "after": {"pnl_dollars": None, "outcome": "unscored"},
            })
            continue

        plan.append({
            "trade_id": t.get("trade_id"), "action": RESCORE,
            "reason": (f"strategy '{t.get('strategy')}' matched no P&L branch; "
                       "recomputed from the recorded prices"),
            "before": {"pnl_dollars": t.get("pnl_dollars"),
                       "outcome": t.get("outcome")},
            "after": {"pnl_dollars": pnl, "outcome": _outcome_for(pnl)},
        })
    return plan


def apply_repairs(trades: list[dict], plan: list[dict]) -> list[dict]:
    """Return a NEW trade list with the plan applied. Does not mutate input."""
    by_id = {p["trade_id"]: p for p in plan}
    out: list[dict] = []
    for t in trades:
        p = by_id.get(t.get("trade_id"))
        if not p:
            out.append(dict(t))
            continue
        n = dict(t)
        if p["action"] == RESCORE_FROM_RISK:
            n.update(p["after"])
            note = (t.get("notes_exit") or "").strip()
            if ENTRY_REPAIR_TAG not in note:
                n["notes_exit"] = f"{note}\n{ENTRY_REPAIR_TAG} {p['reason']}".strip()
            out.append(n)
            continue
        if p["action"] == NORMALISE:
            n.update(p["after"])        # field-level fix; no verdict changes
            out.append(n)
            continue
        n["pnl_dollars"] = p["after"]["pnl_dollars"]
        n["outcome"] = p["after"]["outcome"]
        if p["action"] == RESCORE:
            cost = abs(float(t.get("entry_price") or 0)) * 100 * float(t.get("size") or 1)
            n["pnl_pct"] = round(p["after"]["pnl_dollars"] / cost * 100, 2) if cost else None
            n["pnl_per_contract"] = p["after"]["pnl_dollars"] / float(t.get("size") or 1)
        else:
            n["pnl_pct"] = None
            n["pnl_per_contract"] = None
        tag = (ENTRY_REPAIR_TAG if "placeholder" in p.get("reason", "")
               else REPAIR_TAG)
        note = (t.get("notes_exit") or "").strip()
        if tag not in note:
            n["notes_exit"] = f"{note}\n{tag} {p['reason']}".strip()
        out.append(n)
    return out


def run(apply: bool = False) -> list[dict]:
    """Plan (and optionally apply) the repair. Backs up before writing."""
    path = _trades_path()
    try:
        with open(path) as fh:
            trades = json.load(fh) or []
    except Exception as e:
        logger.error(f"journal_repair: cannot read {path}: {e}")
        return []

    # Verdict repairs first, then bookkeeping normalisation over the result —
    # a rescored trade needs its commission backfilled from the NEW P&L.
    plan = plan_repairs(trades)
    entry = plan_entry_repairs(apply_repairs(trades, plan) if plan else trades)
    if not apply:
        staged = apply_repairs(trades, plan) if plan else trades
        staged = apply_repairs(staged, entry) if entry else staged
        return plan + entry + plan_normalisations(staged)

    repaired = apply_repairs(trades, plan) if plan else [dict(t) for t in trades]
    repaired = apply_repairs(repaired, entry) if entry else repaired
    norm = plan_normalisations(repaired)
    if not plan and not entry and not norm:
        return []
    repaired = apply_repairs(repaired, norm)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{path}.bak-{stamp}"
    shutil.copy2(path, backup)
    logger.info(f"journal_repair: backed up {path} -> {backup}")

    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        json.dump(repaired, fh, indent=2)
    os.replace(tmp, path)
    logger.info(f"journal_repair: applied {len(plan)} repairs, "
                f"{len(entry)} entry repairs, {len(norm)} normalisations")
    return plan + entry + norm


def main():
    apply = "--apply" in sys.argv
    plan = run(apply=apply)
    if not plan:
        print("Nothing to repair — the journal is clean.")
        return
    rescore = [p for p in plan if p["action"] == RESCORE]
    unscored = [p for p in plan if p["action"] == MARK_UNSCORED]
    norm = [p for p in plan if p["action"] == NORMALISE]
    entry_fix = [p for p in plan if p["action"] == RESCORE_FROM_RISK]

    print("=" * 76)
    print(f"JOURNAL REPAIR — {'APPLIED' if apply else 'DRY RUN'} — {len(plan)} records")
    print("=" * 76)

    print(f"\nRESCORE ({len(rescore)}) — real prices, fabricated $0 P&L:")
    net = 0.0
    for p in rescore:
        after = p["after"]["pnl_dollars"]
        net += after
        print(f"  {p['trade_id']}  $0 -> ${after:+9.2f}  ({p['after']['outcome']})")
    print(f"  {'':10} net recovered: ${net:+,.2f}")

    print(f"\nMARK UNSCORED ({len(unscored)}) — exit price is fiction:")
    for p in unscored:
        print(f"  {p['trade_id']}  {p['reason']}")

    if entry_fix:
        print(f"\nENTRY REPAIR ({len(entry_fix)}) — placeholder $1.00 -> derived from risk fields:")
        net = 0.0
        for p in entry_fix:
            b, a = p["before"], p["after"]
            dp = ""
            if "pnl_dollars" in a and b.get("pnl_dollars") is not None:
                delta = a["pnl_dollars"] - b["pnl_dollars"]
                net += delta
                dp = f"   P&L ${b['pnl_dollars']:+8.2f} -> ${a['pnl_dollars']:+8.2f}  ({delta:+.2f})"
            print(f"  {p['trade_id']}  entry ${b['entry_price']:.2f} -> ${a['entry_price']:.2f}{dp}")
        print(f"  {'':10} net P&L correction: ${net:+,.2f}")

    if norm:
        ev_fixes = [p for p in norm if "entry_value" in p["after"]]
        fee_fixes = [p for p in norm if "commission" in p["after"]]
        fees = sum(p["after"].get("commission") or 0 for p in fee_fixes)
        print(f"\nNORMALISE ({len(norm)}) — bookkeeping, no verdict changes:")
        print(f"  entry_value rescaled to dollars: {len(ev_fixes)}")
        print(f"  commission backfilled:           {len(fee_fixes)}"
              f"  (total fees ${fees:,.2f})")

    if not apply:
        print("\nDry run. Re-run with --apply to write (a backup is made first).")
    print("=" * 76)


if __name__ == "__main__":
    main()

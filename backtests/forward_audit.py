"""backtests/forward_audit.py -- try to DISPROVE the live forward-test record.

Companion to docs/FORWARD_TEST_AUDIT.md. Every function here is a validator
that attempts to falsify one assumption the forward-test numbers rest on.

Design rules:
  * A validator reports a VERDICT with EVIDENCE. It never reassures.
  * A validator that cannot fail is not a validator — each one has a concrete
    condition that flips it to FAIL.
  * Read-only. Nothing here mutates the journal; V-A1 prints a repair PLAN,
    it does not apply it.

Run:  .venv/bin/python -m backtests.forward_audit
"""
from __future__ import annotations

import json
import math
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from learning import forward_scorecard as fs

PASS, FAIL, WARN, INFO = "PASS", "FAIL", "WARN", "INFO"

# A 4-leg condor round trip at a typical retail options rate.
COMMISSION_PER_LEG = 0.65
MIN_CLOSED_FOR_A_CLAIM = 10


def _result(vid, name, severity, verdict, headline, evidence=None):
    return {"id": vid, "name": name, "severity": severity, "verdict": verdict,
            "headline": headline, "evidence": evidence or []}


def _load_trades() -> list[dict]:
    path = os.path.join(config.LOG_DIR, "trades.json")
    try:
        with open(path) as fh:
            return json.load(fh) or []
    except Exception:
        return []


def _legs_of(t: dict) -> list[dict]:
    return t.get("legs") or []


def _n_legs(t: dict) -> int:
    return max(1, len(_legs_of(t)))


# One implementation, shared with the dashboard.
from learning.forward_scorecard import wilson  # noqa: E402,F401


# ── A1: records the P&L engine never scored ──────────────────────

def _is_quarantined(t: dict) -> bool:
    """Deliberately marked unrecoverable by learning.journal_repair.

    These are acknowledged and permanently recorded, not outstanding defects —
    a validator that keeps failing on them cries wolf and stops being read.
    """
    from learning.journal_repair import REPAIR_TAG
    return REPAIR_TAG in (t.get("notes_exit") or "")


def v_a1_unscored(trades):
    all_unscored = [t for t in trades
                    if fs.is_closed(t) and fs.integrity(t) == fs.UNSCORED]
    quarantined = [t for t in all_unscored if _is_quarantined(t)]
    unscored = [t for t in all_unscored if not _is_quarantined(t)]
    if not unscored:
        note = (f"All closed records carry engine-computed P&L "
                f"({len(quarantined)} quarantined as unrecoverable)."
                if quarantined else "All closed records carry engine-computed P&L.")
        return _result("A1", "Every closed trade was actually scored", "P1", PASS,
                       note)
    rec = [(t, fs.recompute_pnl(t)) for t in unscored]
    recoverable = [(t, p) for t, p in rec if p is not None]
    hidden = sum(p for _, p in recoverable)
    by_book = defaultdict(lambda: [0, 0.0])
    for t, p in recoverable:
        b = by_book[fs.book_of(t)]
        b[0] += 1
        b[1] += p
    ev = [f"{len(unscored)} closed records fell through _calculate_pnl -> $0",
          f"strategies: {dict(Counter(t.get('strategy') for t in unscored))}",
          f"recoverable from entry/exit: {len(recoverable)}",
          f"hidden P&L: ${hidden:+,.2f}"]
    ev += [f"  {b}: n={c} hidden ${s:+,.2f}" for b, (c, s) in sorted(by_book.items())]
    return _result("A1", "Every closed trade was actually scored", "P1", FAIL,
                   f"{len(unscored)} records were never scored, hiding ${hidden:+,.0f}.", ev)


# ── A2: the two P&L implementations must agree ───────────────────

def v_a2_sign_conventions(trades):
    """journal.trade_recorder._calculate_pnl vs learning.exit_manager._pnl_dollars.

    Two independent implementations of the same thing. Where they disagree,
    at least one book is wrong — and a sign disagreement doubles the error.
    """
    from journal.trade_recorder import TradeRecorder
    from learning.exit_manager import ExitManager
    rec = TradeRecorder.__new__(TradeRecorder)
    strategies = sorted({str(t.get("strategy")) for t in trades if t.get("strategy")})
    ev, disagree = [], []
    for s in strategies:
        entry, exit_p, size = 2.00, 1.00, 1
        a = rec._calculate_pnl(s, "NEUTRAL", entry, exit_p, size)[1]
        b = ExitManager._pnl_dollars(s, entry, exit_p, size)
        if b is None:
            continue
        if abs(a - b) > 0.01:
            disagree.append(s)
            sign = "SIGN FLIP" if a == -b and a != 0 else "mismatch"
            ev.append(f"{s:20} recorder=${a:+8.2f}  exit_manager=${b:+8.2f}   <-- {sign}")
        else:
            ev.append(f"{s:20} agree (${a:+.2f})")
    live = {s: sum(1 for t in trades if t.get("strategy") == s and not fs.is_closed(t))
            for s in disagree}
    if disagree:
        ev.append("")
        ev.append(f"open positions carrying a disputed convention: {live}")
        return _result("A2", "P&L sign conventions agree across implementations",
                       "P1", FAIL,
                       f"{len(disagree)} strategies disagree between the two P&L "
                       f"engines: {', '.join(disagree)}.", ev)
    return _result("A2", "P&L sign conventions agree across implementations",
                   "P1", PASS, "Both engines agree on every live strategy.", ev)


# ── A3: contract-multiplier consistency ──────────────────────────

def v_a3_entry_value_scaling(trades):
    bad = []
    for t in trades:
        ep, ev_, size = t.get("entry_price"), t.get("entry_value"), t.get("size") or 1
        if ep is None or ev_ is None:
            continue
        expected = abs(float(ep)) * 100 * float(size)
        if expected and abs(abs(float(ev_)) - expected) > max(1.0, expected * 0.02):
            bad.append((t.get("trade_id"), t.get("strategy"), ep, ev_, expected))
    if not bad:
        return _result("A3", "Contract multiplier applied consistently", "P2", PASS,
                       "entry_value == entry_price x 100 x size everywhere.")
    ev = [f"{tid} {s:18} entry_price={ep} entry_value={v} (expected ~{exp:.0f})"
          for tid, s, ep, v, exp in bad[:12]]
    if len(bad) > 12:
        ev.append(f"... and {len(bad)-12} more")
    strat = dict(Counter(s for _, s, _, _, _ in bad))
    ev.append(f"affected strategies: {strat}")
    return _result("A3", "Contract multiplier applied consistently", "P2", FAIL,
                   f"{len(bad)} records store entry_value in the wrong unit.", ev)


# ── A4: does the edge survive commissions? ───────────────────────

def v_a4_commissions(trades):
    rows, ev, broken = [], [], []
    scored = [t for t in trades if fs.is_closed(t) and fs.integrity(t) == fs.SCORED
              and t.get("pnl_dollars") is not None]
    by_book = defaultdict(list)
    for t in scored:
        fee = COMMISSION_PER_LEG * _n_legs(t) * 2 * float(t.get("size") or 1)
        by_book[fs.book_of(t)].append((float(t["pnl_dollars"]), fee))
    for book, vals in sorted(by_book.items()):
        n = len(vals)
        gross = sum(p for p, _ in vals) / n
        net = sum(p - f for p, f in vals) / n
        wins_net = sum(1 for p, f in vals if p - f > 0)
        ev.append(f"{book:12} n={n:3} gross avg ${gross:+7.2f} -> net ${net:+7.2f} "
                  f"({(gross-net)/abs(gross)*100 if gross else 0:4.0f}% of edge)  "
                  f"win {wins_net/n*100:.0f}%")
        if gross > 0 and net <= 0:
            broken.append(book)
    # promotion bars under fees
    ev.append("")
    for r in fs.promotion_progress(trades):
        if not r["closed"]:
            continue
        bucket_trades = [t for t in scored if t.get("dte_bucket") == r["bucket"]]
        if not bucket_trades:
            continue
        fees = [COMMISSION_PER_LEG * _n_legs(t) * 2 * float(t.get("size") or 1)
                for t in bucket_trades]
        net_avg = (sum(float(t["pnl_dollars"]) for t in bucket_trades) - sum(fees)) / len(bucket_trades)
        status = "still clears $20 bar" if net_avg > 20 else "FAILS the $20 avg bar"
        ev.append(f"{r['label']:20} avg ${r['avg']:+7.2f} -> net ${net_avg:+7.2f}   {status}")
        if r["avg"] > 20 >= net_avg:
            broken.append(r["label"])
    if broken:
        return _result("A4", "Edge survives commissions", "P2", FAIL,
                       f"Commissions flip or break: {', '.join(broken)}.", ev)
    return _result("A4", "Edge survives commissions", "P2", WARN,
                   f"No book flips sign, but fees are unmodeled "
                   f"(${COMMISSION_PER_LEG}/leg assumed here).", ev)


# ── B1: exits that could not have happened ───────────────────────

def v_b1_impossible_fills(trades):
    bad = []
    for t in trades:
        if not fs.is_closed(t) or t.get("outcome") == "void":
            continue
        xp, notes = t.get("exit_price"), (t.get("notes_exit") or "").lower()
        if xp is None or _is_quarantined(t):
            continue           # already acknowledged and quarantined
        if float(xp) == 0.0 and "stop" in notes:
            bad.append((t.get("trade_id"), t.get("strategy"), t.get("book"),
                        (t.get("notes_exit") or "")[:64]))
    if not bad:
        quarantined = sum(1 for t in trades if _is_quarantined(t))
        note = ("No stop-exit recorded an impossible $0.00 fill"
                + (f" ({quarantined} historical ones quarantined)." if quarantined
                   else "."))
        return _result("B1", "Exit prices are physically possible", "P1", PASS, note)
    ev = [f"{tid} {s:18} {b:12} {n}" for tid, s, b, n in bad[:12]]
    if len(bad) > 12:
        ev.append(f"... and {len(bad)-12} more")
    ev.append("A stop at 75% of max loss cannot fill at $0.00 — the price "
              "lookup failed and returned 0 (Polygon snapshots carry no quotes).")
    return _result("B1", "Exit prices are physically possible", "P1", FAIL,
                   f"{len(bad)} stop-exits recorded a $0.00 fill.", ev)


# ── B2 / C1: mark the open tail ──────────────────────────────────

def v_b2_open_marks(trades):
    """Re-price every open position. A closed-only headline is only honest if
    the open tail isn't hiding losses."""
    from learning.exit_manager import ExitManager
    from datetime import date
    try:
        from alerts.stop_watchdog import yf_spot
        spy = yf_spot("SPY")
        vix = yf_spot("^VIX")
    except Exception:
        spy = vix = None
    opens = [t for t in trades if not fs.is_closed(t)]
    if not opens:
        return _result("B2", "Open tail is marked", "P1", PASS, "Nothing open.")
    if not spy or not vix:
        return _result("B2", "Open tail is marked", "P1", WARN,
                       f"{len(opens)} open positions could not be marked "
                       "(no live SPY/VIX).",
                       ["Unrealized P&L is unknown; every closed-only headline "
                        "excludes this tail."])
    em = ExitManager.__new__(ExitManager)
    by_book, unmarked, ev = defaultdict(lambda: [0, 0.0]), 0, []
    for t in opens:
        legs = _legs_of(t)
        exp = ExitManager._nearest_expiration(legs)
        if not exp or not legs:
            unmarked += 1
            continue
        dte = (exp - date.today()).days
        mark = em._mark_exit_price(t.get("strategy"), legs, spy, vix, date.today(), dte)
        pnl = ExitManager._pnl_dollars(t.get("strategy"), t.get("entry_price"),
                                       mark, t.get("size"))
        if pnl is None:
            unmarked += 1
            continue
        b = by_book[fs.book_of(t)]
        b[0] += 1
        b[1] += pnl
    total = sum(v[1] for v in by_book.values())
    for b, (n, s) in sorted(by_book.items()):
        ev.append(f"{b:12} {n:2} open   unrealized ${s:+9.2f}")
    ev.append(f"{'TOTAL':12} {sum(v[0] for v in by_book.values()):2} open   "
              f"unrealized ${total:+9.2f}")
    if unmarked:
        ev.append(f"({unmarked} could not be marked — missing legs/expiry)")
    ev.append(f"marked at SPY={spy:.2f} VIX={vix:.2f} with the same BS model "
              "the exit manager uses (no live option quotes exist)")
    verdict = FAIL if total < 0 else WARN
    msg = (f"Open tail carries ${total:+,.0f} unrealized — "
           + ("closed-only headlines are flattering." if total < 0
              else "headlines understate, but the tail is unrealized."))
    return _result("B2", "Open tail is marked", "P1", verdict, msg, ev)


def v_c1_survivorship(trades):
    """Any bucket whose headline rests on too few closed trades is not a result."""
    ev, thin = [], []
    for r in fs.promotion_progress(trades):
        tot = r["closed"] + r["open"]
        if not tot:
            continue
        ev.append(f"{r['label']:20} closed={r['closed']:2} open={r['open']:2} "
                  f"-> {r['closed']/tot*100:3.0f}% matured   win={r['win_pct']:.0f}%")
        if 0 < r["closed"] < MIN_CLOSED_FOR_A_CLAIM:
            thin.append(f"{r['label']} (n={r['closed']})")
    hold_w = [t.get("td_held") for t in trades
              if fs.is_closed(t) and fs.integrity(t) == fs.SCORED
              and (t.get("pnl_dollars") or 0) > 0 and t.get("td_held") is not None]
    hold_l = [t.get("td_held") for t in trades
              if fs.is_closed(t) and fs.integrity(t) == fs.SCORED
              and (t.get("pnl_dollars") or 0) <= 0 and t.get("td_held") is not None]
    if hold_w and hold_l:
        ev.append("")
        ev.append(f"avg hold: winners {sum(hold_w)/len(hold_w):.1f} td (n={len(hold_w)}), "
                  f"losers {sum(hold_l)/len(hold_l):.1f} td (n={len(hold_l)})")
    if thin:
        return _result("C1", "Headlines rest on a matured sample", "P1", FAIL,
                       f"Claims resting on <{MIN_CLOSED_FOR_A_CLAIM} closed trades: "
                       f"{', '.join(thin)}.", ev)
    return _result("C1", "Headlines rest on a matured sample", "P1", PASS,
                   "Every reported bucket has a usable closed sample.", ev)


# ── C2: were voids legitimate? ───────────────────────────────────

def v_c2_voids(trades):
    voids = [t for t in trades if t.get("outcome") == "void"]
    if not voids:
        return _result("C2", "Voids are structural, not outcome-based", "P2", PASS,
                       "No voided records.")
    ev, suspicious = [], []
    for t in voids:
        note = t.get("notes_exit") or ""      # match on the FULL note...
        pnl = t.get("pnl_dollars")
        ev.append(f"{t.get('trade_id')} {str(t.get('book')):12} pnl={pnl} {note[:70]}")
        structural = any(k in note.lower() for k in    # ...not the display slice
                         ("stub", "thrash", "placeholder", "synthetic", "scratch"))
        if not structural:
            suspicious.append(t.get("trade_id"))
        if isinstance(pnl, (int, float)) and pnl < 0:
            suspicious.append(t.get("trade_id"))
    if suspicious:
        return _result("C2", "Voids are structural, not outcome-based", "P2", FAIL,
                       f"Voids needing justification: {sorted(set(suspicious))}.", ev)
    return _result("C2", "Voids are structural, not outcome-based", "P2", PASS,
                   f"All {len(voids)} voids cite a structural reason and carry no P&L.", ev)


# ── C4: duplicates ───────────────────────────────────────────────

def v_c4_duplicates(trades):
    sig = defaultdict(list)
    for t in trades:
        key = (t.get("entry_date"), t.get("ticker"), t.get("strategy"),
               t.get("entry_price"), t.get("size"))
        sig[key].append(t.get("trade_id"))
    dups = {k: v for k, v in sig.items() if len(v) > 1}
    if not dups:
        return _result("C4", "No duplicate records inflate n", "P3", PASS,
                       "Every record has a distinct entry signature.")
    ev = [f"x{len(v)}  {k[1]} {k[2]} @ {k[3]} on {k[0]}  ids={v}"
          for k, v in list(dups.items())[:10]]
    return _result("C4", "No duplicate records inflate n", "P3", WARN,
                   f"{len(dups)} duplicate entry signatures "
                   f"({sum(len(v) for v in dups.values())} records).", ev)


# ── D1: regime coverage ──────────────────────────────────────────

def v_d1_regime_coverage(trades):
    plans_path = os.path.join(config.LOG_DIR, "spy_daily_plans.json")
    try:
        with open(plans_path) as fh:
            plans = json.load(fh)
    except Exception:
        plans = {}
    if isinstance(plans, list):
        plans = {p.get("date"): p for p in plans if isinstance(p, dict)}
    regime_of = {d: (p or {}).get("regime") for d, p in (plans or {}).items()}
    counts = Counter()
    for t in trades:
        if not fs.is_closed(t) or fs.integrity(t) != fs.SCORED:
            continue
        day = str(t.get("entry_date") or "")[:10]
        counts[regime_of.get(day) or "unknown"] += 1
    known = [
        "choppy_low_vol", "choppy_transition", "choppy_high_vol",
        "trending_up_calm", "trending_high_vol", "event_day",
    ]
    ev = [f"{r:22} n={counts.get(r, 0)}" for r in known]
    if counts.get("unknown"):
        ev.append(f"{'unknown/no plan':22} n={counts['unknown']}")
    untested = [r for r in known if counts.get(r, 0) == 0]
    thin = [r for r in known if 0 < counts.get(r, 0) < MIN_CLOSED_FOR_A_CLAIM]
    days = sorted(str(t.get("entry_date") or "")[:10] for t in trades if t.get("entry_date"))
    ev.append("")
    ev.append(f"sample span: {days[0]} -> {days[-1]} ({len(set(days))} entry days)")
    ev.append("A premium-selling book is SUPPOSED to look good in a calm drift. "
              "Regimes with n=0 are untested, not validated.")
    if untested or thin:
        return _result("D1", "Sample covers more than one market state", "P1", FAIL,
                       f"Untested regimes: {untested or 'none'}; thin: {thin or 'none'}.", ev)
    return _result("D1", "Sample covers more than one market state", "P1", PASS,
                   "Every regime has a usable sample.", ev)


# ── D2: is any of this distinguishable from noise? ───────────────

def v_d2_confidence(trades):
    books = fs.book_stats(trades)
    ev, noisy = [], []
    for name, st in sorted(books.items()):
        n, w = st["n"], st["wins"]
        if not n:
            continue
        lo, hi = wilson(w, n)
        # one-sided p under a fair coin
        p = sum(math.comb(n, k) for k in range(w, n + 1)) / (2 ** n)
        verdict = "indistinguishable from a coin flip" if lo <= 50 else "beats 50% at 95%"
        ev.append(f"{name:12} {w}/{n} = {st['win_pct']:5.1f}%  95% CI [{lo:4.1f}, {hi:5.1f}]  "
                  f"p={p:.3f}  {verdict}")
        if lo <= 50:
            noisy.append(name)
    ev.append("")
    ev.append("CI is the Wilson score interval. A lower bound at or below 50% "
              "means the observed win rate does not yet exclude chance.")
    if noisy:
        return _result("D2", "Results are distinguishable from noise", "P1", FAIL,
                       f"Books whose win rate does not beat chance: {', '.join(noisy)}.", ev)
    return _result("D2", "Results are distinguishable from noise", "P1", PASS,
                   "Every book's win rate excludes 50% at 95%.", ev)


# ── E2: pre/post strategy-change consistency ─────────────────────

IVR_VETO_REMOVED = "2026-08-14"


def v_e2_rule_change(trades):
    """The IVR veto removal changed WHICH days trade. Pooling across that
    boundary describes a strategy that no longer exists."""
    scored = [t for t in trades if fs.is_closed(t) and fs.integrity(t) == fs.SCORED
              and fs.book_of(t) == "disciplined" and t.get("pnl_dollars") is not None]
    pre = [t for t in scored if str(t.get("entry_date") or "")[:10] < IVR_VETO_REMOVED]
    post = [t for t in scored if str(t.get("entry_date") or "")[:10] >= IVR_VETO_REMOVED]
    def agg(rows):
        if not rows:
            return "n=0"
        p = [float(t["pnl_dollars"]) for t in rows]
        return (f"n={len(p)} win={sum(1 for x in p if x>0)/len(p)*100:.0f}% "
                f"avg=${sum(p)/len(p):+.2f}")
    ev = [f"pre  {IVR_VETO_REMOVED}: {agg(pre)}",
          f"post {IVR_VETO_REMOVED}: {agg(post)}",
          "",
          "The IVR>=50 veto was removed on this date, changing which days "
          "qualify. Pre- and post- trades are governed by different rules."]
    if len(post) < MIN_CLOSED_FOR_A_CLAIM:
        return _result("E2", "Disciplined book is one consistent strategy", "P2", FAIL,
                       f"Only {len(post)} closed trades exist under the CURRENT "
                       "ruleset; the headline is mostly a retired strategy.", ev)
    return _result("E2", "Disciplined book is one consistent strategy", "P2", PASS,
                   "Enough trades under the current ruleset to stand alone.", ev)


# ── F1: does prediction accuracy survive a push band? ────────────

def v_f1_push_band():
    path = os.path.join(config.LOG_DIR, "learning", "predictions.jsonl")
    try:
        rows = [json.loads(l) for l in open(path) if l.strip()]
    except Exception:
        return _result("F1", "'Correct' means something", "P2", WARN,
                       "No prediction log found.")
    scored = [r for r in rows if r.get("resolved")
              and r.get("outcome") in ("correct", "wrong")
              and isinstance(r.get("actual_move_pct"), (int, float))
              and r.get("direction") in ("bullish", "bearish")]
    if not scored:
        return _result("F1", "'Correct' means something", "P2", WARN,
                       "No resolved directional predictions with a move recorded.")
    ev, decay = [], []
    for band in (0.0, 0.25, 0.5, 1.0):
        kept = [r for r in scored if abs(r["actual_move_pct"]) >= band]
        if not kept:
            ev.append(f"band +/-{band:.2f}%  n=0")
            continue
        right = sum(1 for r in kept
                    if (r["actual_move_pct"] > 0) == (r["direction"] == "bullish"))
        acc = right / len(kept) * 100
        lo, hi = wilson(right, len(kept))
        decay.append(acc)
        ev.append(f"band +/-{band:.2f}%  n={len(kept):3}  acc={acc:5.1f}%  "
                  f"CI [{lo:4.1f}, {hi:5.1f}]")
    ev.append("")
    ev.append("Excluding tiny moves tests whether the call has real directional "
              "information or is riding noise that resolves near zero.")
    if decay and decay[-1] < 50:
        return _result("F1", "'Correct' means something", "P2", FAIL,
                       "Accuracy falls below a coin flip once tiny moves are excluded.", ev)
    if decay and decay[0] - decay[-1] > 10:
        return _result("F1", "'Correct' means something", "P2", WARN,
                       f"Accuracy decays {decay[0]-decay[-1]:.0f} pts as the push "
                       "band widens — partly noise-driven.", ev)
    return _result("F1", "'Correct' means something", "P2", PASS,
                   "Accuracy holds up when tiny moves are excluded.", ev)


# ── runner ───────────────────────────────────────────────────────

VALIDATORS = [
    v_a1_unscored, v_a2_sign_conventions, v_a3_entry_value_scaling,
    v_a4_commissions, v_b1_impossible_fills, v_b2_open_marks,
    v_c1_survivorship, v_c2_voids, v_c4_duplicates,
    v_d1_regime_coverage, v_d2_confidence, v_e2_rule_change,
]


def run_all(trades: list[dict] | None = None) -> list[dict]:
    trades = _load_trades() if trades is None else trades
    out = []
    for fn in VALIDATORS:
        try:
            out.append(fn(trades))
        except Exception as e:  # a broken validator must not hide the others
            out.append(_result(fn.__name__, fn.__name__, "?", WARN,
                               f"validator raised: {e}"))
    try:
        out.append(v_f1_push_band())
    except Exception as e:
        out.append(_result("F1", "'Correct' means something", "P2", WARN,
                           f"validator raised: {e}"))
    return out


def main():
    results = run_all()
    icon = {PASS: "PASS", FAIL: "FAIL", WARN: "WARN", INFO: "INFO"}
    print("=" * 78)
    print("FORWARD-TEST AUDIT — attempting to disprove the live record")
    print("=" * 78)
    for r in results:
        print(f"\n[{icon.get(r['verdict'], '?')}] {r['severity']} {r['id']} — {r['name']}")
        print(f"       {r['headline']}")
        for line in r["evidence"]:
            print(f"         {line}")
    tally = Counter(r["verdict"] for r in results)
    print("\n" + "=" * 78)
    print(f"  FAIL={tally.get(FAIL,0)}  WARN={tally.get(WARN,0)}  PASS={tally.get(PASS,0)}")
    fails = [r for r in results if r["verdict"] == FAIL and r["severity"] == "P1"]
    if fails:
        print("\n  P1 failures block sizing real money against these numbers:")
        for r in fails:
            print(f"    {r['id']} — {r['headline']}")
    print("=" * 78)


if __name__ == "__main__":
    main()

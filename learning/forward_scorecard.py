"""learning/forward_scorecard.py -- the live forward-test scorecard.

One-glance answer to "is the edge real, out of sample, with real days?" —
aggregated from the live journal rather than a backtest replay.

DESIGN RULE: this module's first duty is to NOT LIE. It was written after
discovering that 32 of 77 closed trades carried a fabricated $0 P&L:
positions are recorded with strategy="put_debit_spread"/"call_debit_spread",
but `TradeRecorder._calculate_pnl` only branches on "debit_spread", so those
records fell through to `return 0, 0` and were filed as "breakeven". They then
sat in the win-rate denominator as non-wins, quietly diluting every headline
number on the /learning page.

So every closed record is classified before it is counted:

  SCORED       P&L the engine could actually compute -> counted
  UNSCORED     strategy matches no _calculate_pnl branch -> recorded P&L is a
               meaningless 0; excluded from headlines, recomputed for display
  SUSPECT_FILL exited at exactly $0.00 via an auto-STOP -> the fill lookup
               failed (Polygon snapshots carry no quotes), so both the outcome
               and the P&L are untrustworthy; excluded
  VOID         explicitly voided (synthetic stubs, rh-sync thrash)

Headline stats use SCORED only, and every aggregate carries the excluded count
so a clean-looking number can never hide a dirty sample.
"""
from __future__ import annotations

import json
import math
import os

from loguru import logger

# ── integrity classes ────────────────────────────────────────────
SCORED = "scored"
UNSCORED = "unscored"
SUSPECT_FILL = "suspect_fill"
VOID = "void"

# Single source of truth — imported, never mirrored. A local copy of this set
# is exactly how the two implementations drifted apart in the first place.
from journal.trade_recorder import _pnl_convention  # noqa: E402

# Promotion bars, mirrored from the forward-test modules so the dashboard can
# show progress toward them. (bucket -> (label, n, win%, avg$))
PROMOTION_BARS = {
    "BWB-30DTE": ("Broken-wing 30DTE", 15, 70.0, 20.0),
    "BWB-45DTE": ("Broken-wing 45DTE", 15, 70.0, 20.0),
    "7DTE":      ("7DTE condor",       15, 70.0, 20.0),
    "14DTE":     ("14DTE condor",      15, 70.0, 20.0),
    "21DTE":     ("21DTE condor",      15, 70.0, 20.0),
    "qqq_condor": ("QQQ condor",       15, 70.0, 20.0),
    "dipbuy":    ("Dip-buy",           15, 70.0, 20.0),
}

_CLOSED_EXCLUDED = ("open", None, "")


def is_closed(trade: dict) -> bool:
    """A record is closed once it has a terminal outcome (void included)."""
    return trade.get("outcome") not in _CLOSED_EXCLUDED


def book_of(trade: dict) -> str:
    """Records predating the book split count as disciplined (matches
    PredictionLog.accuracy's convention)."""
    return trade.get("book") or "disciplined"


def integrity(trade: dict) -> str:
    """Classify how much we can trust this record's P&L.

    Checked in order of how badly each defect corrupts the number.
    """
    outcome = trade.get("outcome")
    if outcome == "void":
        return VOID
    # The recorder now marks what it cannot score, instead of writing a $0.
    if outcome == "unscored" or trade.get("pnl_dollars") is None:
        return UNSCORED

    exit_price = trade.get("exit_price")
    entry = trade.get("entry_price")
    notes = (trade.get("notes_exit") or "").lower()

    # A stop is by definition a PARTIAL loss; it cannot fill at zero. When the
    # price lookup fails it returns 0.0 and the trade books a phantom exit.
    # Expiring worthless is a genuine $0 and stays SCORED.
    if exit_price is not None and float(exit_price) == 0.0 and "stop" in notes:
        return SUSPECT_FILL

    if _pnl_convention(trade.get("strategy")) is None:
        return UNSCORED

    # A closed trade with no exit price cannot be checked against anything.
    if exit_price is None or entry is None:
        return UNSCORED

    # Legacy fabricated zero: the old engine returned (0, 0) for strategies it
    # did not recognise. A P&L of exactly $0 while entry and exit differ is
    # arithmetically impossible, so it identifies those records regardless of
    # what the strategy is called.
    try:
        if (float(trade.get("pnl_dollars")) == 0.0 and entry is not None
                and exit_price is not None
                and abs(float(entry) - float(exit_price)) > 0.01):
            return UNSCORED
    except (TypeError, ValueError):
        return UNSCORED
    return SCORED


def recompute_pnl(trade: dict) -> float | None:
    """Best-effort true P&L for a record the engine could not score.

    Returns None when the record is already trustworthy or lacks an exit price.
    Debit-convention only (exit - entry); the unscored population is entirely
    put/call debit spreads.
    """
    if integrity(trade) != UNSCORED:
        return None
    entry, exit_price = trade.get("entry_price"), trade.get("exit_price")
    if entry is None or exit_price is None:
        return None
    size = trade.get("size") or 1
    convention = _pnl_convention(trade.get("strategy"))
    try:
        entry, exit_price, size = float(entry), float(exit_price), float(size)
    except (TypeError, ValueError):
        return None
    if convention == "credit":
        return round((entry - exit_price) * 100 * size, 2)
    if convention == "debit":
        return round((exit_price - entry) * 100 * size, 2)
    # No convention: an unrecognised structure stays unrecoverable by design.
    return None


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a win rate.

    Shown next to every point estimate because at n=13 a 61.5% win rate spans
    [35.5, 82.3] — it does not exclude a coin flip, and a bare "61.5%" implies
    a confidence the sample cannot support (audit D2).
    """
    if n <= 0:
        return (0.0, 0.0)
    p = wins / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0.0, (centre - half) * 100), 1),
            round(min(100.0, (centre + half) * 100), 1))


def net_pnl(trade: dict) -> float | None:
    """P&L after round-trip commissions (audit A4).

    Prefers the stored `pnl_net`; computes it for records written before
    commissions were modelled, so old and new trades are comparable.
    """
    stored = trade.get("pnl_net")
    if stored is not None:
        try:
            return float(stored)
        except (TypeError, ValueError):
            pass
    gross = trade.get("pnl_dollars")
    if gross is None:
        return None
    from journal.trade_recorder import round_trip_commission
    try:
        fee = round_trip_commission(trade.get("strategy"), trade.get("legs"),
                                    trade.get("size"))
        return round(float(gross) - fee, 2)
    except (TypeError, ValueError):
        return None


def _blank_stats() -> dict:
    return {"n": 0, "wins": 0, "win_pct": 0.0, "total": 0.0,
            "avg": 0.0, "worst": 0.0, "excluded": 0,
            "ci_low": 0.0, "ci_high": 0.0, "beats_chance": False,
            "net_total": 0.0, "net_avg": 0.0, "fees": 0.0}


def book_stats(trades: list[dict]) -> dict[str, dict]:
    """Per-book aggregates over SCORED closed records only.

    `excluded` counts closed records dropped for untrustworthy P&L — surfaced
    so the win rate can never quietly rest on a filtered sample.
    """
    out: dict[str, dict] = {}
    for t in trades:
        if not is_closed(t):
            continue
        book = book_of(t)
        st = out.setdefault(book, _blank_stats())
        if integrity(t) != SCORED:
            st["excluded"] += 1
            continue
        pnl = t.get("pnl_dollars")
        if pnl is None:
            st["excluded"] += 1
            continue
        pnl = float(pnl)
        net = net_pnl(t)
        st["n"] += 1
        st["wins"] += 1 if pnl > 0 else 0
        st["total"] += pnl
        st["worst"] = min(st["worst"], pnl)
        if net is not None:
            st["net_total"] += net
            st["fees"] += pnl - net
    for st in out.values():
        if st["n"]:
            st["win_pct"] = round(st["wins"] / st["n"] * 100, 1)
            st["avg"] = round(st["total"] / st["n"], 2)
            st["total"] = round(st["total"], 2)
            st["net_total"] = round(st["net_total"], 2)
            st["net_avg"] = round(st["net_total"] / st["n"], 2)
            st["fees"] = round(st["fees"], 2)
            st["ci_low"], st["ci_high"] = wilson(st["wins"], st["n"])
            st["beats_chance"] = st["ci_low"] > 50.0
    return out


def strategy_stats(trades: list[dict], books: tuple[str, ...] | None = None
                   ) -> list[dict]:
    """Per-(book, strategy) breakdown of SCORED closed records."""
    agg: dict[tuple[str, str], dict] = {}
    for t in trades:
        if not is_closed(t) or integrity(t) != SCORED:
            continue
        book = book_of(t)
        if books and book not in books:
            continue
        pnl = t.get("pnl_dollars")
        if pnl is None:
            continue
        key = (book, str(t.get("strategy")))
        st = agg.setdefault(key, {"book": book, "strategy": key[1],
                                  "n": 0, "wins": 0, "total": 0.0})
        st["n"] += 1
        st["wins"] += 1 if float(pnl) > 0 else 0
        st["total"] += float(pnl)
    rows = []
    for st in agg.values():
        st["win_pct"] = round(st["wins"] / st["n"] * 100, 1) if st["n"] else 0.0
        st["avg"] = round(st["total"] / st["n"], 2) if st["n"] else 0.0
        st["total"] = round(st["total"], 2)
        rows.append(st)
    return sorted(rows, key=lambda r: (-r["n"], r["strategy"]))


def integrity_summary(trades: list[dict]) -> dict:
    """Counts per integrity class + the share of closed records we can trust."""
    out = {SCORED: 0, UNSCORED: 0, SUSPECT_FILL: 0, VOID: 0, "open": 0}
    for t in trades:
        if not is_closed(t):
            out["open"] += 1
            continue
        out[integrity(t)] += 1
    closed = out[SCORED] + out[UNSCORED] + out[SUSPECT_FILL] + out[VOID]
    out["closed"] = closed
    out["trust_pct"] = round(out[SCORED] / closed * 100, 1) if closed else 0.0
    # Value hidden by the unscored records, for the audit banner.
    recovered = [recompute_pnl(t) for t in trades
                 if is_closed(t) and integrity(t) == UNSCORED]
    recovered = [r for r in recovered if r is not None]
    out["unscored_recoverable"] = len(recovered)
    out["unscored_hidden_pnl"] = round(sum(recovered), 2)
    return out


def promotion_progress(trades: list[dict]) -> list[dict]:
    """Candidate-generator progress toward each promotion bar."""
    rows = []
    for bucket, (label, tgt_n, tgt_win, tgt_avg) in PROMOTION_BARS.items():
        closed = [t for t in trades
                  if t.get("dte_bucket") == bucket and is_closed(t)
                  and integrity(t) == SCORED and t.get("pnl_dollars") is not None]
        open_n = sum(1 for t in trades
                     if t.get("dte_bucket") == bucket and not is_closed(t))
        # The bar decides real money, so it is judged NET of commissions: a
        # trade that wins gross and loses net is a loss (audit A4).
        pnls = [p for p in (net_pnl(t) for t in closed) if p is not None]
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        win_pct = round(wins / n * 100, 1) if n else 0.0
        avg = round(sum(pnls) / n, 2) if n else 0.0
        rows.append({
            "bucket": bucket, "label": label, "closed": n, "open": open_n,
            "win_pct": win_pct, "avg": avg, "total": round(sum(pnls), 2),
            "target_n": tgt_n, "target_win": tgt_win, "target_avg": tgt_avg,
            "pct_of_bar": round(min(n / tgt_n, 1.0) * 100, 1) if tgt_n else 0.0,
            "met": bool(n >= tgt_n and win_pct >= tgt_win and avg > tgt_avg),
        })
    return sorted(rows, key=lambda r: (-r["closed"] - r["open"], r["label"]))


def open_positions(trades: list[dict]) -> list[dict]:
    """Open roster, newest first — the unrealized tail of the forward test."""
    rows = []
    for t in trades:
        if is_closed(t):
            continue
        rows.append({
            "trade_id": t.get("trade_id"), "ticker": t.get("ticker"),
            "strategy": t.get("strategy"), "book": book_of(t),
            "bucket": t.get("dte_bucket"), "entry_date": t.get("entry_date"),
            "entry_price": t.get("entry_price"), "size": t.get("size"),
            "max_loss": t.get("max_loss"),
        })
    return sorted(rows, key=lambda r: str(r["entry_date"] or ""), reverse=True)


def prediction_stats(window: int = 250) -> dict:
    """Directional accuracy + stand-down quality from the prediction log."""
    empty = {"sample": 0, "accuracy": 0.0, "correct": 0, "wrong": 0,
             "skips": 0, "skip_right_pct": 0.0}
    try:
        from learning.predictions import PredictionLog
        log = PredictionLog()
        acc = log.accuracy(n=window) or {}
        skip = log.skip_quality(n=window) or {}
        rows = log.recent(window) or []
    except Exception as e:
        logger.warning(f"forward_scorecard: prediction stats failed: {e}")
        return empty
    return {
        "sample": acc.get("sample", 0),
        "accuracy": round(float(acc.get("accuracy", 0.0)), 1),
        "correct": acc.get("correct", 0),
        "wrong": acc.get("wrong", 0),
        "skips": sum(1 for r in rows if r.get("outcome") == "skip"),
        "skip_right_pct": round(float(skip.get("right_pct", 0.0)), 1),
    }


REGIMES = ("choppy_low_vol", "choppy_transition", "choppy_high_vol",
           "trending_up_calm", "trending_high_vol", "event_day")


def regime_coverage(trades: list[dict]) -> list[dict]:
    """Which market states the live sample has actually seen (audit D1).

    A regime with n=0 is UNTESTED, not validated — and a premium-selling book
    is supposed to look good in the calm drift that dominates this sample.
    """
    import config
    try:
        with open(os.path.join(config.LOG_DIR, "spy_daily_plans.json")) as fh:
            plans = json.load(fh)
    except Exception:
        plans = {}
    if isinstance(plans, list):
        plans = {p.get("date"): p for p in plans if isinstance(p, dict)}
    regime_of = {d: (p or {}).get("regime") for d, p in (plans or {}).items()}

    counts: dict[str, int] = {r: 0 for r in REGIMES}
    unknown = 0
    for t in trades:
        if not is_closed(t) or integrity(t) != SCORED:
            continue
        day = str(t.get("entry_date") or "")[:10]
        r = regime_of.get(day)
        if r in counts:
            counts[r] += 1
        else:
            unknown += 1
    rows = [{"regime": r, "n": counts[r],
             "state": ("untested" if counts[r] == 0
                       else "thin" if counts[r] < 10 else "covered")}
            for r in REGIMES]
    if unknown:
        rows.append({"regime": "no plan recorded", "n": unknown, "state": "thin"})
    return rows


def open_exposure(trades: list[dict]) -> dict:
    """Mark the open tail (audit B2). Best-effort — never raises, and says so
    when it cannot mark rather than implying $0."""
    from datetime import date
    out = {"count": sum(1 for t in trades if not is_closed(t)),
           "marked": 0, "unrealized": None, "by_book": {}, "note": ""}
    if not out["count"]:
        out["note"] = "Nothing open."
        return out
    try:
        from learning.exit_manager import ExitManager
        from alerts.stop_watchdog import yf_spot
        spy, vix = yf_spot("SPY"), yf_spot("^VIX")
        if not spy or not vix:
            raise RuntimeError("no live SPY/VIX")
        em = ExitManager.__new__(ExitManager)
        by_book: dict[str, float] = {}
        total = 0.0
        for t in trades:
            if is_closed(t):
                continue
            legs = t.get("legs") or []
            exp = ExitManager._nearest_expiration(legs)
            if not exp or not legs:
                continue
            mark = em._mark_exit_price(t.get("strategy"), legs, spy, vix,
                                       date.today(), (exp - date.today()).days)
            pnl = ExitManager._pnl_dollars(t.get("strategy"), t.get("entry_price"),
                                           mark, t.get("size"))
            if pnl is None:
                continue
            by_book[book_of(t)] = round(by_book.get(book_of(t), 0.0) + pnl, 2)
            total += pnl
            out["marked"] += 1
        out["unrealized"] = round(total, 2)
        out["by_book"] = by_book
        out["note"] = (f"Modelled at SPY {spy:.2f} / VIX {vix:.2f} — no live "
                       "option quotes exist, so this is a model mark.")
    except Exception as e:
        logger.warning(f"forward_scorecard: open marking failed: {e}")
        out["note"] = ("Could not mark the open tail — its P&L is unknown and "
                       "sits outside every number here.")
    return out


def _load_trades() -> list[dict]:
    try:
        from journal.trade_recorder import TradeRecorder
        return TradeRecorder().get_all_trades() or []
    except Exception as e:
        logger.warning(f"forward_scorecard: trade load failed: {e}")
        return []


def scorecard() -> dict:
    """Assemble the whole card. Never raises — the dashboard depends on it."""
    trades = _load_trades()
    books = book_stats(trades)
    # The headline is the disciplined book: the real-money proxy.
    headline = books.get("disciplined", _blank_stats())
    live = books.get("live", _blank_stats())
    return {
        "books": books,
        "headline": headline,
        "live": live,
        "strategies": strategy_stats(trades),
        "integrity": integrity_summary(trades),
        "promotion": promotion_progress(trades),
        "open_positions": open_positions(trades),
        "open_exposure": open_exposure(trades),
        "regime_coverage": regime_coverage(trades),
        "predictions": prediction_stats(),
        "total_records": len(trades),
    }

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

from loguru import logger

# ── integrity classes ────────────────────────────────────────────
SCORED = "scored"
UNSCORED = "unscored"
SUSPECT_FILL = "suspect_fill"
VOID = "void"

# Mirrors the branches in TradeRecorder._calculate_pnl. A strategy outside this
# set silently yields a $0 P&L, which is why the drift is guarded by a test.
PNL_HANDLED_STRATEGIES = frozenset({
    "stock", "debit_spread", "credit_spread",
    "iron_condor", "broken_wing", "single_leg",
})

# Debit-convention structures whose true P&L we can rebuild from entry/exit.
_DEBIT_LIKE = ("debit_spread", "single_leg", "custom")

# Promotion bars, mirrored from the forward-test modules so the dashboard can
# show progress toward them. (bucket -> (label, n, win%, avg$))
PROMOTION_BARS = {
    "BWB-30DTE": ("Broken-wing 30DTE", 15, 70.0, 20.0),
    "BWB-45DTE": ("Broken-wing 45DTE", 15, 70.0, 20.0),
    "7DTE":      ("7DTE condor",       15, 70.0, 20.0),
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
    """Classify how much we can trust this record's P&L."""
    if trade.get("outcome") == "void":
        return VOID
    if trade.get("strategy") not in PNL_HANDLED_STRATEGIES:
        return UNSCORED
    # A stop is defined as a *partial* loss; it cannot fill at zero. When the
    # exit-price lookup fails it returns 0.0 and the trade books a phantom
    # breakeven, so treat a zero stop-fill as untrustworthy. Expiring worthless
    # is a genuine $0 and stays SCORED.
    exit_price = trade.get("exit_price")
    notes = (trade.get("notes_exit") or "").lower()
    if exit_price is not None and float(exit_price) == 0.0 and "stop" in notes:
        return SUSPECT_FILL
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
    strategy = str(trade.get("strategy") or "")
    try:
        if any(k in strategy for k in _DEBIT_LIKE) or strategy.endswith("_spread"):
            return round((float(exit_price) - float(entry)) * 100 * float(size), 2)
    except (TypeError, ValueError):
        return None
    return None


def _blank_stats() -> dict:
    return {"n": 0, "wins": 0, "win_pct": 0.0, "total": 0.0,
            "avg": 0.0, "worst": 0.0, "excluded": 0}


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
        st["n"] += 1
        st["wins"] += 1 if pnl > 0 else 0
        st["total"] += pnl
        st["worst"] = min(st["worst"], pnl)
    for st in out.values():
        if st["n"]:
            st["win_pct"] = round(st["wins"] / st["n"] * 100, 1)
            st["avg"] = round(st["total"] / st["n"], 2)
            st["total"] = round(st["total"], 2)
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
        pnls = [float(t["pnl_dollars"]) for t in closed]
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
        "predictions": prediction_stats(),
        "total_records": len(trades),
    }

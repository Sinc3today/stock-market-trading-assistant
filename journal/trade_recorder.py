"""
journal/trade_recorder.py — Trade Recorder
Logs actual trades taken. Supports stocks, single leg options,
debit spreads, credit spreads, and iron condors.

Usage:
    from journal.trade_recorder import TradeRecorder
    tr = TradeRecorder()
    trade_id = tr.log_entry(ticker="AAPL", entry_price=170.0, size=10)
    tr.log_exit(trade_id, exit_price=182.0)
"""

import fcntl
import functools
from contextlib import contextmanager
import json
import os
import uuid
from datetime import datetime
from loguru import logger
import pytz
import config


# ── Valid strategy types ──────────────────────────────────────
STRATEGY_TYPES = [
    "stock",
    "single_leg",
    "debit_spread",
    "credit_spread",
    "iron_condor",
]

# ── P&L sign conventions ──────────────────────────────────────
# CREDIT structures are sold to open: profit = entry - exit.
# DEBIT structures are bought to open: profit = exit - entry.
# Positions are recorded under variant names ("put_debit_spread",
# "call_debit_spread"), so matching is by suffix as well as exact name —
# an exact-only match is what silently zeroed 32 live trades
# (docs/FORWARD_TEST_AUDIT.md A1).
_CREDIT_STRATEGIES = frozenset({"credit_spread", "iron_condor", "broken_wing"})
_DEBIT_STRATEGIES  = frozenset({"debit_spread", "single_leg", "butterfly"})

# Names that CANNOT determine the convention, by design — not a gap waiting to
# be filled. rh_sync emits "custom" for a Robinhood position it cannot name (a
# 3-leg, 5+-leg, or hand-edited structure), and "none" is the no-trade stub.
# Whether such a position was opened for a credit or a debit is a property of
# its legs and their prices, not of the word "custom", so there is no set to
# add them to.
#
# The distinction exists for the LOG, not the outcome: both still refuse to
# score. But the old message told you to "add it to _CREDIT_STRATEGIES /
# _DEBIT_STRATEGIES", which is right for a typo and wrong for these, and it
# fired at ERROR on every audit run for two permanent records. A message that
# always fires stops meaning anything.
_UNCLASSIFIABLE_STRATEGIES = frozenset({"custom", "none"})


def round_trip_commission(strategy: str | None, legs: list | None,
                          size: float | None) -> float:
    """Total commission to open AND close this position, in dollars.

    Per contract, per leg, per side. Stock pays no per-contract fee. A trade
    with no recorded legs is assumed single-leg rather than free — the honest
    default for a missing field is a cost, not a discount.
    """
    if (strategy or "").lower() == "stock":
        return 0.0
    n_legs = len(legs) if legs else 1
    try:
        size = float(size or 1)
    except (TypeError, ValueError):
        size = 1.0
    return round(config.COMMISSION_PER_CONTRACT_LEG * n_legs * 2 * size, 2)


def _with_journal_lock(fn):
    """Hold an exclusive cross-process lock for the whole read-modify-write.

    Four independently-scheduled jobs mutate trades.json at 09:45 ET, and one
    of them runs in the separate uvicorn process, so a threading.Lock cannot
    help. _save is atomic, which prevents a TORN file but not a LOST UPDATE:
    two writers both load, both save, and the second snapshot erases the
    first's trade. 29 same-minute multi-writes already exist in the journal.

    The lock lives on a sidecar file, not on trades.json — an atomic replace
    swaps the inode, so a lock held on the journal itself would be released by
    the very write it is guarding.
    """
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._locked():
            return fn(self, *args, **kwargs)
    return wrapper


def _pnl_convention(strategy: str | None) -> str | None:
    """'credit' | 'debit' | None for an unrecognised structure.

    Suffix matching covers the variant names producers actually emit:
    put_debit_spread, call_credit_spread, and the router's bull_debit /
    bear_debit (which reach the journal through dipbuy_forward's default).
    Enumerated by tools/enumerate_conventions — run it after adding a producer.

    Returning None is a real answer, not a gap: rh_sync emits "custom" for a
    3-leg or 5+-leg position it cannot name, and refusing to score that is
    correct. Guessing is what produced the sign-flipped records.
    """
    s = (strategy or "").strip().lower()
    if s in _CREDIT_STRATEGIES:
        return "credit"
    if s in _DEBIT_STRATEGIES:
        return "debit"
    if s.endswith("_credit_spread") or s.endswith("_credit"):
        return "credit"
    if s.endswith("_debit_spread") or s.endswith("_debit"):
        return "debit"
    return None


def convention_status(strategy: str | None) -> str:
    """'credit' | 'debit' | 'unclassifiable' | 'unknown'.

    Same answer as _pnl_convention for the first two; splits its None into the
    two cases that need different responses. 'unclassifiable' is expected and
    permanent (see _UNCLASSIFIABLE_STRATEGIES); 'unknown' is a defect — a typo
    or an unwired producer — and is the only one worth an ERROR.

    Callers that must decide "can I score this?" should keep using
    _pnl_convention. This is for diagnosis.
    """
    conv = _pnl_convention(strategy)
    if conv:
        return conv
    if (strategy or "").strip().lower() in _UNCLASSIFIABLE_STRATEGIES:
        return "unclassifiable"
    return "unknown"


class TradeRecorder:
    """
    Records actual trades including multi-leg options strategies.
    P&L calculated correctly for each strategy type.
    """

    def __init__(self):
        os.makedirs(config.LOG_DIR, exist_ok=True)
        self.trades_path = os.path.join(config.LOG_DIR, "trades.json")
        self.simulated_path = os.path.join(config.LOG_DIR, "simulated_trades.json")

    # ─────────────────────────────────────────
    # ENTRY LOGGING
    # ─────────────────────────────────────────

    @_with_journal_lock
    def log_entry(
        self,
        ticker:          str,
        entry_price:     float,       # For stocks: share price. For spreads: net debit/credit
        size:            float,       # Shares for stock, contracts for options
        trade_type:      str  = "stock",
        strategy:        str  = None, # debit_spread, credit_spread, iron_condor, single_leg
        direction:       str  = "bullish",
        mode:            str  = "swing",
        legs:            list = None, # List of leg dicts for options spreads
        max_profit:      float = None,
        max_loss:        float = None,
        alert_timestamp: str  = None,
        alert_score:     int  = None,
        notes:           str  = "",
        dte_bucket:      str | None = None,   # "0DTE" / "1-3DTE" / "45DTE"
        book:            str | None = None,   # "disciplined" / "learning"
        source:          str | None = None,   # "auto-paper" for bot-generated paper trades
        bot_mark:        float | None = None, # the bot's assumed price when you placed a
                                              # live copy — for slippage vs your real fill
    ) -> str:
        """
        Log a trade entry.

        For stocks:
            entry_price = share price
            size        = number of shares

        For single leg options:
            entry_price = premium paid per share
            size        = number of contracts
            legs        = [{"action": "BUY", "option_type": "CALL",
                            "strike": 170, "expiry": "2024-03-15"}]

        For debit/credit spreads:
            entry_price = net debit paid OR net credit received per share
            size        = number of spreads (contracts)
            legs        = [leg1_dict, leg2_dict]
            max_profit  = max profit per contract (optional, auto-calculated if blank)
            max_loss    = max loss per contract (optional)

        For iron condors:
            entry_price = total net credit received per share
            size        = number of condors
            legs        = [put_long, put_short, call_short, call_long]

        Returns:
            trade_id — 8 character ID e.g. "A3F9B2C1"
        """
        eastern  = pytz.timezone("US/Eastern")
        now_est  = datetime.now(eastern).strftime("%Y-%m-%d %I:%M %p EST")
        trade_id = str(uuid.uuid4())[:8].upper()

        # Derive strategy from trade_type if not explicitly set
        if strategy is None:
            strategy = trade_type if trade_type in STRATEGY_TYPES else "stock"

        # Calculate entry value
        entry_value = self._calculate_entry_value(
            strategy, entry_price, size, max_loss
        )

        trade = {
            # Identity
            "trade_id":    trade_id,
            "ticker":      ticker.upper(),
            "trade_type":  trade_type,
            "strategy":    strategy,
            "direction":   direction.upper(),
            "mode":        mode,

            # Entry
            "entry_price":  round(entry_price, 2),
            "size":         size,
            "entry_date":   now_est,
            "entry_value":  entry_value,

            # Options spread fields
            "legs":        legs or [],
            "max_profit":  max_profit,
            "max_loss":    max_loss,

            # Alert link
            "alert_timestamp": alert_timestamp,
            "alert_score":     alert_score,

            # Exit (filled later)
            "exit_price":  None,
            "exit_date":   None,
            "exit_value":  None,

            # Outcome (filled later)
            "outcome":     "open",
            "pnl_dollars": None,
            "pnl_pct":     None,
            "pnl_per_contract": None,

            # Notes
            "notes_entry": notes,
            "notes_exit":  "",
            "lessons":     "",

            # Per-strategy tags (Phase 2a)
            "dte_bucket": dte_bucket,
            "book":       book,

            # Provenance — "auto-paper" for bot-generated paper trades, else None
            "source":     source,

            # Slippage baseline — bot's assumed price when a live copy was placed
            "bot_mark":   bot_mark,
        }

        trades = self._load()
        trades.append(trade)
        self._save(trades)

        logger.info(
            f"Trade entry logged: [{trade_id}] "
            f"{ticker.upper()} {strategy} {direction} | "
            f"Entry: ${entry_price} × {size}"
        )
        return trade_id

    # ─────────────────────────────────────────
    # EXIT LOGGING
    # ─────────────────────────────────────────

    @_with_journal_lock
    def log_exit(
        self,
        trade_id:   str,
        exit_price: float,    # For spreads: net credit received to close (debit spread)
                              #              or net debit paid to close (credit spread)
        notes:      str = "",
        exit_reason: str | None = None,   # "target" / "stop" / "time_stop" / "target_intraday" / "expiry"
    ) -> bool:
        """
        Log the exit of an open trade.
        P&L calculated based on strategy type.

        For debit spreads:
            exit_price = what you sold the spread for
            P&L = (exit_price - entry_price) * size * 100

        For credit spreads:
            exit_price = what you paid to close (buy back)
            P&L = (entry_price - exit_price) * size * 100

        For iron condors:
            exit_price = what you paid to close
            P&L = (entry_price - exit_price) * size * 100
        """
        trades  = self._load()
        updated = False

        for trade in trades:
            if trade.get("trade_id") != trade_id.upper():
                continue

            eastern = pytz.timezone("US/Eastern")
            now_est = datetime.now(eastern).strftime("%Y-%m-%d %I:%M %p EST")

            entry    = trade["entry_price"]
            size     = trade["size"]
            strategy = trade.get("strategy", "stock")
            direction = trade["direction"]

            pnl_per_share, pnl_dollars = self._calculate_pnl(
                strategy, direction, entry, exit_price, size
            )

            # An unrecognised structure yields None, NOT a fake $0. Recording it
            # as "breakeven" is what hid 32 real trades (docs/FORWARD_TEST_AUDIT.md
            # A1) — a trade we cannot score must say so.
            if pnl_dollars is None:
                trade["exit_price"]       = round(exit_price, 2)
                trade["exit_date"]        = now_est
                trade["pnl_dollars"]      = None
                trade["pnl_pct"]          = None
                trade["pnl_per_contract"] = None
                trade["outcome"]          = "unscored"
                trade["notes_exit"]       = notes
                trade["exit_reason"]      = exit_reason
                updated = True
                _log = (logger.info
                        if convention_status(strategy) == "unclassifiable"
                        else logger.error)
                _log(
                    f"Trade exit UNSCORED: [{trade_id}] {trade['ticker']} "
                    f"{strategy} — no P&L convention for this strategy."
                    + ("" if convention_status(strategy) == "unclassifiable"
                       else " Add it to _calculate_pnl.")
                )
                break

            # P&L percentage
            cost_basis = self._get_cost_basis(strategy, entry, size, trade.get("max_loss"))
            pnl_pct    = round((pnl_dollars / abs(cost_basis)) * 100, 2) \
                         if cost_basis else 0

            # Outcome
            if pnl_dollars > 0.01:
                outcome = "win"
            elif pnl_dollars < -0.01:
                outcome = "loss"
            else:
                outcome = "breakeven"

            trade["exit_price"]       = round(exit_price, 2)
            trade["exit_date"]        = now_est
            trade["exit_value"]       = round(exit_price * size * (1 if strategy == "stock" else 100), 2)
            # pnl_dollars stays GROSS so history remains comparable; the net
            # figure sits beside it. Promotion bars and the scorecard use net —
            # a $20 average that only clears before fees does not clear.
            commission = round_trip_commission(strategy, trade.get("legs"), size)
            trade["pnl_dollars"]      = round(pnl_dollars, 2)
            trade["commission"]       = commission
            trade["pnl_net"]          = round(pnl_dollars - commission, 2)
            trade["pnl_pct"]          = pnl_pct
            trade["pnl_per_contract"] = round(pnl_per_share * 100, 2) \
                                        if strategy != "stock" else None
            trade["outcome"]          = outcome
            trade["notes_exit"]       = notes
            trade["exit_reason"]      = exit_reason
            updated = True

            logger.info(
                f"Trade exit logged: [{trade_id}] "
                f"{trade['ticker']} {strategy} → {outcome} | "
                f"P&L: ${round(pnl_dollars, 2)} ({pnl_pct}%)"
            )
            break

        if updated:
            self._save(trades)
        else:
            logger.warning(f"Trade not found: {trade_id}")

        return updated

    @_with_journal_lock
    def mark_unscored(self, trade_id: str, reason: str) -> bool:
        """Close a position we genuinely cannot price, WITHOUT inventing a fill.

        Distinct from void_trade: a void means "this was never a real
        position." Unscored means "this really happened, and we do not know
        what it was worth." Both book no P&L, but only unscored counts as a
        real trade we failed to measure — which is information worth keeping.

        Exists because rh_sync marked such closes at the ENTRY price, producing
        a fabricated $0 "breakeven" on real money that no heuristic could
        detect, since entry == exit defeats the legacy-zero check.
        """
        trades, updated = self._load(), False
        eastern = pytz.timezone("US/Eastern")
        stamp = datetime.now(eastern).strftime("%Y-%m-%d %I:%M %p EST")
        for trade in trades:
            if trade.get("trade_id") != trade_id:
                continue
            trade["outcome"] = "unscored"
            trade["exit_date"] = stamp
            trade["exit_price"] = None
            trade["pnl_dollars"] = None
            trade["pnl_pct"] = None
            trade["pnl_per_contract"] = None
            note = (trade.get("notes_exit") or "").strip()
            trade["notes_exit"] = f"{note}\n[UNSCORED {stamp}] {reason}".strip()
            updated = True
            break
        if updated:
            self._save(trades)
            logger.warning(f"Trade marked UNSCORED: [{trade_id}] — {reason}")
        else:
            logger.warning(f"mark_unscored: trade not found: {trade_id}")
        return updated

    @_with_journal_lock
    def void_trade(self, trade_id: str, reason: str) -> bool:
        """Void a trade that was never a real fill (e.g. a synthetic stub).

        Unlike log_exit, this books NO P&L: outcome="void", pnl_dollars=None.
        Voided trades are excluded from both the open and closed sets and from
        get_summary_stats, so they never affect win rate or total P&L.
        """
        trades  = self._load()
        updated = False
        eastern = pytz.timezone("US/Eastern")
        now_est = datetime.now(eastern).strftime("%Y-%m-%d %I:%M %p EST")

        for trade in trades:
            if trade.get("trade_id") != trade_id.upper():
                continue
            trade["outcome"]     = "void"
            trade["exit_date"]   = now_est
            trade["pnl_dollars"] = None
            trade["pnl_pct"]     = None
            trade["notes_exit"]  = f"[VOID {now_est}] {reason}"
            updated = True
            logger.info(f"Trade voided: [{trade_id}] {trade.get('ticker')} — {reason}")
            break

        if updated:
            self._save(trades)
        else:
            logger.warning(f"Trade not found to void: {trade_id}")
        return updated

    @_with_journal_lock
    def update_open_position(self, trade_id: str, *, legs: list,
                             strategy: str | None = None,
                             size: float | None = None,
                             entry_price: float | None = None,
                             notes: str | None = None) -> bool:
        """Rewrite an OPEN trade's legs/strategy/size in place, keeping the same
        trade_id, book, source and entry_date. Used by the RH reconcile when the
        user EDITS a position on Robinhood (legs changed) — updating beats
        close+recreate, which minted a new id every cycle and re-armed the stop
        watchdog. max_profit/max_loss are cleared to None (unknown until the user
        confirms the new fill on /copilot) so nothing downstream trusts a stale
        risk basis. Never touches closed/void trades."""
        trades = self._load()
        updated = False
        for trade in trades:
            if trade.get("trade_id") != trade_id.upper():
                continue
            if trade.get("outcome", "open") != "open":
                logger.warning(f"update_open_position: {trade_id} not open — skip")
                break
            trade["legs"] = legs
            if strategy is not None:
                trade["strategy"] = strategy
            if size is not None:
                trade["size"] = size
            if entry_price is not None:
                trade["entry_price"] = entry_price
            trade["max_profit"] = None      # unknown after an edit; confirm on /copilot
            trade["max_loss"] = None
            if notes is not None:
                trade["notes_entry"] = notes
            updated = True
            logger.info(f"Trade updated in place: [{trade_id}] "
                        f"{trade.get('ticker')} -> {trade.get('strategy')} "
                        f"({len(legs)} legs)")
            break
        if updated:
            self._save(trades)
        else:
            logger.warning(f"update_open_position: trade not found/open: {trade_id}")
        return updated

    # ─────────────────────────────────────────
    # P&L CALCULATIONS
    # ─────────────────────────────────────────

    def _calculate_entry_value(
        self, strategy: str, entry_price: float,
        size: float, max_loss: float = None
    ) -> float:
        """What did this trade cost to enter, in DOLLARS?

        Options are always × 100 per contract. This used to match exact
        strategy names and fall through to `entry_price * size` — so
        put_debit_spread / call_debit_spread / broken_wing stored per-share
        dollars, off by 100× (docs/FORWARD_TEST_AUDIT.md A3). Nothing reads
        this field today, which is precisely why it went unnoticed for months.
        """
        if strategy == "stock":
            return round(entry_price * size, 2)
        gross = entry_price * size * 100
        # A credit received is a negative cost. Unknown structures are assumed
        # bought (positive) — only the SIGN is ambiguous, never the multiplier.
        if _pnl_convention(strategy) == "credit":
            return round(-gross, 2)
        return round(gross, 2)

    def _calculate_pnl(
        self,
        strategy:    str,
        direction:   str,
        entry:       float,
        exit_price:  float,
        size:        float,
    ) -> tuple[float, float]:
        """
        Returns (pnl_per_share, total_pnl_dollars), or (None, None) when the
        strategy has no known P&L convention.

        Returning None rather than 0 is deliberate. This method used to end in
        `return 0, 0`, so structures recorded under a variant name
        ("put_debit_spread" vs the handled "debit_spread") silently booked a $0
        "breakeven" — 32 live trades, -$718 of real P&L, hidden for months.
        See docs/FORWARD_TEST_AUDIT.md A1. Unknown now means unknown.
        """
        if strategy == "stock":
            if direction == "BULLISH":
                pps = exit_price - entry
            else:
                pps = entry - exit_price
            return pps, round(pps * size, 2)

        convention = _pnl_convention(strategy)
        if convention == "credit":
            # Sold the structure for entry_price, bought it back for exit_price.
            # Covers condors, credit spreads, and the broken-wing butterfly —
            # a BWB's close cost can go negative (it is long a far wing), so
            # this is deliberately NOT clamped.
            pps = entry - exit_price
            return pps, round(pps * size * 100, 2)

        if convention == "debit":
            # Bought the structure for entry_price, sold it for exit_price.
            pps = exit_price - entry
            return pps, round(pps * size * 100, 2)

        if convention_status(strategy) == "unclassifiable":
            # Expected and permanent — the name genuinely cannot determine the
            # convention. Refuse to score, but do not raise an alarm that has
            # no action behind it.
            logger.info(
                f"_calculate_pnl: '{strategy}' cannot be classified by name — "
                "leaving this trade unscored (correct; see "
                "_UNCLASSIFIABLE_STRATEGIES)."
            )
        else:
            logger.error(
                f"_calculate_pnl: no P&L convention for strategy '{strategy}' — "
                "refusing to fabricate $0. Add it to _CREDIT_STRATEGIES / "
                "_DEBIT_STRATEGIES in journal/trade_recorder.py."
            )
        return None, None

    def _get_cost_basis(
        self, strategy: str, entry: float,
        size: float, max_loss: float = None
    ) -> float:
        """Cost basis for P&L % calculation."""
        if strategy == "stock":
            return entry * size
        elif strategy in ("debit_spread", "single_leg"):
            return entry * size * 100
        elif strategy in ("credit_spread", "iron_condor", "broken_wing"):
            return max_loss or (entry * size * 100)
        return entry * size

    # ─────────────────────────────────────────
    # RETRIEVAL
    # ─────────────────────────────────────────

    def get_all_trades(self) -> list:
        """Return every recorded trade."""
        return self._load()

    def get_open_trades(self) -> list:
        """Return trades with outcome == 'open'."""
        return [t for t in self._load() if t.get("outcome") == "open"]

    def get_closed_trades(self) -> list:
        """Return trades with outcome in (win / loss / breakeven).

        Excludes 'open' and 'void' — voided trades were never real fills and
        carry no P&L, so they are not part of closed-trade performance.
        """
        return [t for t in self._load() if t.get("outcome") not in ("open", "void")]

    def get_trade_by_id(self, tid) -> dict | None:
        """Look up a trade by its trade_id (case-insensitive); returns None if missing."""
        for t in self._load():
            if t.get("trade_id") == tid.upper(): return t
        return None

    def get_trades_for_ticker(self, ticker: str) -> list:
        """Return all trades matching the given ticker (case-insensitive)."""
        return [t for t in self._load() if t.get("ticker") == ticker.upper()]

    def get_summary_stats(self) -> dict:
        """Aggregate win rate, P&L, and breakdown across all closed trades.

        Shadow-book ('shadow') AND candidate-book ('candidate', the dip-buy
        forward paper-test) trades are excluded from ALL aggregations here —
        they are counterfactual / research positions the user never actually
        traded and must not inflate or deflate headline win-rate / total P&L.
        (Key Decision 2: excluded from disciplined/learning stats.)

        Note: get_all_trades() is intentionally NOT filtered — the exit-manager,
        expiry-resolver, and dip-buy resolver lifecycles still need to see and
        manage shadow/candidate trades.
        """
        all_trades  = self._load()
        disciplined = [t for t in all_trades if t.get("book") not in ("shadow", "candidate")]
        closed      = [t for t in disciplined if t.get("outcome") not in ("open", "void")]
        open_t      = [t for t in disciplined if t.get("outcome") == "open"]

        if not closed:
            return {"total": len(disciplined), "open": len(open_t),
                    "closed": 0, "wins": 0, "losses": 0,
                    "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl_pct": 0.0}

        wins     = [t for t in closed if t.get("outcome") == "win"]
        pnls     = [t["pnl_dollars"] for t in closed if t.get("pnl_dollars") is not None]
        pnl_pcts = [t["pnl_pct"]     for t in closed if t.get("pnl_pct")     is not None]

        return {
            "total":       len(disciplined),
            "open":        len(open_t),
            "closed":      len(closed),
            "wins":        len(wins),
            "losses":      len(closed) - len(wins),
            "win_rate":    round((len(wins) / len(closed)) * 100, 1),
            "total_pnl":   round(sum(pnls), 2),
            "avg_pnl_pct": round(sum(pnl_pcts) / len(pnl_pcts), 2) if pnl_pcts else 0.0,
        }

    def get_trades_by(self, *, strategy: str | None = None,
                      dte_bucket: str | None = None,
                      book: str | None = None,
                      exit_reason: str | None = None,
                      include_simulated: bool = False) -> list:
        """Filter trades by optional tag values. Trades that lack a tag are
        EXCLUDED from filters that specify that tag — old (untagged) trades
        don't participate in strategy/book/dte_bucket searches.

        include_simulated=True unions in synthetic trades from simulated_trades.json.
        Default False keeps P&L / dashboard callers safe.

        No-filter call returns all trades.
        """
        rows = self.get_all_trades()
        if include_simulated:
            rows = rows + self._load_simulated()
        if strategy is not None:
            rows = [t for t in rows if t.get("strategy") == strategy]
        if dte_bucket is not None:
            rows = [t for t in rows if t.get("dte_bucket") == dte_bucket]
        if book is not None:
            rows = [t for t in rows if t.get("book") == book]
        if exit_reason is not None:
            rows = [t for t in rows if t.get("exit_reason") == exit_reason]
        return rows

    def import_from_robinhood(self) -> list:
        """Placeholder — Robinhood import added in future session."""
        logger.info("Robinhood import not yet implemented")
        return []

    # ─────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────

    @contextmanager
    def _locked(self):
        """Exclusive cross-process lock around a read-modify-write."""
        lock_path = self.trades_path + ".lock"
        os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
        fh = open(lock_path, "a+")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            finally:
                fh.close()

    def _load(self) -> list:
        """Current journal contents.

        A MISSING file is genuinely empty. An UNREADABLE one is not — this used
        to return [] for both, so one transient OSError plus the next write
        durably replaced 109 real trades with a single row, behind a warning.
        A read failure must stop the write, not license it.
        """
        if not os.path.exists(self.trades_path):
            return []
        with open(self.trades_path, "r") as f:
            return json.load(f)

    def _load_simulated(self) -> list:
        """Load synthetic trades from simulated_trades.json. Returns [] if missing.

        Simulated trades have `simulated: True` flag. Used by learning-loop
        consumers (hypothesis_engine, off_hours_learner, rolling_accuracy)
        that explicitly pass include_simulated=True.
        """
        if not os.path.exists(self.simulated_path):
            return []
        try:
            with open(self.simulated_path, "r") as f:
                rows = json.load(f)
            for r in rows:
                r.setdefault("simulated", True)
            return rows
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"TradeRecorder: failed to load {self.simulated_path}: {e}")
            return []

    def _save(self, trades: list):
        # atomic write: a freeze/crash mid-write must never corrupt the journal
        from atomic_io import atomic_write_text
        atomic_write_text(self.trades_path, json.dumps(trades, indent=2))
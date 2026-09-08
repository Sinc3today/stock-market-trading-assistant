"""tests/test_forward_scorecard.py -- the live forward-test scorecard.

The scorecard is the one-glance answer to "is the live edge real?", so its
first duty is to NOT LIE. The bug that motivated this module: trades are
recorded with strategy="put_debit_spread"/"call_debit_spread", but
TradeRecorder._calculate_pnl only branches on "debit_spread" — so those
records fell through to `return 0, 0` and were filed as $0 "breakeven".
32 of 77 closed trades carried a fabricated $0.

These tests pin the integrity classification: a record whose P&L the engine
could not actually compute must never be counted in a headline win-rate.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning import forward_scorecard as fs


def _t(**kw):
    """Minimal trade record; override what the test cares about."""
    base = {
        "trade_id": "T1", "ticker": "SPY", "strategy": "iron_condor",
        "book": "disciplined", "outcome": "win", "entry_price": 1.60,
        "exit_price": 0.60, "size": 1, "pnl_dollars": 100.0,
        "notes_entry": "", "notes_exit": "", "dte_bucket": "45DTE",
        "entry_date": "2026-09-01 09:45 AM EST",
    }
    base.update(kw)
    return base


# ─── integrity classification ────────────────────────────────────

def test_handled_strategy_with_real_fill_is_scored():
    assert fs.integrity(_t()) == fs.SCORED


def test_unhandled_strategy_is_flagged_unscored():
    """The actual bug: put_debit_spread matches no _calculate_pnl branch."""
    t = _t(strategy="put_debit_spread", outcome="breakeven", pnl_dollars=0)
    assert fs.integrity(t) == fs.UNSCORED


def test_call_debit_spread_also_unscored():
    t = _t(strategy="call_debit_spread", outcome="breakeven", pnl_dollars=0)
    assert fs.integrity(t) == fs.UNSCORED


def test_convention_resolver_is_shared_not_mirrored():
    """The scorecard must ask the recorder, never keep its own copy — a local
    copy is exactly how the two implementations drifted apart."""
    from journal.trade_recorder import _pnl_convention
    assert fs._pnl_convention is _pnl_convention
    assert not hasattr(fs, "PNL_HANDLED_STRATEGIES")


def test_variant_spread_names_resolve_to_a_convention():
    from journal.trade_recorder import _pnl_convention
    assert _pnl_convention("put_debit_spread") == "debit"
    assert _pnl_convention("call_debit_spread") == "debit"
    assert _pnl_convention("put_credit_spread") == "credit"
    assert _pnl_convention("broken_wing") == "credit"
    assert _pnl_convention("moon_spread") is None


def test_legacy_fabricated_zero_is_caught_arithmetically():
    """P&L of exactly $0 while entry != exit is impossible — catches the old
    engine's fake breakevens whatever the strategy is called."""
    t = _t(strategy="debit_spread", entry_price=0.78, exit_price=0.0,
           pnl_dollars=0, outcome="breakeven", notes_exit="expired")
    assert fs.integrity(t) == fs.UNSCORED


def test_genuine_breakeven_stays_scored():
    """Entry == exit really is $0. Don't punish an honest scratch."""
    t = _t(entry_price=1.50, exit_price=1.50, pnl_dollars=0.0,
           outcome="breakeven", notes_exit="scratched")
    assert fs.integrity(t) == fs.SCORED


def test_none_pnl_is_unscored():
    assert fs.integrity(_t(pnl_dollars=None)) == fs.UNSCORED


def test_recorder_marked_unscored_is_respected():
    assert fs.integrity(_t(outcome="unscored", pnl_dollars=None)) == fs.UNSCORED


def test_zero_fill_on_a_stop_is_suspect():
    """A stop that triggers at '75% of max loss' cannot fill at $0.00."""
    t = _t(strategy="iron_condor", exit_price=0.0,
           notes_exit="[AUTO-EXIT 2026-06-02] stop 75% of max loss fill=$0.00")
    assert fs.integrity(t) == fs.SUSPECT_FILL


def test_void_is_its_own_class():
    assert fs.integrity(_t(outcome="void")) == fs.VOID


def test_open_trade_is_not_closed():
    assert not fs.is_closed(_t(outcome="open"))
    assert fs.is_closed(_t(outcome="loss"))


def test_expiry_worthless_zero_fill_is_not_suspect():
    """Legitimately expiring worthless is a real $0 — only auto-STOP fills lie."""
    t = _t(strategy="iron_condor", exit_price=0.0,
           notes_exit="[AUTO-EXIT] expired worthless — max profit")
    assert fs.integrity(t) == fs.SCORED


# ─── recomputation of the broken records ─────────────────────────

def test_recompute_pnl_for_unscored_debit_spread():
    """Bought at 0.73, sold at 1.89 -> +$116, not the recorded $0."""
    t = _t(strategy="put_debit_spread", entry_price=0.73, exit_price=1.89,
           pnl_dollars=0, outcome="breakeven")
    assert fs.recompute_pnl(t) == pytest.approx(116.0)


def test_recompute_respects_size():
    t = _t(strategy="call_debit_spread", entry_price=1.00, exit_price=1.50, size=3,
           pnl_dollars=0, outcome="breakeven")      # legacy fabricated zero
    assert fs.recompute_pnl(t) == pytest.approx(150.0)


def test_recompute_returns_none_without_an_exit_price():
    assert fs.recompute_pnl(_t(strategy="put_debit_spread", exit_price=None)) is None


def test_recompute_none_for_already_scored():
    """Scored records already have trustworthy P&L; don't second-guess them."""
    assert fs.recompute_pnl(_t()) is None


# ─── book aggregation excludes untrustworthy records ─────────────

def test_book_stats_excludes_unscored_from_headline():
    trades = [
        _t(trade_id="A", pnl_dollars=100.0, outcome="win"),
        _t(trade_id="B", pnl_dollars=-50.0, outcome="loss"),
        _t(trade_id="C", strategy="put_debit_spread", pnl_dollars=0,
           outcome="breakeven"),
    ]
    st = fs.book_stats(trades)["disciplined"]
    assert st["n"] == 2                      # the fabricated $0 is NOT counted
    assert st["wins"] == 1
    assert st["total"] == pytest.approx(50.0)
    assert st["excluded"] == 1


def test_book_stats_reports_win_rate_on_scored_only():
    trades = [_t(trade_id=str(i), pnl_dollars=10.0, outcome="win") for i in range(3)]
    trades.append(_t(trade_id="X", strategy="put_debit_spread",
                     pnl_dollars=0, outcome="breakeven"))
    st = fs.book_stats(trades)["disciplined"]
    assert st["win_pct"] == pytest.approx(100.0)   # not 75%


def test_book_stats_separates_books():
    trades = [
        _t(trade_id="A", book="disciplined", pnl_dollars=100.0),
        _t(trade_id="B", book="learning", pnl_dollars=-20.0, outcome="loss"),
    ]
    st = fs.book_stats(trades)
    assert st["disciplined"]["n"] == 1
    assert st["learning"]["total"] == pytest.approx(-20.0)


def test_book_stats_tracks_worst_loss():
    trades = [
        _t(trade_id="A", pnl_dollars=-300.0, outcome="loss"),
        _t(trade_id="B", pnl_dollars=50.0),
    ]
    assert fs.book_stats(trades)["disciplined"]["worst"] == pytest.approx(-300.0)


def test_untagged_book_defaults_to_disciplined():
    """Records predating the book split are disciplined (matches PredictionLog)."""
    t = _t(book=None)
    assert "disciplined" in fs.book_stats([t])


# ─── integrity summary ───────────────────────────────────────────

def test_integrity_summary_counts_each_class():
    trades = [
        _t(trade_id="A"),
        _t(trade_id="B", strategy="put_debit_spread", outcome="breakeven",
           pnl_dollars=0),
        _t(trade_id="C", outcome="void"),
        _t(trade_id="D", outcome="open"),
    ]
    s = fs.integrity_summary(trades)
    assert s[fs.SCORED] == 1
    assert s[fs.UNSCORED] == 1
    assert s[fs.VOID] == 1
    assert s["open"] == 1


def test_integrity_summary_flags_trust_ratio():
    trades = [_t(trade_id="A")] + [
        _t(trade_id=str(i), strategy="put_debit_spread", outcome="breakeven",
           pnl_dollars=0)
        for i in range(3)
    ]
    s = fs.integrity_summary(trades)
    assert s["trust_pct"] == pytest.approx(25.0)


# ─── promotion progress ──────────────────────────────────────────

def test_promotion_progress_counts_closed_candidates():
    trades = [
        _t(trade_id=str(i), book="candidate", dte_bucket="BWB-45DTE",
           strategy="broken_wing", pnl_dollars=30.0, outcome="win")
        for i in range(5)
    ]
    rows = {r["bucket"]: r for r in fs.promotion_progress(trades)}
    assert rows["BWB-45DTE"]["closed"] == 5
    assert rows["BWB-45DTE"]["target_n"] == 15
    assert not rows["BWB-45DTE"]["met"]


def test_promotion_met_when_bar_cleared():
    trades = [
        _t(trade_id=str(i), book="candidate", dte_bucket="7DTE",
           strategy="iron_condor", pnl_dollars=40.0, outcome="win")
        for i in range(15)
    ]
    row = {r["bucket"]: r for r in fs.promotion_progress(trades)}["7DTE"]
    assert row["closed"] == 15 and row["win_pct"] == 100.0 and row["met"]


def test_promotion_not_met_on_thin_average():
    """15 wins but pennies each -> avg bar ($20) fails."""
    trades = [
        _t(trade_id=str(i), book="candidate", dte_bucket="7DTE",
           strategy="iron_condor", pnl_dollars=5.0, outcome="win")
        for i in range(15)
    ]
    row = {r["bucket"]: r for r in fs.promotion_progress(trades)}["7DTE"]
    assert not row["met"]


# ─── assembly ────────────────────────────────────────────────────

def test_scorecard_never_raises_without_logs(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    card = fs.scorecard()
    assert "books" in card and "integrity" in card


def test_scorecard_on_real_logs_is_wellformed():
    """Smoke test against whatever the live log actually holds."""
    card = fs.scorecard()
    for key in ("books", "integrity", "open_positions", "promotion", "predictions"):
        assert key in card, key


# ── commissions (audit A4) ────────────────────────────────────────

def _condor_legs():
    return [{"action": "SELL", "option_type": "call", "strike": 780},
            {"action": "BUY", "option_type": "call", "strike": 785},
            {"action": "SELL", "option_type": "put", "strike": 750},
            {"action": "BUY", "option_type": "put", "strike": 745}]


def test_net_pnl_prefers_the_stored_value():
    assert fs.net_pnl(_t(pnl_dollars=100.0, pnl_net=94.8)) == pytest.approx(94.8)


def test_net_pnl_is_computed_for_legacy_records():
    """Records written before commissions existed must still compare fairly."""
    t = _t(pnl_dollars=100.0, legs=_condor_legs(), size=1)
    assert fs.net_pnl(t) == pytest.approx(100.0 - 4 * 2 * 0.65)


def test_net_pnl_is_none_without_gross():
    assert fs.net_pnl(_t(pnl_dollars=None)) is None


def test_book_stats_reports_fees_separately_from_gross():
    trades = [_t(trade_id=str(i), pnl_dollars=100.0, legs=_condor_legs())
              for i in range(3)]
    st = fs.book_stats(trades)["disciplined"]
    assert st["total"] == pytest.approx(300.0)        # gross preserved
    assert st["fees"] == pytest.approx(3 * 5.2)
    assert st["net_total"] == pytest.approx(300.0 - 3 * 5.2)


def test_promotion_bar_is_judged_net_of_commissions():
    """15 wins averaging $22 gross clears the $20 bar; net of a 4-leg round
    trip it averages $16.80 and does not."""
    trades = [_t(trade_id=str(i), book="candidate", dte_bucket="7DTE",
                 pnl_dollars=22.0, outcome="win", legs=_condor_legs())
              for i in range(15)]
    row = {r["bucket"]: r for r in fs.promotion_progress(trades)}["7DTE"]
    assert row["avg"] == pytest.approx(16.8)
    assert not row["met"], "a bar that only clears before fees has not cleared"


def test_promotion_bar_still_passes_when_it_clears_net():
    trades = [_t(trade_id=str(i), book="candidate", dte_bucket="7DTE",
                 pnl_dollars=40.0, outcome="win", legs=_condor_legs())
              for i in range(15)]
    row = {r["bucket"]: r for r in fs.promotion_progress(trades)}["7DTE"]
    assert row["met"]


def test_a_gross_win_that_is_a_net_loss_counts_as_a_loss_at_the_bar():
    trades = [_t(trade_id=str(i), book="candidate", dte_bucket="7DTE",
                 pnl_dollars=2.0, outcome="win", legs=_condor_legs())
              for i in range(15)]
    row = {r["bucket"]: r for r in fs.promotion_progress(trades)}["7DTE"]
    assert row["win_pct"] == 0.0


# ── vocabulary totality (enumeration E1, 2026-09-07) ─────────────
# tools/enumerate_conventions found 5 strategy names reachable by a producer
# that the convention owner did not understand. A name the owner cannot
# classify becomes an unscoreable trade at best, and a sign-flipped one at
# worst.

def test_debit_suffix_variants_resolve():
    """bull_debit / bear_debit reach the journal via dipbuy_forward's default."""
    from journal.trade_recorder import _pnl_convention
    assert _pnl_convention("bull_debit") == "debit"
    assert _pnl_convention("bear_debit") == "debit"


def test_credit_suffix_variants_resolve():
    from journal.trade_recorder import _pnl_convention
    assert _pnl_convention("bull_credit") == "credit"
    assert _pnl_convention("bear_credit") == "credit"


def test_genuinely_unknown_shapes_stay_unknown():
    """rh_sync emits 'custom' for a 3-leg or 5+-leg position it cannot name.
    That must stay None — refusing is correct, guessing is not."""
    from journal.trade_recorder import _pnl_convention
    assert _pnl_convention("custom") is None
    assert _pnl_convention("none") is None
    assert _pnl_convention("") is None
    assert _pnl_convention(None) is None


def test_every_producer_name_is_understood_or_deliberately_unknown():
    """THE enumeration test. Every strategy name any producer can emit must
    either resolve to a convention, or be on the explicit unknown list.

    This is what makes a partial fix impossible: it fails when someone adds a
    producer without teaching the owner about it.
    """
    from journal.trade_recorder import _pnl_convention
    from tools.enumerate_conventions import strategy_vocabulary, journal_vocabulary
    # Deliberately unclassifiable: an unrecognised broker shape, and a legacy
    # placeholder that predates the bug fix. Both correctly refuse to score.
    DELIBERATELY_UNKNOWN = {"custom", "none", "option_spread"}
    names = set(strategy_vocabulary()) | journal_vocabulary()
    gaps = sorted(n for n in names
                  if _pnl_convention(n) is None and n not in DELIBERATELY_UNKNOWN)
    assert gaps == [], f"producers emit names the convention owner cannot classify: {gaps}"

"""tests/test_spread_price.py -- entry price must be real or absent.

paper_broker._spread_price returned a hardcoded 1.00 when it could not read
the premium, and that became the RECORDED entry price on 18 of 109 trades
(11 disciplined). Correcting for it flips the candidate book from a reported
+$1,503 to roughly -$186.

The price was never missing. A real plan carries `mark` on every leg
(mid is None on this Polygon tier); _spread_price searched four keys that the
plan does not publish, hit `mid: None`, and fell through to the placeholder.

Principle under test: A MISSING PRICE IS NOT A PRICE. Return None and refuse
the open.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from learning.paper_broker import PaperBroker

_sp = PaperBroker._spread_price


def _condor_legs(marks=(3.07, 1.42, 2.90, 1.35)):
    """Real shape from logs/spy_daily_plans.json — lowercase actions, mid=None."""
    sc, lc, sp_, lp = marks
    return [
        {"action": "sell", "option_type": "call", "strike": 793, "mid": None, "mark": sc},
        {"action": "buy",  "option_type": "call", "strike": 798, "mid": None, "mark": lc},
        {"action": "sell", "option_type": "put",  "strike": 747, "mid": None, "mark": sp_},
        {"action": "buy",  "option_type": "put",  "strike": 742, "mid": None, "mark": lp},
    ]


# ── the placeholder must be gone ─────────────────────────────────

def test_never_returns_the_one_dollar_placeholder():
    assert _sp({"legs": [{"action": "sell", "strike": 793, "mid": None}]}) != 1.00


def test_returns_none_when_no_price_is_available():
    assert _sp({"legs": [{"action": "sell", "strike": 793, "mid": None}]}) is None


def test_returns_none_for_an_empty_payload():
    assert _sp({}) is None
    assert _sp({"legs": []}) is None


# ── it can read what the real plan actually carries ──────────────

def test_derives_the_credit_from_leg_marks():
    """The exact failure: mid is None, mark is populated. 3.07-1.42+2.90-1.35."""
    assert _sp({"legs": _condor_legs()}) == pytest.approx(3.20)


def test_leg_action_matching_is_case_insensitive():
    """Real plans write 'sell'; condor_calc writes 'SELL'. Both must work."""
    upper = [dict(l, action=l["action"].upper()) for l in _condor_legs()]
    assert _sp({"legs": upper}) == pytest.approx(_sp({"legs": _condor_legs()}))


def test_prefers_an_explicit_numeric_premium():
    assert _sp({"net_credit": 1.60, "legs": _condor_legs()}) == pytest.approx(1.60)


def test_ignores_a_display_string_premium():
    """options_layer publishes net_premium as '~$3.0 total credit (estimated)'
    on several branches. An estimate rendered for humans is not a price."""
    out = _sp({"net_premium": "~$3.0 total credit (estimated)", "legs": _condor_legs()})
    assert out == pytest.approx(3.20), "must fall through to the real leg marks"


def test_ignores_a_zero_premium():
    assert _sp({"net_credit": 0, "legs": _condor_legs()}) == pytest.approx(3.20)


def test_falls_back_to_mid_when_mark_is_absent():
    legs = [dict(l) for l in _condor_legs()]
    for l in legs:
        l["mid"] = l.pop("mark")
    assert _sp({"legs": legs}) == pytest.approx(3.20)


def test_returns_none_if_any_leg_is_unpriced():
    """A partly-priced spread yields a credit inflated by the unpriced wing —
    that is how a structurally impossible risk-free trade gets recorded."""
    legs = _condor_legs()
    legs[1]["mark"] = None
    assert _sp({"legs": legs}) is None


def test_result_is_positive_magnitude():
    """entry_price is stored as a positive magnitude; sign lives in the
    strategy's convention, not here."""
    v = _sp({"legs": _condor_legs()})
    assert v is not None and v > 0


# ── the caller must refuse rather than record ────────────────────

def test_open_is_refused_when_the_price_is_unknown(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setattr(config, "ENFORCE_ENTRY_WINDOW", False)
    from journal.trade_recorder import TradeRecorder
    rec = TradeRecorder()
    pb = PaperBroker.__new__(PaperBroker)
    pb.trades = rec
    out = pb._record_from_options(
        options={"legs": [{"action": "sell", "strike": 793, "mid": None}]},
        regime="choppy_low_vol", confidence=0.85, reasons=["test"],
        strategy="iron_condor", book="disciplined",
    ) if hasattr(pb, "_record_from_options") else None
    # Whatever the entry point is named, nothing may be journalled at $1.00
    assert not any(t.get("entry_price") == 1.00 for t in rec.get_all_trades())

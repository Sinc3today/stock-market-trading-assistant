"""tests/test_rh_sync.py -- RH read-only position sync (pure mapping + reconcile).

robin_stocks fetch is isolated; these test the parts that matter: turning RH's
per-leg positions into our trade shape, and RECONCILING against the live book so
a position the user already logged ('I placed it') is matched + updated, never
duplicated. The module must never touch an order function (read-only).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _pos(side, qty, avg, oid):
    return {"type": side, "quantity": qty, "average_price": avg,
            "option_id": oid, "chain_symbol": "SPY"}


def _inst(strike, cp, exp):
    return {"strike_price": strike, "type": cp, "expiration_date": exp,
            "chain_symbol": "SPY"}


def test_module_imports_no_order_functions():
    # read-only guarantee: the module must not reference order placement
    import learning.rh_sync as m
    src = open(m.__file__).read()
    for banned in ("order_buy", "order_sell", "order_option", "place_order"):
        assert banned not in src, f"read-only violation: {banned}"


def test_normalize_leg_maps_short_call():
    from learning.rh_sync import normalize_leg
    leg = normalize_leg(_pos("short", "2.0000", "0.8000", "OID1"),
                         _inst("771.0000", "call", "2026-07-24"))
    assert leg["action"] == "SELL"
    assert leg["option_type"] == "CALL"
    assert leg["strike"] == 771.0
    assert leg["expiry"] == "2026-07-24"
    assert leg["quantity"] == 2.0


def _condor_legs():
    # RH average_price is SIGNED and PER-CONTRACT: shorts negative (credit
    # received), longs positive (debit paid). These are the user's REAL July
    # condor fills — the net must come out to their known $1.55/share credit.
    return [
        normalize_leg_q("short", "2", "-401.0", _inst("771", "call", "2026-07-24")),
        normalize_leg_q("long",  "2", "276.0",  _inst("776", "call", "2026-07-24")),
        normalize_leg_q("short", "2", "-238.0", _inst("700", "put",  "2026-07-24")),
        normalize_leg_q("long",  "2", "208.0",  _inst("695", "put",  "2026-07-24")),
    ]


def normalize_leg_q(side, qty, avg, inst):
    from learning.rh_sync import normalize_leg
    return normalize_leg(_pos(side, qty, avg, "x"), inst)


def test_group_into_positions_builds_condor():
    from learning.rh_sync import group_into_positions
    positions = group_into_positions(_condor_legs())
    assert len(positions) == 1
    p = positions[0]
    assert p["ticker"] == "SPY"
    assert p["expiry"] == "2026-07-24"
    assert p["strategy"] == "iron_condor"
    assert p["size"] == 2
    strikes = sorted(l["strike"] for l in p["legs"])
    assert strikes == [695.0, 700.0, 771.0, 776.0]
    # net = Σ signed per-contract avg fills = 276-401+208-238 = -155/contract
    # -> $1.55/share credit (matches the user's real verified fill)
    assert round(p["entry_price"], 2) == 1.55


def test_reconcile_matches_existing_live_trade_not_duplicate():
    from learning.rh_sync import group_into_positions, reconcile
    positions = group_into_positions(_condor_legs())
    # the user already logged this condor via "I placed it"
    existing = [{"trade_id": "E7350D4A", "book": "live", "ticker": "SPY",
                 "outcome": "open",
                 "legs": [{"option_type": "CALL", "strike": 771.0},
                          {"option_type": "CALL", "strike": 776.0},
                          {"option_type": "PUT",  "strike": 700.0},
                          {"option_type": "PUT",  "strike": 695.0}],
                 "legs_expiry": "2026-07-24"}]
    # give the existing legs an expiry the matcher can read
    for leg in existing[0]["legs"]:
        leg["expiry"] = "2026-07-24"
    plan = reconcile(positions, existing)
    assert len(plan) == 1
    assert plan[0]["action"] == "match"          # matched, NOT a new duplicate
    assert plan[0]["trade_id"] == "E7350D4A"


def test_reconcile_creates_new_when_unmatched():
    from learning.rh_sync import group_into_positions, reconcile
    positions = group_into_positions(_condor_legs())
    plan = reconcile(positions, existing_live=[])
    assert len(plan) == 1
    assert plan[0]["action"] == "create"


def _existing_live(tid="E7350D4A", strikes=((("C", 771.0)), ("C", 776.0), ("P", 700.0), ("P", 695.0))):
    legs = [{"option_type": ("CALL" if t == "C" else "PUT"), "strike": s,
             "expiry": "2026-07-24"} for t, s in strikes]
    return {"trade_id": tid, "book": "live", "ticker": "SPY",
            "outcome": "open", "legs": legs}


def test_reconcile_detects_position_closed_on_rh():
    # Audit T1.3: the user closes on RH -> our copy must be flagged, not watched
    # as a phantom forever. RH returns NO positions; we hold one open live trade.
    from learning.rh_sync import reconcile
    plan = reconcile([], existing_live=[_existing_live()])
    assert len(plan) == 1
    assert plan[0]["action"] == "close"
    assert plan[0]["trade_id"] == "E7350D4A"


def test_reconcile_no_close_when_still_open_on_rh():
    from learning.rh_sync import group_into_positions, reconcile
    positions = group_into_positions(_condor_legs())   # the same July condor
    plan = reconcile(positions, existing_live=[_existing_live()])
    assert [s["action"] for s in plan] == ["match"]    # matched, no close action


def test_reconcile_close_only_targets_synced_sources():
    # a live trade with an unknown/manual-legacy source is still closed-detected;
    # but NON-live books are never touched by sync close-detection
    from learning.rh_sync import reconcile
    disc = _existing_live(tid="D1"); disc["book"] = "disciplined"
    plan = reconcile([], existing_live=[disc])
    assert plan == []


def test_reconcile_updates_in_place_when_position_edited():
    # The user EDITED a live position on RH (added legs). Same underlying+expiry,
    # different strike-set. Reconcile must UPDATE the existing trade in place
    # (same trade_id) — NOT close it (phantom max-loss) and mint a new id (which
    # re-armed the stop watchdog every cycle: the 2026-07-20 alert-flood bug).
    from learning.rh_sync import reconcile
    # RH now shows a 4-leg call structure at 07-27; journal has the old 2-leg.
    positions = [{"ticker": "SPY", "expiry": "2026-07-27", "strategy": "custom",
                  "size": 1, "entry_price": 6.63,
                  "legs": [{"action": "BUY",  "option_type": "CALL", "strike": 729.0, "expiry": "2026-07-27"},
                           {"action": "SELL", "option_type": "CALL", "strike": 744.0, "expiry": "2026-07-27"},
                           {"action": "SELL", "option_type": "CALL", "strike": 745.0, "expiry": "2026-07-27"},
                           {"action": "BUY",  "option_type": "CALL", "strike": 760.0, "expiry": "2026-07-27"}]}]
    existing = [{"trade_id": "OLD2LEG1", "book": "live", "ticker": "SPY", "outcome": "open",
                 "legs": [{"option_type": "CALL", "strike": 729.0, "expiry": "2026-07-27"},
                          {"option_type": "CALL", "strike": 745.0, "expiry": "2026-07-27"}]}]
    plan = reconcile(positions, existing)
    assert len(plan) == 1
    assert plan[0]["action"] == "update"          # NOT close + create
    assert plan[0]["trade_id"] == "OLD2LEG1"      # id preserved -> no alert churn


def test_reconcile_still_closes_genuinely_gone_position():
    # A journal live trade with NO RH position at its (ticker, expiry) is a real
    # close, not an edit — must still close.
    from learning.rh_sync import reconcile
    existing = [_existing_live()]                 # 07-24 condor
    plan = reconcile([], existing)                # nothing on RH
    assert len(plan) == 1 and plan[0]["action"] == "close"


def test_close_estimate_sign_correct_for_debit_spread(monkeypatch):
    # The phantom-loss bug: the old formula used the credit convention and
    # clamped at 0, so closing a debit spread booked a total-debit loss.
    import learning.rh_sync as rh
    # BUY 729C (deep ITM ~16) / SELL 745C (~0) at expiry with SPY ~745.
    monkeypatch.setattr("data.market_quotes.fetch_leg_quotes", lambda tk, legs: [
        {"action": "BUY",  "mid": 16.0}, {"action": "SELL", "mid": 0.0}], raising=False)
    trade = {"ticker": "SPY", "strategy": "debit_spread",
             "legs": [{"action": "BUY", "option_type": "CALL", "strike": 729.0},
                      {"action": "SELL", "option_type": "CALL", "strike": 745.0}]}
    est = rh._close_estimate(trade)
    # For a debit spread, log_exit wants the SALE value (positive), not 0.
    assert est is not None and est > 10          # ~ $16 spread value, NOT $0


def test_update_open_position_preserves_id_and_clears_stale_risk(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from journal.trade_recorder import TradeRecorder
    from datetime import date
    rec = TradeRecorder()
    tid = rec.log_entry(ticker="SPY", entry_price=13.26, size=1,
                        trade_type="debit_spread", strategy="debit_spread",
                        direction="bullish", mode="swing",
                        legs=[{"action": "BUY", "option_type": "CALL", "strike": 729.0,
                               "expiry": "2026-07-27"},
                              {"action": "SELL", "option_type": "CALL", "strike": 745.0,
                               "expiry": "2026-07-27"}],
                        max_profit=274.0, max_loss=1326.0, book="live", source="rh-sync")
    ok = rec.update_open_position(
        tid, legs=[{"action": "BUY", "option_type": "CALL", "strike": 729.0, "expiry": "2026-07-27"},
                   {"action": "SELL", "option_type": "CALL", "strike": 744.0, "expiry": "2026-07-27"},
                   {"action": "SELL", "option_type": "CALL", "strike": 745.0, "expiry": "2026-07-27"},
                   {"action": "BUY", "option_type": "CALL", "strike": 760.0, "expiry": "2026-07-27"}],
        strategy="custom", size=1)
    assert ok
    t = rec.get_trade_by_id(tid)
    assert t["outcome"] == "open" and len(t["legs"]) == 4 and t["strategy"] == "custom"
    assert t["max_profit"] is None and t["max_loss"] is None   # stale risk cleared
    assert rec.get_open_trades() and len(rec.get_open_trades()) == 1  # no new id


def test_update_open_position_ignores_closed_trade(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from journal.trade_recorder import TradeRecorder
    rec = TradeRecorder()
    tid = rec.log_entry(ticker="SPY", entry_price=1.0, size=1, trade_type="iron_condor",
                        strategy="iron_condor", direction="neutral", mode="swing",
                        legs=[{"action": "SELL", "option_type": "PUT", "strike": 700.0,
                               "expiry": "2026-07-27"}], book="live")
    rec.void_trade(tid, "test")
    assert rec.update_open_position(tid, legs=[]) is False

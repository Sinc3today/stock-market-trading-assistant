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
    return [
        normalize_leg_q("short", "2", "0.80", _inst("771", "call", "2026-07-24")),
        normalize_leg_q("long",  "2", "0.45", _inst("776", "call", "2026-07-24")),
        normalize_leg_q("short", "2", "3.90", _inst("700", "put",  "2026-07-24")),
        normalize_leg_q("long",  "2", "3.32", _inst("695", "put",  "2026-07-24")),
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
    # net entry from leg avg_prices: shorts(0.80+3.90) - longs(0.45+3.32) = 0.93
    assert round(p["entry_price"], 2) == 0.93


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

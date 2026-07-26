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


def test_session_status_thresholds(tmp_path, monkeypatch):
    import config
    from datetime import datetime, timedelta
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    import learning.rh_sync as rh
    # no timestamp yet -> unknown
    assert rh.session_status() == "unknown"
    # valid: expiry 3 days out
    rh._write_session_expiry(3 * 86400)
    assert rh.session_status() == "valid"
    now = datetime.now()
    # expiring soon: < 24h left
    exp_soon = (now + timedelta(hours=5)).isoformat()
    open(rh._session_expiry_path(), "w").write(exp_soon)
    assert rh.session_status() == "expiring_soon"
    # expired: in the past
    open(rh._session_expiry_path(), "w").write((now - timedelta(hours=1)).isoformat())
    assert rh.session_status() == "expired"


def test_load_session_fails_fast_when_expired(tmp_path, monkeypatch):
    # Expired-by-timestamp must raise WITHOUT calling robin_stocks (no hammering
    # a dead login every cycle — the whole point of the proactive design).
    import config
    from datetime import datetime, timedelta
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    import learning.rh_sync as rh
    open(rh._session_expiry_path(), "w").write((datetime.now() - timedelta(hours=1)).isoformat())

    import robin_stocks.robinhood as r
    def _boom(*a, **k):
        raise AssertionError("must not attempt r.login on an expired session")
    monkeypatch.setattr(r, "login", _boom)
    import pytest
    with pytest.raises(RuntimeError):
        rh._load_session()


def test_job_rh_sync_warns_before_expiry(tmp_path, monkeypatch):
    import config
    from datetime import datetime, timedelta
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setattr(config, "RH_SYNC_ENABLED", True)
    monkeypatch.setattr(config, "is_trading_day", lambda *a, **k: True)
    import learning.rh_sync as rh
    open(rh._session_expiry_path(), "w").write((datetime.now() + timedelta(hours=5)).isoformat())
    # sync must NOT be called with a dead session? here it's still valid -> it will
    # be called; stub it so the test stays offline.
    monkeypatch.setattr(rh, "sync", lambda dry_run=False: [], raising=False)
    from learning import scheduler as sched
    sched._rh_expiring_soon_pushed[0] = None
    pushes = []
    sched.job_rh_sync(alert_fn=lambda **kw: pushes.append(kw["title"]))
    assert any("expires soon" in t for t in pushes)


def test_job_rh_sync_skips_and_notifies_when_expired(tmp_path, monkeypatch):
    import config
    from datetime import datetime, timedelta
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setattr(config, "RH_SYNC_ENABLED", True)
    monkeypatch.setattr(config, "is_trading_day", lambda *a, **k: True)
    import learning.rh_sync as rh
    open(rh._session_expiry_path(), "w").write((datetime.now() - timedelta(hours=1)).isoformat())
    def _boom(*a, **k):
        raise AssertionError("must not run sync on an expired session")
    monkeypatch.setattr(rh, "sync", _boom, raising=False)
    from learning import scheduler as sched
    sched._rh_expiry_pushed[0] = None
    pushes = []
    sched.job_rh_sync(alert_fn=lambda **kw: pushes.append(kw["title"]))
    assert any("expired" in t.lower() for t in pushes)


def test_login_headless_requires_env_creds(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.delenv("RH_USERNAME", raising=False)
    monkeypatch.delenv("RH_PASSWORD", raising=False)
    import learning.rh_sync as rh
    out = rh.login_headless()
    assert out["state"] == "error" and "RH_USERNAME" in out["detail"]


def test_login_headless_success_uses_device_approval(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setenv("RH_USERNAME", "u@example.com")
    monkeypatch.setenv("RH_PASSWORD", "secret")
    import robin_stocks.robinhood as r
    calls = {}
    def _login(**kw):
        calls.update(kw)
        return {"access_token": "x"}
    monkeypatch.setattr(r, "login", _login)
    import learning.rh_sync as rh
    out = rh.login_headless()
    assert out["state"] == "ok"
    assert calls["mfa_code"] is None                 # device approval, not typed
    assert calls["store_session"] is True
    assert calls["expiresIn"] == rh.SESSION_DAYS * 86400
    # session now marked valid + success stamped
    assert rh.session_status() == "valid"


def test_rh_reauth_route_renders_and_starts(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from fastapi.testclient import TestClient
    import alerts.web_app as wa
    import learning.rh_sync as rh
    rh._REAUTH.update(state="idle", detail="", ts=None)
    client = TestClient(wa.app)
    # page renders
    r = client.get("/rh-reauth")
    assert r.status_code == 200 and "Re-authenticate Robinhood" in r.text
    # POST starts a background login; stub it so nothing hits the network
    started = {"n": 0}
    monkeypatch.setattr(rh, "login_headless",
                        lambda: started.update(n=started["n"] + 1))
    r2 = client.post("/rh-reauth", follow_redirects=False)
    assert r2.status_code == 303 and r2.headers["location"] == "/rh-reauth"

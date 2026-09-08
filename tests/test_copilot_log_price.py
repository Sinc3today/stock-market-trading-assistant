"""tests/test_copilot_log_price.py -- a real fill must never be logged at $0.

This is the path the user types their actual Robinhood fill into. Two defects
met in the middle:

  _f() parsed "1.55" but returned None for "$1.55" and "1,55" — a dollar sign
  is a completely natural thing to type into a price field.
  Then `_f(form, "entry_price") or 0.0` turned that None into a RECORDED
  entry price of $0.00 on a real-money position.

The form carries no `required` attribute and there is no server-side check, so
nothing anywhere stopped it.

Same family as everything else in the 2026-09-07 audit: a missing price is not
a price.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from alerts.copilot_log import _f, build_live_trade_kwargs as parse_form


# ── the parser must accept what a human types ────────────────────

@pytest.mark.parametrize("raw,want", [
    ("1.55", 1.55),
    ("$1.55", 1.55),
    (" 1.55 ", 1.55),
    ("$ 1.55", 1.55),
    ("1,55", 1.55),          # comma decimal separator
    ("1,550.25", 1550.25),   # thousands separator
    ("-0.45", -0.45),
    ("$-0.45", -0.45),
    (1.55, 1.55),
])
def test_parses_realistic_price_input(raw, want):
    assert _f({"entry_price": raw}, "entry_price") == pytest.approx(want)


@pytest.mark.parametrize("raw", ["", "   ", None, "abc", "$", "--"])
def test_unparseable_input_is_none_not_zero(raw):
    assert _f({"entry_price": raw}, "entry_price") is None


def test_a_dollar_sign_no_longer_costs_the_whole_price():
    """The specific live risk: '$1.55' used to become a recorded $0.00."""
    assert _f({"entry_price": "$1.55"}, "entry_price") == pytest.approx(1.55)


# ── a blank price must be refused, not recorded ──────────────────

def _form(**kw):
    # Real form keys: bc/sc/bp/sp = buy-call / sell-call / buy-put / sell-put.
    base = {"ticker": "SPY", "contracts": "1",
            "entry_price": "1.55", "expiry": "2026-10-16",
            "sc": "805", "bc": "810", "sp": "738", "bp": "733"}
    base.update(kw)
    base.pop("strategy", None)
    return base


def test_a_blank_entry_price_is_rejected():
    with pytest.raises(ValueError, match="(?i)entry price"):
        parse_form(_form(entry_price=""))


def test_an_unparseable_entry_price_is_rejected():
    with pytest.raises(ValueError, match="(?i)entry price"):
        parse_form(_form(entry_price="about a buck fifty"))


def test_a_zero_entry_price_is_rejected():
    """A real options fill is never free."""
    with pytest.raises(ValueError, match="(?i)entry price"):
        parse_form(_form(entry_price="0"))


def test_a_valid_form_still_parses():
    out = parse_form(_form())
    assert out["entry_price"] == pytest.approx(1.55)
    assert out["ticker"] == "SPY"


def test_no_path_can_return_a_zero_entry_price():
    """Belt and braces: the condor path AND the butterfly path."""
    condor = parse_form(_form())
    assert condor["entry_price"], "condor path produced a falsy entry price"
    fly = parse_form({"ticker": "SPY", "contracts": "1", "entry_price": "$2.10",
                      "expiry": "2026-10-16",
                      "fly_lo": "760", "fly_mid": "770", "fly_hi": "780"})
    assert fly["entry_price"] == pytest.approx(2.10)
    assert fly["strategy"] == "butterfly"


def test_the_butterfly_path_also_refuses_a_blank_price():
    with pytest.raises(ValueError, match="(?i)entry price"):
        parse_form({"ticker": "SPY", "contracts": "1", "entry_price": "",
                    "expiry": "2026-10-16",
                    "fly_lo": "760", "fly_mid": "770", "fly_hi": "780"})


# ── manually logged structures must be scoreable ─────────────────

def test_butterfly_has_a_pnl_convention():
    """A fly logged through the copilot used to be unscoreable, and
    web_app's display treated it as a CREDIT — showing +$110 profit on a
    total loss."""
    from journal.trade_recorder import _pnl_convention
    assert _pnl_convention("butterfly") == "debit"


def test_every_strategy_the_form_can_emit_is_scoreable():
    from journal.trade_recorder import _pnl_convention
    import inspect
    import alerts.copilot_log as cl
    src = inspect.getsource(cl)
    import re
    emitted = set(re.findall(r'strategy["\']?\s*[:=]\s*["\'](\w+)["\']', src))
    emitted |= set(re.findall(r'strategy,\s*direction\s*=\s*["\'](\w+)["\']', src))
    unknown = sorted(s for s in emitted
                     if _pnl_convention(s) is None and s not in {"custom"})
    assert unknown == [], f"copilot can log unscoreable strategies: {unknown}"


# ── the web path must show an error, not record a $0 trade ───────

def test_web_form_marks_the_price_required():
    from fastapi.testclient import TestClient
    from alerts.web_app import app
    html = TestClient(app).get("/copilot/log").text
    assert 'name="entry_price"' in html
    i = html.index('name="entry_price"')
    assert "required" in html[i:i + 200], "price field must be required"


def test_web_post_with_a_blank_price_records_nothing(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    from fastapi.testclient import TestClient
    from alerts.web_app import app
    from journal.trade_recorder import TradeRecorder

    r = TestClient(app).post("/copilot/log", data={
        "ticker": "SPY", "expiry": "2026-10-16", "entry_price": "",
        "contracts": "1", "sc": "805", "bc": "810", "sp": "738", "bp": "733",
    })
    assert r.status_code == 200, "must re-render the form, not 500"
    assert "entry price" in r.text.lower()
    assert TradeRecorder().get_all_trades() == [], "nothing may be journalled"

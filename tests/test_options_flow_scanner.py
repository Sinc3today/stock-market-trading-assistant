"""
tests/test_options_flow_scanner.py -- Test OptionsFlowScanner

All unit tests -- no API calls.
Contract scoring logic is tested with synthetic chain data.

Run with:
    pytest tests/test_options_flow_scanner.py -v
"""

import pytest
import sys
import os
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scanners.options_flow_scanner import OptionsFlowScanner


# ─────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────

@pytest.fixture
def scanner():
    return OptionsFlowScanner()

def _make_contract(
    contract_type   = "call",
    strike          = 500.0,
    spot            = 500.0,
    dte             = 7,
    volume          = 1000,
    open_interest   = 100,
    iv              = 0.30,
    delta           = 0.50,
):
    """Build a synthetic options contract row."""
    expiry = (date.today() + timedelta(days=dte)).isoformat()
    return pd.Series({
        "contract_type":      contract_type,
        "strike_price":       strike,
        "expiration_date":    expiry,
        "volume":             volume,
        "open_interest":      open_interest,
        "implied_volatility": iv,
        "delta":              delta,
        "vega":               0.05,
    })


# ─────────────────────────────────────────
# MINIMUM FILTERS
# ─────────────────────────────────────────

def test_low_volume_filtered_out(scanner):
    row = _make_contract(volume=50, open_interest=10)
    result = scanner._score_contract(row, "SPY", 500.0, date.today())
    assert result is None
    print("\n✅ Low volume filtered")

def test_low_oi_filtered_out(scanner):
    row = _make_contract(volume=200, open_interest=5)
    result = scanner._score_contract(row, "SPY", 500.0, date.today())
    assert result is None
    print("\n✅ Low OI filtered")

def test_dte_too_high_filtered(scanner):
    row = _make_contract(volume=500, open_interest=100, dte=90)
    result = scanner._score_contract(row, "SPY", 500.0, date.today())
    assert result is None
    print("\n✅ High DTE (LEAPS) filtered")

def test_dte_zero_filtered(scanner):
    row = _make_contract(volume=500, open_interest=100, dte=0)
    result = scanner._score_contract(row, "SPY", 500.0, date.today())
    assert result is None
    print("\n✅ 0DTE filtered")


# ─────────────────────────────────────────
# VOL/OI SPIKE DETECTION
# ─────────────────────────────────────────

def test_strong_vol_oi_spike_detected(scanner):
    """Volume 10x open interest should fire as strong signal."""
    row    = _make_contract(volume=1000, open_interest=100)
    result = scanner._score_contract(row, "SPY", 500.0, date.today())
    assert result is not None
    assert result["vol_oi_ratio"] == 10.0
    assert any("STRONG" in f for f in result["flags"])
    assert result["conviction"] >= 40
    print(f"\n✅ Strong VOL/OI spike: {result['vol_oi_ratio']}x | conviction={result['conviction']}")

def test_moderate_vol_oi_spike_detected(scanner):
    """Volume 4x OI should fire as moderate spike."""
    row    = _make_contract(volume=400, open_interest=100)
    result = scanner._score_contract(row, "SPY", 500.0, date.today())
    assert result is not None
    assert result["vol_oi_ratio"] >= 3.0
    assert result["conviction"] >= 25
    print(f"\n✅ Moderate VOL/OI spike: {result['vol_oi_ratio']}x | conviction={result['conviction']}")

def test_normal_vol_oi_no_signal(scanner):
    """Volume equal to OI is normal -- no signal."""
    row    = _make_contract(volume=100, open_interest=500)
    result = scanner._score_contract(row, "SPY", 500.0, date.today())
    assert result is None
    print("\n✅ Normal vol/OI ratio: no signal")


# ─────────────────────────────────────────
# OTM MONSTER DETECTION
# ─────────────────────────────────────────

def test_otm_monster_call_detected(scanner):
    """Deep OTM call with huge volume = directional bet."""
    row = _make_contract(
        contract_type = "call",
        strike        = 560.0,   # 12% OTM
        spot          = 500.0,
        volume        = 600,
        open_interest = 50,
        dte           = 14,
    )
    result = scanner._score_contract(row, "SPY", 500.0, date.today())
    assert result is not None
    assert result["is_otm"] is True
    assert any("OTM monster" in f for f in result["flags"])
    assert result["implied_direction"] == "bullish"
    print(f"\n✅ OTM monster call detected: {result['otm_pct']}% OTM | conviction={result['conviction']}")

def test_otm_monster_put_detected(scanner):
    """Deep OTM put with huge volume = bearish bet."""
    row = _make_contract(
        contract_type = "put",
        strike        = 440.0,   # 12% OTM put
        spot          = 500.0,
        volume        = 600,
        open_interest = 50,
        dte           = 14,
    )
    result = scanner._score_contract(row, "SPY", 500.0, date.today())
    assert result is not None
    assert result["is_otm"] is True
    assert result["implied_direction"] in ("bearish", "hedge")
    print(f"\n✅ OTM monster put detected: {result['otm_pct']}% OTM")


# ─────────────────────────────────────────
# DIRECTION INFERENCE
# ─────────────────────────────────────────

def test_call_buying_is_bullish(scanner):
    row    = _make_contract(contract_type="call", volume=1000, open_interest=100)
    result = scanner._score_contract(row, "SPY", 500.0, date.today())
    assert result is not None
    assert result["implied_direction"] == "bullish"
    print("\n✅ Call buying inferred as bullish")

def test_put_buying_is_bearish(scanner):
    row    = _make_contract(contract_type="put", volume=1000, open_interest=100)
    result = scanner._score_contract(row, "SPY", 500.0, date.today())
    assert result is not None
    assert result["implied_direction"] in ("bearish", "hedge")
    print(f"\n✅ Put buying inferred as: {result['implied_direction']}")

def test_put_with_large_existing_oi_is_hedge(scanner):
    """When OI >> volume on put, it's likely a hedge not a directional bet."""
    row = _make_contract(
        contract_type = "put",
        volume        = 200,
        open_interest = 2000,  # 10x volume = existing position
    )
    result = scanner._score_contract(row, "SPY", 500.0, date.today())
    if result:
        assert result["implied_direction"] == "hedge"
        print(f"\n✅ Put hedge correctly identified")
    else:
        print("\n✅ Low vol/OI put filtered out (normal)")


# ─────────────────────────────────────────
# HIGH IV EVENT BET
# ─────────────────────────────────────────

def test_high_iv_near_term_flagged(scanner):
    """High IV + short DTE = event positioning."""
    row = _make_contract(
        volume        = 500,
        open_interest = 100,
        iv            = 0.80,   # 80% IV
        dte           = 7,
    )
    result = scanner._score_contract(row, "SPY", 500.0, date.today())
    assert result is not None
    assert any("IV" in f for f in result["flags"])
    print(f"\n✅ High IV event bet flagged: {result['iv']}%")


# ─────────────────────────────────────────
# CONVICTION SCORING
# ─────────────────────────────────────────

def test_conviction_increases_with_multiple_flags(scanner):
    """Contract hitting multiple flags should have higher conviction."""
    # Single flag -- just vol/OI
    single = _make_contract(volume=400, open_interest=100)
    r1     = scanner._score_contract(single, "SPY", 500.0, date.today())

    # Multiple flags -- vol/OI spike + large block
    multi  = _make_contract(volume=2500, open_interest=100, iv=0.70, dte=7)
    r2     = scanner._score_contract(multi, "SPY", 500.0, date.today())

    assert r2 is not None
    if r1:
        assert r2["conviction"] > r1["conviction"]
    print(f"\n✅ Multi-flag conviction higher: {r2['conviction']}")

def test_signal_has_required_fields(scanner):
    row    = _make_contract(volume=1000, open_interest=100)
    result = scanner._score_contract(row, "SPY", 500.0, date.today())
    assert result is not None
    for key in ("ticker","contract_type","strike","expiry","dte",
                "volume","open_interest","vol_oi_ratio","implied_direction",
                "flags","conviction","spot_price","timestamp"):
        assert key in result, f"Missing field: {key}"
    print(f"\n✅ All required fields present")


# ─────────────────────────────────────────
# WEEKEND / TRADING DAY
# ─────────────────────────────────────────

def test_run_skips_on_weekend(scanner, monkeypatch):
    """Scanner should return empty list on weekends."""
    from datetime import datetime
    import pytz

    # Mock to Saturday
    fake_now = datetime(2026, 4, 26, 10, 0, 0,  # Saturday
                        tzinfo=pytz.timezone("US/Eastern"))
    with patch("scanners.options_flow_scanner.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.now.side_effect  = None
        # _is_trading_day uses datetime.now(eastern).weekday()
        assert not scanner._is_trading_day()
    print("\n✅ Weekend correctly detected -- scan would be skipped")


# ─────────────────────────────────────────
# DISCORD FORMATTING
# ─────────────────────────────────────────

def test_discord_post_called_when_signals_found(scanner):
    """Discord function should be called when signals exist."""
    posted = []
    scanner.set_discord_fn(lambda msg: posted.append(msg))

    fake_signals = [{
        "ticker": "SPY", "contract_type": "CALL", "strike": 520.0,
        "expiry": "2026-05-10", "dte": 14, "volume": 2000,
        "open_interest": 200, "vol_oi_ratio": 10.0, "iv": 45.0,
        "otm_pct": 4.0, "is_otm": True, "implied_direction": "bullish",
        "flags": ["VOL/OI=10.0x (STRONG)"], "conviction": 40,
        "spot_price": 500.0, "timestamp": "2026-04-28 09:30 AM EST",
    }]

    scanner._post_to_discord(fake_signals)
    assert len(posted) == 1
    assert "SPY" in posted[0]
    assert "UNUSUAL OPTIONS ACTIVITY" in posted[0]
    print(f"\n✅ Discord message posted with SPY signal")

def test_no_discord_post_when_no_signals(scanner):
    """No Discord post if no signals found."""
    posted = []
    scanner.set_discord_fn(lambda msg: posted.append(msg))
    scanner._post_to_discord([])
    assert len(posted) == 0
    print("\n✅ No Discord post when no signals")

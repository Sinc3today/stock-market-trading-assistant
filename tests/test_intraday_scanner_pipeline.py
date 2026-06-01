"""Phase 3: intraday_scanner wires high-conviction setups → execute_signal.

Flag default ON: scanner produces execute_signal calls for high-conv setups.
Flag OFF: scanner is byte-identical to Phase 2b (zero execute_signal calls).
Standard/watch conviction setups never trigger execute_signal regardless."""

import os, sys
from datetime import datetime, date
from unittest import mock
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
import pytest
import pytz

import scanners.intraday_scanner as _intraday_mod
from scanners.intraday_scanner import IntradayScanner
from signals.spy_options_engine import SPYSetup


@pytest.fixture(autouse=True)
def _clear_fired_cache():
    """Reset the module-level dedup cache before every test so scores from
    one test don't suppress alerts in the next."""
    _intraday_mod._fired_cache.clear()
    yield
    _intraday_mod._fired_cache.clear()

EASTERN = pytz.timezone("US/Eastern")


def _mk_setup(strategy="call_debit_spread", conviction="high", score=75,
              direction="bullish"):
    """Build a minimal SPYSetup-like mock.

    Using a real SPYSetup with only a few fields populated would blow up
    inside to_discord_msg() (None strike formatting).  A Mock with the
    required attributes is simpler and stable against internal SPYSetup
    changes.
    """
    m = mock.Mock(spec=SPYSetup)
    m.strategy   = strategy
    m.conviction = conviction
    m.timeframe  = "intraday"
    m.score      = score
    m.reasons    = ["test"]
    m.direction  = direction
    m.spy_price  = 500.0
    m.to_discord_msg.return_value = f"[mock discord msg: {strategy}]"
    return m


def _mk_alpaca_df():
    return pd.DataFrame({
        "close": [500.0] * 30, "high": [501.0] * 30, "low": [499.0] * 30,
        "volume": [1_000_000] * 30,
    }, index=pd.date_range("2026-05-27 09:30", periods=30, freq="15min"))


def test_flag_off_produces_zero_execute_signal_calls(tmp_path, monkeypatch):
    """The kill-switch path: with INTRADAY_PAPER_BROKER_ENABLED = False,
    the scanner behaves byte-identically to Phase 2b — alerts fire but no
    paper position is opened."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setattr(config, "INTRADAY_PAPER_BROKER_ENABLED", False)

    scanner = IntradayScanner()
    scanner.spy_engine = mock.Mock()
    scanner.spy_engine.analyze.return_value = [_mk_setup(score=78)]
    scanner._fetch_alpaca = mock.Mock(return_value=_mk_alpaca_df())
    scanner.is_market_hours = mock.Mock(return_value=True)
    scanner.logger = mock.Mock()  # avoid JSON serialization of SPYSetup in log_alert

    with mock.patch("scanners.intraday_scanner.PaperBroker") as PB:
        broker_inst = PB.return_value
        scanner._scan_spy_intraday()
        # No execute_signal calls — flag was off.
        assert broker_inst.execute_signal.call_count == 0


def test_flag_on_high_conv_setup_triggers_execute_signal(tmp_path, monkeypatch):
    """Flag on + high-conviction setup → one execute_signal call."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setattr(config, "INTRADAY_PAPER_BROKER_ENABLED", True)

    scanner = IntradayScanner()
    scanner.spy_engine = mock.Mock()
    scanner.spy_engine.analyze.return_value = [_mk_setup(score=78)]
    scanner._fetch_alpaca = mock.Mock(return_value=_mk_alpaca_df())
    scanner.is_market_hours = mock.Mock(return_value=True)
    scanner.logger = mock.Mock()  # avoid JSON serialization of SPYSetup in log_alert

    with mock.patch("scanners.intraday_scanner.PaperBroker") as PB, \
         mock.patch("scanners.intraday_scanner._route_entry") as router_mock, \
         mock.patch("scanners.intraday_scanner.build_intraday_structure",
                    side_effect=lambda sd, **kw: sd) as _bis:
        router_mock.return_value = [{
            "date": "2026-05-27", "strategy": "call_debit_spread",
            "dte_bucket": "0DTE", "book": "disciplined",
            "direction": "bullish", "entry_price": 1.0,
            "max_profit": 200.0, "max_loss": 100.0, "legs": [],
        }]
        broker_inst = PB.return_value
        scanner._scan_spy_intraday()
        assert broker_inst.execute_signal.call_count == 1
        sd = broker_inst.execute_signal.call_args[0][0]
        assert sd["dte_bucket"] == "0DTE"
        assert sd["strategy"]   == "call_debit_spread"


def test_flag_on_standard_conv_does_not_trigger_execute_signal(tmp_path, monkeypatch):
    """Phase 3 ships with ENTRY_TIER_MINIMUM='high'. Standard-tier setups
    still alert, but never become positions."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setattr(config, "INTRADAY_PAPER_BROKER_ENABLED", True)

    scanner = IntradayScanner()
    scanner.spy_engine = mock.Mock()
    scanner.spy_engine.analyze.return_value = [_mk_setup(conviction="standard", score=55)]
    scanner._fetch_alpaca = mock.Mock(return_value=_mk_alpaca_df())
    scanner.is_market_hours = mock.Mock(return_value=True)
    scanner.logger = mock.Mock()  # avoid JSON serialization of SPYSetup in log_alert

    with mock.patch("scanners.intraday_scanner.PaperBroker") as PB:
        broker_inst = PB.return_value
        scanner._scan_spy_intraday()
        assert broker_inst.execute_signal.call_count == 0


def test_router_returning_two_dicts_produces_two_execute_signal_calls(tmp_path, monkeypatch):
    """Ultra-conviction path: router returns [0DTE_dict, 1-3DTE_dict] → two
    execute_signal calls."""
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    monkeypatch.setattr(config, "INTRADAY_PAPER_BROKER_ENABLED", True)

    scanner = IntradayScanner()
    scanner.spy_engine = mock.Mock()
    scanner.spy_engine.analyze.return_value = [_mk_setup(score=92)]
    scanner._fetch_alpaca = mock.Mock(return_value=_mk_alpaca_df())
    scanner.is_market_hours = mock.Mock(return_value=True)
    scanner.logger = mock.Mock()  # avoid JSON serialization of SPYSetup in log_alert

    with mock.patch("scanners.intraday_scanner.PaperBroker") as PB, \
         mock.patch("scanners.intraday_scanner._route_entry") as router_mock, \
         mock.patch("scanners.intraday_scanner.build_intraday_structure",
                    side_effect=lambda sd, **kw: sd) as _bis:
        router_mock.return_value = [
            {"date": "2026-05-27", "strategy": "call_debit_spread",
             "dte_bucket": "0DTE", "book": "disciplined", "direction": "bullish",
             "entry_price": 1.0, "max_profit": 200.0, "max_loss": 100.0, "legs": []},
            {"date": "2026-05-27", "strategy": "call_debit_spread",
             "dte_bucket": "1-3DTE", "book": "disciplined", "direction": "bullish",
             "entry_price": 1.0, "max_profit": 200.0, "max_loss": 100.0, "legs": []},
        ]
        broker_inst = PB.return_value
        scanner._scan_spy_intraday()
        assert broker_inst.execute_signal.call_count == 2
        buckets = {c.args[0]["dte_bucket"] for c in broker_inst.execute_signal.call_args_list}
        assert buckets == {"0DTE", "1-3DTE"}

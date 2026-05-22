"""
tests/test_watchlist_loader.py -- config.load_watchlist() spy_focus collapse.

The bot is SPY-focused for now (other tickers retired until we have a proven
edge). config.load_watchlist() is the single source of truth every scanner
reads, and the spy_focus flag in watchlist.json toggles the collapse.
"""

import json

import config


def _write_watchlist(tmp_path, monkeypatch, data: dict):
    path = tmp_path / "watchlist.json"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(config, "WATCHLIST_PATH", str(path))


def test_spy_focus_true_collapses_all_universes(tmp_path, monkeypatch):
    _write_watchlist(tmp_path, monkeypatch, {
        "swing":           ["SPY", "QQQ", "NVDA"],
        "intraday":        ["SPY", "AAPL"],
        "options_enabled": ["SPY", "TSLA", "AMD"],
        "spy_focus":       True,
    })
    wl = config.load_watchlist()
    assert wl["swing"]           == ["SPY"]
    assert wl["intraday"]        == ["SPY"]
    assert wl["options_enabled"] == ["SPY"]


def test_spy_focus_false_keeps_full_lists(tmp_path, monkeypatch):
    full = {
        "swing":           ["SPY", "QQQ", "NVDA"],
        "intraday":        ["SPY", "AAPL"],
        "options_enabled": ["SPY", "TSLA", "AMD"],
        "spy_focus":       False,
    }
    _write_watchlist(tmp_path, monkeypatch, full)
    wl = config.load_watchlist()
    assert wl["swing"]    == ["SPY", "QQQ", "NVDA"]
    assert wl["intraday"] == ["SPY", "AAPL"]


def test_collapse_skips_missing_keys(tmp_path, monkeypatch):
    # Only some universes present — collapse must not invent keys.
    _write_watchlist(tmp_path, monkeypatch, {
        "intraday":  ["SPY", "AAPL"],
        "spy_focus": True,
    })
    wl = config.load_watchlist()
    assert wl["intraday"] == ["SPY"]
    assert "swing" not in wl


def test_missing_file_falls_back_to_spy_only(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WATCHLIST_PATH", str(tmp_path / "nope.json"))
    wl = config.load_watchlist()
    assert wl["swing"]           == ["SPY"]
    assert wl["intraday"]        == ["SPY"]
    assert wl["options_enabled"] == ["SPY"]

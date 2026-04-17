"""
tests/test_economic_scanner.py — Test economic scanner + FRED client
Requires FRED_API_KEY in .env

Run with:
    pytest tests/test_economic_scanner.py -v
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from data.fred_client import FREDClient, TRACKED_SERIES, HIGH_IMPACT_SERIES
from scanners.economic_scanner import EconomicScanner


@pytest.fixture
def client():
    return FREDClient()

@pytest.fixture
def scanner():
    return EconomicScanner()


# ── FRED Client Tests ─────────────────────────────────────────

def test_fred_client_initializes(client):
    assert client is not None
    print("\n✅ FREDClient initialized")

def test_tracked_series_defined():
    assert len(TRACKED_SERIES) >= 6
    assert "CPIAUCSL" in TRACKED_SERIES
    assert "PAYEMS"   in TRACKED_SERIES
    assert "FEDFUNDS" in TRACKED_SERIES
    print(f"\n✅ {len(TRACKED_SERIES)} tracked series defined")

def test_high_impact_series_defined():
    assert len(HIGH_IMPACT_SERIES) >= 4
    assert "CPIAUCSL" in HIGH_IMPACT_SERIES
    print(f"\n✅ {len(HIGH_IMPACT_SERIES)} high impact series: {HIGH_IMPACT_SERIES}")

def test_get_latest_cpi(client):
    data = client.get_latest_observation("CPIAUCSL")
    assert data is not None
    assert "current_value" in data
    assert "current_date"  in data
    assert "change"        in data
    print(f"\n✅ CPI: {data['current_value']} ({data['current_date']}) change={data['change']}")

def test_get_latest_jobs(client):
    data = client.get_latest_observation("PAYEMS")
    assert data is not None
    assert float(data["current_value"]) > 100000  # NFP in thousands
    print(f"\n✅ NFP: {data['current_value']}K ({data['current_date']})")

def test_observation_has_direction(client):
    data = client.get_latest_observation("FEDFUNDS")
    assert data is not None
    assert data["direction"] in ("up", "down", "flat")
    print(f"\n✅ Fed Rate: {data['current_value']}% direction={data['direction']}")

def test_get_all_tracked(client):
    all_data = client.get_all_tracked()
    assert len(all_data) >= 4
    for sid, data in all_data.items():
        assert "current_value" in data
        assert "name"          in data
    print(f"\n✅ Fetched {len(all_data)} series")

def test_economic_snapshot(client):
    snapshot = client.get_economic_snapshot()
    assert "indicators"       in snapshot
    assert "high_impact"      in snapshot
    assert "recent_releases"  in snapshot
    assert "summary"          in snapshot
    assert len(snapshot["summary"]) > 0
    print(f"\n✅ Snapshot built — summary:\n{snapshot['summary'][:200]}")


# ── Economic Scanner Tests ────────────────────────────────────

def test_scanner_initializes(scanner):
    assert scanner is not None
    print("\n✅ EconomicScanner initialized")

def test_scan_for_recent_releases(scanner):
    releases = scanner.scan_for_new_releases(days_back=60)
    assert isinstance(releases, list)
    print(f"\n✅ Found {len(releases)} releases in last 60 days")
    for r in releases[:3]:
        print(f"  • {r['name']}: {r['current_value']} ({r['current_date']})")

def test_discord_alert_format(scanner):
    mock_release = {
        "series_id":      "CPIAUCSL",
        "name":           "CPI (Inflation)",
        "short":          "CPI",
        "emoji":          "📊",
        "impact":         "HIGH",
        "unit":           "Index",
        "description":    "Consumer Price Index",
        "current_date":   "2026-03-01",
        "current_value":  "315.6",
        "previous_value": "314.2",
        "change":         1.4,
        "change_pct":     0.45,
        "direction":      "up",
    }
    alert = scanner._format_discord_alert(mock_release, "Inflation is rising.")
    assert "CPI" in alert
    assert "315.6" in alert
    assert "ECONOMIC RELEASE" in alert
    print(f"\n✅ Discord alert formatted correctly")

def test_briefing_format(scanner):
    mock_snapshot = {
        "summary": "📊 CPI: 315.6 Index\n🏦 Fed Rate: 4.5 Percent",
        "high_impact": {
            "CPIAUCSL": {
                "emoji": "📊", "short": "CPI", "unit": "Index",
                "current_value": "315.6", "current_date": "2026-03-01",
                "change": 1.4, "impact": "HIGH",
            }
        },
        "recent_releases": [],
    }
    formatted = scanner._format_for_briefing(mock_snapshot, "Markets look cautious.")
    assert "ECONOMIC CONDITIONS" in formatted
    assert "CPI" in formatted
    print(f"\n✅ Briefing format correct")

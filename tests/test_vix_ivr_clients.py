"""
tests/test_vix_ivr_clients.py — Test VIX and IVR clients

Split into two groups:
    - Unit tests (no API, always run)
    - Integration tests (require real internet, marked with @pytest.mark.integration)

Run unit tests only (CI safe):
    pytest tests/test_vix_ivr_clients.py -v -m "not integration"

Run everything including live fetch:
    pytest tests/test_vix_ivr_clients.py -v
"""

import pytest
import sys
import os
import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.vix_client import VIXClient
from data.ivr_client import IVRClient


# ─────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────

def _make_vix_df(low=12.0, high=35.0, current=16.0, days=252) -> pd.DataFrame:
    """Build a synthetic VIX history DataFrame."""
    import numpy as np
    rng    = np.random.default_rng(42)
    closes = rng.uniform(low, high, days)
    closes[-1] = current   # force the last value
    dates  = [date.today() - timedelta(days=days - i) for i in range(days)]
    df     = pd.DataFrame({"close": closes}, index=dates)
    return df


# ─────────────────────────────────────────
# VIX CLIENT — UNIT TESTS
# ─────────────────────────────────────────

class TestVIXClientUnit:

    def test_cboe_csv_parse(self):
        """_fetch_cboe_df should parse a valid CSV string."""
        csv_content = "DATE,OPEN,HIGH,LOW,CLOSE\n04/01/2026,14.2,14.8,13.9,14.5\n04/02/2026,14.5,15.1,14.2,14.9\n"
        with patch("requests.get") as mock_get:
            mock_resp        = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text   = csv_content
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp
            df = VIXClient._fetch_cboe_df()
        assert df is not None
        assert "close" in df.columns
        assert len(df) == 2
        assert float(df["close"].iloc[-1]) == pytest.approx(14.9)
        print(f"\n✅ CBOE CSV parsed: {len(df)} rows")

    def test_cboe_csv_network_error_returns_none(self):
        """Network error on CBOE should return None, not raise."""
        with patch("requests.get", side_effect=Exception("network error")):
            df = VIXClient._fetch_cboe_df()
        assert df is None
        print("\n✅ CBOE network error handled gracefully")

    def test_get_current_returns_safe_fallback_when_all_fail(self):
        """If all data sources fail, get_current returns 20.0 (safe neutral)."""
        client = VIXClient()
        with patch.object(client, "_fetch_polygon_latest", return_value=None), \
             patch.object(client, "_fetch_cboe_latest",    return_value=None):
            vix = client.get_current()
        assert vix == 20.0
        print(f"\n✅ VIX fallback: {vix}")

    def test_get_current_uses_cache(self):
        """Second call within TTL should not re-fetch."""
        client = VIXClient()
        with patch.object(client, "_fetch_cboe_latest", return_value=15.5) as mock_cboe, \
             patch.object(client, "_fetch_polygon_latest", return_value=None):
            v1 = client.get_current()
            v2 = client.get_current()   # should hit cache
        # cboe_latest called at most once (cache hit on second call)
        assert mock_cboe.call_count <= 1
        assert v1 == v2 == 15.5
        print(f"\n✅ VIX cache working: called cboe {mock_cboe.call_count}x")

    def test_get_history_returns_dataframe(self):
        """get_history should return a DataFrame with 'close' column."""
        client = VIXClient()
        fake_df = _make_vix_df()
        with patch.object(client, "_fetch_polygon_history", return_value=None), \
             patch.object(client, "_fetch_cboe_history",    return_value=fake_df):
            df = client.get_history(days=252)
        assert df is not None
        assert "close" in df.columns
        assert len(df) > 0
        print(f"\n✅ VIX history: {len(df)} rows, range {df['close'].min():.1f}–{df['close'].max():.1f}")

    def test_get_history_falls_back_when_polygon_fails(self):
        """Polygon failure should silently use CBOE."""
        import data.vix_client as _vix_mod
        _vix_mod._cache["df"] = None          # clear in-process cache
        _vix_mod._cache["df_at"] = None

        client  = VIXClient()
        fake_df = _make_vix_df(days=100)
        with patch.object(client, "_fetch_polygon_history", return_value=None), \
             patch.object(client, "_fetch_cboe_history",    return_value=fake_df):
            df = client.get_history(days=100)
        assert df is not None
        assert len(df) == 100
        print("\n✅ VIX history fallback to CBOE working")


# ─────────────────────────────────────────
# IVR CLIENT — UNIT TESTS
# ─────────────────────────────────────────

class TestIVRClientUnit:

    def test_vix_proxy_computes_correctly(self):
        """
        IVR = (current - low) / (high - low) * 100
        With VIX=16, low=12, high=32 → IVR = (16-12)/(32-12)*100 = 20.0
        """
        mock_vix = MagicMock()
        mock_vix.get_current.return_value  = 16.0
        mock_vix.get_history.return_value  = _make_vix_df(low=12.0, high=32.0, current=16.0)

        client = IVRClient(polygon_client=None, vix_client=mock_vix)
        ivr    = client._compute_vix_proxy()

        assert ivr is not None
        # IVR should be around 20 (low end of range)
        assert 10 <= ivr <= 35, f"Expected IVR ~20, got {ivr}"
        print(f"\n✅ VIX proxy IVR computed: {ivr:.1f}")

    def test_vix_proxy_high_vix_gives_high_ivr(self):
        """VIX near 52w high should produce IVR near 100."""
        mock_vix = MagicMock()
        mock_vix.get_current.return_value = 34.0
        mock_vix.get_history.return_value = _make_vix_df(low=12.0, high=35.0, current=34.0)

        client = IVRClient(polygon_client=None, vix_client=mock_vix)
        ivr    = client._compute_vix_proxy()

        assert ivr is not None
        assert ivr >= 80, f"Expected high IVR (≥80), got {ivr}"
        print(f"\n✅ High VIX → high IVR: {ivr:.1f}")

    def test_vix_proxy_low_vix_gives_low_ivr(self):
        """VIX near 52w low should produce IVR near 0."""
        mock_vix = MagicMock()
        mock_vix.get_current.return_value = 12.5
        mock_vix.get_history.return_value = _make_vix_df(low=12.0, high=35.0, current=12.5)

        client = IVRClient(polygon_client=None, vix_client=mock_vix)
        ivr    = client._compute_vix_proxy()

        assert ivr is not None
        assert ivr <= 20, f"Expected low IVR (≤20), got {ivr}"
        print(f"\n✅ Low VIX → low IVR: {ivr:.1f}")

    def test_ivr_clamped_0_to_100(self):
        """IVR must never exceed 0–100 bounds."""
        mock_vix = MagicMock()
        # Extreme scenario: current > historical high
        mock_vix.get_current.return_value = 80.0
        mock_vix.get_history.return_value = _make_vix_df(low=10.0, high=40.0, current=80.0)

        client = IVRClient(polygon_client=None, vix_client=mock_vix)
        ivr    = client._compute_vix_proxy()

        assert ivr is not None
        assert 0.0 <= ivr <= 100.0
        print(f"\n✅ IVR clamped: {ivr}")

    def test_safe_fallback_when_vix_unavailable(self):
        """If VIX client returns None, IVR returns 30.0 neutral fallback."""
        mock_vix = MagicMock()
        mock_vix.get_current.return_value = None
        mock_vix.get_history.return_value = None

        client = IVRClient(polygon_client=None, vix_client=mock_vix)
        ivr    = client.get_iv_rank("SPY")

        assert ivr == 30.0
        print(f"\n✅ IVR safe fallback: {ivr}")

    def test_no_clients_returns_fallback(self):
        """No injected clients at all → returns 30.0."""
        client = IVRClient(polygon_client=None, vix_client=None)
        ivr    = client.get_iv_rank("SPY")
        assert ivr == 30.0
        print(f"\n✅ IVR no-client fallback: {ivr}")

    def test_ivr_history_log_written(self, tmp_path, monkeypatch):
        """IV history log should be created and updated."""
        monkeypatch.chdir(tmp_path)
        os.makedirs("logs", exist_ok=True)

        client = IVRClient()
        ivr    = client._compute_ivr_from_history("SPY", current_iv=22.0)

        # First run with only 1 day of history → returns 50.0 neutral
        assert ivr == 50.0
        log_path = tmp_path / "logs" / "iv_history_SPY.json"
        assert log_path.exists()

        with open(log_path) as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["iv"] == 22.0
        print(f"\n✅ IV history log written: {log_path}")


# ─────────────────────────────────────────
# INTEGRATION TESTS (require internet)
# ─────────────────────────────────────────

@pytest.mark.integration
class TestVIXIntegration:

    def test_cboe_live_fetch(self):
        """Fetch real VIX data from CBOE CSV."""
        df = VIXClient._fetch_cboe_df()
        assert df is not None, "CBOE fetch returned None — check internet connection"
        assert len(df) > 100
        assert float(df["close"].iloc[-1]) > 0
        print(f"\n✅ CBOE live VIX: {float(df['close'].iloc[-1]):.2f} ({len(df)} rows)")

    def test_get_current_live(self):
        """VIX current value should be a plausible number."""
        client = VIXClient()
        vix    = client.get_current()
        assert 5.0 <= vix <= 100.0, f"VIX {vix} out of plausible range"
        print(f"\n✅ Live VIX: {vix:.2f}")

    def test_get_history_live(self):
        """VIX history should have sufficient rows for IVR computation."""
        client = VIXClient()
        df     = client.get_history(days=252)
        assert df is not None
        assert len(df) >= 100
        print(f"\n✅ Live VIX history: {len(df)} rows, "
              f"range {df['close'].min():.1f}–{df['close'].max():.1f}")


@pytest.mark.integration
class TestIVRIntegration:

    def test_ivr_live_via_vix_proxy(self):
        """End-to-end IVR computation using live CBOE data."""
        vix_client = VIXClient()
        ivr_client = IVRClient(polygon_client=None, vix_client=vix_client)
        ivr        = ivr_client.get_iv_rank("SPY")
        assert 0.0 <= ivr <= 100.0
        print(f"\n✅ Live SPY IVR (VIX proxy): {ivr:.1f}/100")

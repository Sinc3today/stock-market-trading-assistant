"""
tests/test_backtest_summary.py -- read-only aggregator coverage.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import data.backtest_summary as bs
from learning.knowledge_base import KnowledgeBase, KBEntry
from learning.predictions    import PredictionLog, Prediction


@pytest.fixture
def iso_logs(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path) + "/")
    return tmp_path


# ─────────────────────────────────────────
# Production stats
# ─────────────────────────────────────────

def test_production_stats_returns_defaults_when_no_file(iso_logs):
    stats = bs.production_stats()
    assert stats["source"] == "static_defaults"
    assert stats["overview"]["sharpe"] == pytest.approx(1.73)
    # Core regime claim from docs
    ic = next(r for r in stats["by_regime"] if r["regime"] == "choppy_low_vol")
    assert ic["win_rate_pct"] == pytest.approx(74.1)


def test_production_stats_loads_persisted_file(iso_logs):
    bs.save_production_stats({
        "source": "logs/backtest_summary.json",  # overwritten on read anyway
        "version": "v2",
        "overview": {"sharpe": 2.10, "win_rate_pct": 60.2},
        "by_regime": [],
        "thresholds": {},
    })
    stats = bs.production_stats()
    assert stats["version"] == "v2"
    assert stats["overview"]["sharpe"] == pytest.approx(2.10)


def test_production_stats_falls_back_on_corrupt_file(iso_logs):
    with open(bs._summary_path(), "w") as f:
        f.write("not-json")
    # Should fall back to defaults, not crash
    stats = bs.production_stats()
    assert stats["source"] == "static_defaults"


# ─────────────────────────────────────────
# Hypotheses
# ─────────────────────────────────────────

def test_hypotheses_empty_when_no_dir(iso_logs):
    out = bs.hypotheses_by_status()
    assert out == {"pending": [], "accepted": [], "rejected": [], "inconclusive": []}


def test_hypotheses_grouped_by_verdict(iso_logs):
    d = os.path.join(str(iso_logs), "learning", "hypotheses")
    os.makedirs(d, exist_ok=True)
    cases = [
        ("hyp_a.json", {"id": "a", "verdict": "accepted",      "var": "ADX_TREND_MIN"}),
        ("hyp_b.json", {"id": "b", "verdict": "rejected",      "var": "VIX_CALM_MAX"}),
        ("hyp_c.json", {"id": "c", "verdict": "inconclusive",  "var": "SCORE_ALERT_MINIMUM"}),
        ("hyp_d.json", {"id": "d", "status":  "proposed"}),   # no verdict yet
    ]
    for name, spec in cases:
        with open(os.path.join(d, name), "w") as f:
            json.dump(spec, f)

    out = bs.hypotheses_by_status()
    assert len(out["accepted"])     == 1 and out["accepted"][0]["var"]    == "ADX_TREND_MIN"
    assert len(out["rejected"])     == 1 and out["rejected"][0]["var"]    == "VIX_CALM_MAX"
    assert len(out["inconclusive"]) == 1
    assert len(out["pending"])      == 1 and out["pending"][0]["id"]      == "d"


def test_hypotheses_skips_corrupt_file(iso_logs):
    d = os.path.join(str(iso_logs), "learning", "hypotheses")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "good.json"), "w") as f:
        json.dump({"id": "good", "verdict": "accepted"}, f)
    with open(os.path.join(d, "bad.json"), "w") as f:
        f.write("not-json")
    out = bs.hypotheses_by_status()
    assert len(out["accepted"]) == 1
    assert all("bad" not in str(spec) for specs in out.values() for spec in specs)


def test_hypotheses_unknown_verdict_creates_bucket(iso_logs):
    """Forward-compat: unknown verdict strings get their own bucket
    rather than being silently dropped."""
    d = os.path.join(str(iso_logs), "learning", "hypotheses")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "weird.json"), "w") as f:
        json.dump({"id": "x", "verdict": "DEFERRED"}, f)
    out = bs.hypotheses_by_status()
    assert "deferred" in out and len(out["deferred"]) == 1


# ─────────────────────────────────────────
# Predictions
# ─────────────────────────────────────────

def test_prediction_accuracy_empty(iso_logs):
    out = bs.prediction_accuracy()
    assert out["sample"]   == 0
    assert out["accuracy"] == 0.0


def test_prediction_accuracy_with_resolved_entries(iso_logs):
    pl = PredictionLog()
    pl.save(Prediction(
        date="2026-05-15", regime="choppy_low_vol",
        direction="bullish", tradeable=True, entry_spy=700.0,
    ))
    pl.mark_resolved("2026-05-15", actual_close=705.0,
                      outcome="correct", resolution_date="2026-05-15")
    out = bs.prediction_accuracy()
    assert out["sample"]   == 1
    assert out["accuracy"] == 100.0


# ─────────────────────────────────────────
# KB observations
# ─────────────────────────────────────────

def test_kb_observations_empty(iso_logs):
    assert bs.kb_observations_by_category() == []


def test_kb_observations_grouped_and_sorted(iso_logs):
    kb = KnowledgeBase()
    today = date.today().isoformat()
    kb.append(KBEntry(date=today, category="regime_accuracy",
                      claim="Bullish call confirmed", confidence=0.7))
    kb.append(KBEntry(date=today, category="market_context",
                      claim="VIX flipped to cautious", confidence=0.6))
    kb.append(KBEntry(date=today, category="market_context",
                      claim="Sector dispersion rising", confidence=0.5))

    out = bs.kb_observations_by_category()
    assert out[0]["category"] == "market_context"   # highest count first
    assert out[0]["count"]    == 2
    # latest_claim is a string snippet
    assert "Sector" in out[0]["latest_claim"] or "VIX" in out[0]["latest_claim"]

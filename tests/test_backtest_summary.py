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


# ─────────────────────────────────────────
# Recent predictions (for /learning page)
# ─────────────────────────────────────────

def test_recent_predictions_empty(iso_logs):
    assert bs.recent_predictions() == []


def test_recent_predictions_normalises_rows(iso_logs):
    pl = PredictionLog()
    pl.save(Prediction(
        date="2026-05-15", regime="trending_up_calm",
        direction="bullish", tradeable=True, entry_spy=700.0,
    ))
    pl.mark_resolved("2026-05-15", actual_close=705.0,
                     outcome="correct", resolution_date="2026-05-15")
    pl.save(Prediction(
        date="2026-05-16", regime="choppy_low_vol",
        direction="neutral", tradeable=True, entry_spy=702.0,
    ))   # unresolved

    out = bs.recent_predictions(n=10)
    assert len(out) == 2
    by_date = {r["date"]: r for r in out}
    assert by_date["2026-05-15"]["outcome"]  == "correct"
    assert by_date["2026-05-15"]["resolved"] is True
    assert by_date["2026-05-16"]["outcome"]  is None
    assert by_date["2026-05-16"]["resolved"] is False


def test_recent_predictions_attaches_skip_verdict(iso_logs):
    pl = PredictionLog()
    pl.save(Prediction(date="2026-05-19", regime="trending_up_calm",
                       direction="bullish", tradeable=False, entry_spy=737.80))
    pl.mark_resolved("2026-05-19", 733.73, "skip", "2026-05-19")
    row = bs.recent_predictions(n=5)[0]
    assert row["outcome"]      == "skip"
    assert row["skip_verdict"] == "right"   # bullish skip, SPY fell


def test_skip_quality_wrapper(iso_logs):
    pl = PredictionLog()
    pl.save(Prediction(date="2026-05-19", regime="trending_up_calm",
                       direction="bullish", tradeable=False, entry_spy=737.80))
    pl.mark_resolved("2026-05-19", 733.73, "skip", "2026-05-19")
    out = bs.skip_quality()
    assert out["right"] == 1
    assert out["right_pct"] == 100.0


# ─────────────────────────────────────────
# Paper trade stats (for /learning page)
# ─────────────────────────────────────────

def test_paper_trade_stats_empty(iso_logs):
    out = bs.paper_trade_stats()
    assert out["open"]   == 0
    assert out["closed"] == 0
    assert out["total_pnl"] == 0.0
    assert out["closed_trades"] == []


def test_paper_trade_stats_only_counts_auto_tagged(iso_logs):
    """Manual trades must NOT contaminate the paper-broker stats."""
    from journal.trade_recorder import TradeRecorder
    from learning.paper_broker  import AUTO_TAG
    tr = TradeRecorder()
    # One AUTO-PAPER trade, still open
    tr.log_entry(
        ticker="SPY", entry_price=1.0, size=1,
        trade_type="credit_spread", strategy="credit_spread",
        direction="bullish", notes=f"{AUTO_TAG} regime=trending_up_calm",
    )
    # One MANUAL trade (no AUTO-PAPER tag) — should be ignored
    tr.log_entry(
        ticker="QQQ", entry_price=2.5, size=1,
        trade_type="single_leg", direction="bullish",
        notes="Manual entry — testing",
    )
    out = bs.paper_trade_stats()
    assert out["open"]   == 1
    assert out["closed"] == 0
    # Manual QQQ trade is excluded
    assert all(t["ticker"] == "SPY" for t in out["open_trades"])


def test_paper_trade_stats_aggregates_closed(iso_logs):
    """Closed AUTO-PAPER trades roll into win-rate and total P&L."""
    from journal.trade_recorder import TradeRecorder
    from learning.paper_broker  import AUTO_TAG
    tr = TradeRecorder()
    tid_win = tr.log_entry(
        ticker="SPY", entry_price=1.0, size=1,
        trade_type="credit_spread", strategy="credit_spread",
        direction="bullish", max_loss=4.0,
        notes=f"{AUTO_TAG} winner",
    )
    tid_loss = tr.log_entry(
        ticker="SPY", entry_price=1.0, size=1,
        trade_type="credit_spread", strategy="credit_spread",
        direction="bullish", max_loss=4.0,
        notes=f"{AUTO_TAG} loser",
    )
    tr.log_exit(tid_win,  exit_price=0.2)  # bought back cheap → profit
    tr.log_exit(tid_loss, exit_price=3.5)  # bought back expensive → loss

    out = bs.paper_trade_stats()
    assert out["open"]   == 0
    assert out["closed"] == 2
    assert out["wins"] + out["losses"] == 2
    assert isinstance(out["total_pnl"], (int, float))
    assert len(out["closed_trades"]) == 2
    # Sparkline series: starts at 0 + one point per closed trade.
    assert out["cumulative_pnl_series"][0] == 0.0
    assert len(out["cumulative_pnl_series"]) == 3
    # Last point of the series matches the reported total P&L.
    assert out["cumulative_pnl_series"][-1] == out["total_pnl"]


def test_paper_trade_stats_cumulative_series_is_monotonic_when_all_wins(iso_logs):
    """If every closed trade is a win, the cumulative series should be
    strictly non-decreasing."""
    from journal.trade_recorder import TradeRecorder
    from learning.paper_broker  import AUTO_TAG
    tr = TradeRecorder()
    for i in range(3):
        tid = tr.log_entry(
            ticker="SPY", entry_price=1.0, size=1,
            trade_type="credit_spread", strategy="credit_spread",
            direction="bullish", max_loss=4.0,
            notes=f"{AUTO_TAG} winner-{i}",
        )
        tr.log_exit(tid, exit_price=0.2)  # buy back cheap → profit

    series = bs.paper_trade_stats()["cumulative_pnl_series"]
    assert series[0] == 0.0
    assert len(series) == 4
    for a, b in zip(series, series[1:]):
        assert b >= a, f"series not non-decreasing: {series}"

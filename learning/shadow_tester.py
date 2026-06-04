"""learning/shadow_tester.py -- Extension-gate shadow-test (anti-bias).

On a day the regime's extension gate forces SKIP, record + score the
counterfactual bull trade the gate refused. See
docs/superpowers/specs/2026-06-03-extension-gate-shadow-test-design.md.
"""
from __future__ import annotations

import os
import sys
from datetime import date as _date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from loguru import logger

import config
from signals.regime_detector import Regime

SHADOW_BOOK = "shadow"


def _is_extension_skip(regime_result) -> bool:
    """True only for the extension-gate skip (TRENDING_UP_CALM, not tradeable,
    reason mentions over-extension).

    Checks both `recommendation` and `play` attributes so it works with
    both test SimpleNamespace objects (which use `recommendation`) and
    real RegimeResult dataclasses (which use `play`).
    """
    rec = (
        getattr(regime_result, "recommendation", None)
        or getattr(regime_result, "play", None)
        or ""
    ).lower()
    return (
        getattr(regime_result, "regime", None) == Regime.TRENDING_UP_CALM
        and not getattr(regime_result, "tradeable", True)
        and ("extended" in rec or "extension" in rec)
    )


def _build_shadow_options(options_layer, spot: float, ivr: float) -> dict | None:
    """Build the would-be bull structure, mirroring SPYDailyStrategy's bull path.

    Uses the same synthetic score_result, target/stop bands, mode, iv_rank, and
    dte_target that spy_daily_strategy.py uses for TRENDING_UP_CALM:
      - direction: "bullish"
      - target = spot * 1.02  (mirrors _direction_and_levels for bullish regime)
      - stop   = spot * 0.98
      - mode   = "swing"
      - dte_target = 45  (project default for swing SPY plays)

    OptionsLayer picks bull put credit if IVR>=50 & not PREFER_DEBIT_OVER_CREDIT,
    else bull call debit — same logic as the daily play's analyze() dispatch.
    """
    target = round(spot * 1.02, 2)
    stop   = round(spot * 0.98, 2)
    score_result = {
        "final_score": 85,
        "direction":   "bullish",
        "tier":        "regime_driven",
    }
    try:
        return options_layer.analyze(
            "SPY", score_result, spot, target, stop,
            mode="swing", iv_rank=ivr, dte_target=45,
        )
    except Exception as e:
        logger.warning(f"shadow: OptionsLayer.analyze failed: {e}")
        return None


def run_shadow(
    regime_result,
    *,
    spot: float,
    ivr: float,
    options_layer,
    trade_recorder,
    today=None,
) -> "dict | None":
    """If today is an extension-skip, build + record the counterfactual bull
    trade as a book='shadow'/source='auto-paper' paper position.

    Returns ``{"recorded": True, "trade_id": tid}`` on success, ``None``
    when the gate is disabled or this is not an extension-skip day.

    Parameters are fully dependency-injected so tests supply fakes — no live
    clients are created inside this function.
    """
    if not config.SHADOW_TEST_ENABLED:
        return None
    if not _is_extension_skip(regime_result):
        return None

    opts = _build_shadow_options(options_layer, spot, ivr)
    if not opts or not opts.get("legs"):
        logger.info("shadow: no priceable bull structure today — no shadow trade")
        return None

    today = today or _date.today()

    from learning.paper_broker import AUTO_SOURCE  # late import avoids circular dep

    tid = trade_recorder.log_entry(
        ticker      = "SPY",
        entry_price = float(opts.get("net_premium") or opts.get("entry_price") or 1.0),
        size        = 1,
        trade_type  = opts.get("strategy", "credit_spread"),
        strategy    = opts.get("strategy", "credit_spread"),
        direction   = "bullish",
        mode        = "swing",
        legs        = opts.get("legs", []),
        max_profit  = opts.get("max_profit"),
        max_loss    = opts.get("max_loss"),
        notes       = f"[SHADOW {today.isoformat()}] extension-gate counterfactual bull play",
        dte_bucket  = "45DTE",
        book        = SHADOW_BOOK,
        source      = AUTO_SOURCE,
    )

    # Stamp entry_spy for directional scoring by outcome_resolver at EOD.
    trades = trade_recorder.get_all_trades()
    for t in trades:
        if t.get("trade_id") == tid:
            t["entry_spy"] = float(spot)
            break
    trade_recorder._save(trades)

    logger.info(f"shadow: recorded counterfactual bull trade {tid} (book=shadow, entry_spy={spot})")
    return {"recorded": True, "trade_id": tid}


def shadow_stats(n_days: int = 30, *, trade_recorder=None) -> dict:
    """Rolling expectancy over book='shadow' trades. Neutral (n=0) when none.

    Parameters
    ----------
    n_days:
        Accepted for API stability; v1 aggregates over all shadow trades
        (a date-window filter is a noted follow-up).
    trade_recorder:
        Optional injected TradeRecorder; a fresh one is constructed when None.

    Returns
    -------
    dict with keys:
        n                    — total shadow trades
        n_closed             — trades with outcome in (win/loss/breakeven)
        closed_pnl           — sum of pnl_dollars for closed trades
        open_mtm             — mark-to-market on open trades (0.0 in v1)
        directional_win_rate — fraction of shadow_directional="correct" over scored trades
    """
    if trade_recorder is None:
        from journal.trade_recorder import TradeRecorder
        trade_recorder = TradeRecorder()

    shadow = [t for t in trade_recorder.get_all_trades() if t.get("book") == SHADOW_BOOK]
    closed = [t for t in shadow if t.get("outcome") in ("win", "loss", "breakeven")]
    closed_pnl = round(sum(t.get("pnl_dollars") or 0.0 for t in closed), 2)
    scored = [t for t in shadow if t.get("shadow_directional") in ("correct", "wrong")]
    correct = sum(1 for t in scored if t.get("shadow_directional") == "correct")
    win_rate = round(correct / len(scored), 3) if scored else 0.0
    return {
        "n":                    len(shadow),
        "n_closed":             len(closed),
        "closed_pnl":           closed_pnl,
        "open_mtm":             0.0,
        "directional_win_rate": win_rate,
    }

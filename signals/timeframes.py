"""
signals/timeframes.py -- Multi-timeframe track registry.

The bot is moving from a single 45-DTE swing decision per day toward four
independent timeframe TRACKS, each with its own decision-making, expiry, and
exit math:

    0DTE   same-day expiry      intraday, fastest, most gamma/tail risk
    1DTE   next-day expiry       intraday
    5DTE   ~weekly               daily-data OK
    45DTE  swing (the original)  daily-data OK

A track shares the regime's directional read (the regime classifier is
timeframe-agnostic -- it describes market state) but expresses it at its own
DTE with its own exit rules. Shorter DTE => shorter holds => the position
slot frees up faster => more trades, which is how stacking tracks reaches a
higher daily frequency.

Single source of truth: both the realistic backtest and (later) the live
engine read tracks from here, so per-track params never drift between them.

Data reality: 0DTE/1DTE need intraday bars + intraday signal logic + intraday
backtest data (a 0DTE trade lives entirely within one day -- daily bars can't
see its path). They are registered here but flagged requires_intraday=True
and left disabled until that infrastructure exists. 5DTE/45DTE run on the
daily data we already have.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TimeframeTrack:
    """One timeframe's decision profile. Consumed by backtest + live engine."""

    name:                str     # "0DTE" | "1DTE" | "5DTE" | "45DTE"
    target_dte:          int     # calendar days to expiry at entry
    profit_target_pct:   float   # close once this fraction of max profit captured
    dte_close_threshold: int     # time-stop: close at <= this many days to expiry
    requires_intraday:   bool    # needs intraday data/signals (not yet built)
    enabled:             bool     # whether the track is live in production
    note:                str = ""

    @property
    def daily_backtestable(self) -> bool:
        """Can this track be honestly backtested on daily bars? Sub-2-DTE
        holds need intraday resolution; daily bars can't model them."""
        return not self.requires_intraday


# ── REGISTRY ──────────────────────────────────────────────────────────
# Profit targets scale with DTE: shorter trades take profit faster (less
# time for theta to keep working), longer trades let it ride further.
TRACKS: list[TimeframeTrack] = [
    TimeframeTrack(
        name="0DTE", target_dte=0, profit_target_pct=0.50, dte_close_threshold=0,
        requires_intraday=True, enabled=False,
        note="Same-day expiry. Needs intraday signals + data. Highest tail risk.",
    ),
    TimeframeTrack(
        name="1DTE", target_dte=1, profit_target_pct=0.55, dte_close_threshold=0,
        requires_intraday=True, enabled=False,
        note="Next-day expiry. Needs intraday infrastructure.",
    ),
    TimeframeTrack(
        name="5DTE", target_dte=5, profit_target_pct=0.65, dte_close_threshold=1,
        requires_intraday=False, enabled=True,
        note="~Weekly. Runs on daily data. Higher frequency than 45DTE.",
    ),
    TimeframeTrack(
        name="45DTE", target_dte=45, profit_target_pct=0.70, dte_close_threshold=21,
        requires_intraday=False, enabled=True,
        note="The original swing track. Iron-condor core edge.",
    ),
]

_BY_NAME = {t.name: t for t in TRACKS}


def get_track(name: str) -> TimeframeTrack:
    """Look up a track by name (case-insensitive). Raises KeyError if absent."""
    return _BY_NAME[name.upper()]


def enabled_tracks() -> list[TimeframeTrack]:
    return [t for t in TRACKS if t.enabled]


def daily_backtestable_tracks() -> list[TimeframeTrack]:
    """Tracks we can honestly backtest on daily bars right now (5DTE, 45DTE)."""
    return [t for t in TRACKS if t.daily_backtestable]

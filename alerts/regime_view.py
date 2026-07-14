"""alerts/regime_view.py -- pure builders for the /regime page.

The page teaches WHY the classifier picked today's regime (threshold gauges),
where today sits in the 6-regime map, and what each stress-tested structure
looks like at the current price: expiry payoff diagram (inline SVG, no chart
libs), a model probability-of-profit, and the backtested win rate under real
management. Model-POP and managed-win-rate are deliberately shown side by
side — they answer different questions (expiry maths vs how we actually trade,
closing at 70% of max profit / 21 DTE).

Everything here is pure: no network, no clients. web_app fetches, this renders.
"""
from __future__ import annotations

import math

from signals.condor_calc import _norm_cdf, _strike_for_delta, build_butterfly, build_condor
from learning.exit_manager import bs_price


# ── probability helpers (r=0 lognormal, same assumptions as bs_price) ──────

def _p_above(spot: float, k: float, t: float, sigma: float) -> float:
    """P(S_T > k) = N(d2)."""
    if k <= 0 or spot <= 0 or t <= 0 or sigma <= 0:
        return 0.0
    d2 = (math.log(spot / k) - 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
    return _norm_cdf(d2)


def pop_above(spot: float, vix: float, dte: int, be: float) -> float:
    return _p_above(spot, be, dte / 365.0, (vix or 16.0) / 100.0)


def pop_between(spot: float, vix: float, dte: int, be_low: float, be_high: float) -> float:
    t, sigma = dte / 365.0, (vix or 16.0) / 100.0
    return max(0.0, _p_above(spot, be_low, t, sigma) - _p_above(spot, be_high, t, sigma))


# ── expiry payoff ───────────────────────────────────────────────────────────

def payoff_points(legs: list[tuple], net_debit: float, lo: float, hi: float,
                  n: int = 120) -> list[tuple]:
    """Expiry P&L per SHARE x100 across [lo, hi].
    legs: (opt_type 'call'/'put', strike, signed_qty) — BUY +, SELL -.
    net_debit: what the position cost (negative = credit received)."""
    pts = []
    for i in range(n + 1):
        s = lo + (hi - lo) * i / n
        v = 0.0
        for opt, k, q in legs:
            intrinsic = max(0.0, s - k) if opt == "call" else max(0.0, k - s)
            v += q * intrinsic
        pts.append((s, (v - net_debit) * 100))
    return pts


def payoff_svg(legs: list[tuple], net_debit: float, spot: float,
               width: int = 340, height: int = 150, pad: int = 8) -> str:
    """Inline-SVG expiry payoff: green fill above zero, red below, zero axis,
    dashed spot marker. Price range = ±8% around spot (covers all wings)."""
    lo, hi = spot * 0.92, spot * 1.08
    pts = payoff_points(legs, net_debit, lo, hi)
    ys = [p[1] for p in pts]
    y_min, y_max = min(ys), max(ys)
    if y_max == y_min:
        y_max = y_min + 1
    yr = (y_max - y_min) * 0.12
    y_min, y_max = y_min - yr, y_max + yr

    def X(s):
        return pad + (s - lo) / (hi - lo) * (width - 2 * pad)

    def Y(p):
        return height - pad - (p - y_min) / (y_max - y_min) * (height - 2 * pad)

    line = " ".join(f"{X(s):.1f},{Y(p):.1f}" for s, p in pts)
    y0 = Y(0)
    # profit / loss fills: the curve polygon clamped against the zero line
    pos_poly = "M" + f"{X(pts[0][0]):.1f},{y0:.1f} " + \
        " ".join(f"L{X(s):.1f},{min(Y(p), y0):.1f}" for s, p in pts) + \
        f" L{X(pts[-1][0]):.1f},{y0:.1f} Z"
    neg_poly = "M" + f"{X(pts[0][0]):.1f},{y0:.1f} " + \
        " ".join(f"L{X(s):.1f},{max(Y(p), y0):.1f}" for s, p in pts) + \
        f" L{X(pts[-1][0]):.1f},{y0:.1f} Z"
    return (
        f'<svg class="payoff" viewBox="0 0 {width} {height}" width="100%" '
        f'preserveAspectRatio="none" role="img" aria-label="expiry payoff">'
        f'<path d="{pos_poly}" fill="var(--ok,#16a34a)" opacity=".16"/>'
        f'<path d="{neg_poly}" fill="var(--err,#dc2626)" opacity=".10"/>'
        f'<line x1="{pad}" y1="{y0:.1f}" x2="{width-pad}" y2="{y0:.1f}" '
        f'stroke="var(--border,#e4e4e7)" stroke-width="1"/>'
        f'<line x1="{X(spot):.1f}" y1="{pad}" x2="{X(spot):.1f}" y2="{height-pad}" '
        f'stroke="var(--fg-subtle,#a1a1aa)" stroke-width="1" stroke-dasharray="3,3"/>'
        f'<polyline points="{line}" fill="none" stroke="var(--accent,#4f46e5)" '
        f'stroke-width="2" stroke-linejoin="round"/>'
        f'<text x="{X(spot)+3:.1f}" y="{pad+9}" font-size="9" '
        f'fill="var(--fg-subtle,#a1a1aa)">SPY {spot:,.0f}</text>'
        '</svg>'
    )


# ── threshold gauges ────────────────────────────────────────────────────────

def gauge_svg(label: str, value: float, vmin: float, vmax: float,
              marks: list[tuple], value_fmt: str = "{:.1f}",
              width: int = 340, height: int = 40) -> str:
    """Horizontal gauge: track, threshold tick(s), marker at today's value.
    marks: [(threshold_value, short_label)] rendered as ticks on the track."""
    pad, track_y, track_h = 8, 22, 8
    span = (vmax - vmin) or 1.0

    def X(v):
        v = max(vmin, min(vmax, v))
        return pad + (v - vmin) / span * (width - 2 * pad)

    ticks = "".join(
        f'<line x1="{X(tv):.1f}" y1="{track_y-4}" x2="{X(tv):.1f}" '
        f'y2="{track_y+track_h+4}" stroke="var(--fg-muted,#52525b)" stroke-width="1.5"/>'
        f'<text x="{X(tv):.1f}" y="{track_y+track_h+14}" font-size="8.5" text-anchor="middle" '
        f'fill="var(--fg-subtle,#a1a1aa)">{tl}</text>'
        for tv, tl in marks)
    xv = X(value)
    return (
        f'<svg class="gauge" viewBox="0 0 {width} {height}" width="100%" role="img" '
        f'aria-label="{label}">'
        f'<text x="{pad}" y="10" font-size="9.5" fill="var(--fg-subtle,#a1a1aa)" '
        f'style="text-transform:uppercase;letter-spacing:.04em">{label}</text>'
        f'<rect x="{pad}" y="{track_y}" width="{width-2*pad}" height="{track_h}" rx="4" '
        f'fill="var(--border,#e4e4e7)" opacity=".55"/>'
        f'{ticks}'
        f'<circle cx="{xv:.1f}" cy="{track_y+track_h/2}" r="6" '
        f'fill="var(--accent,#4f46e5)"/>'
        f'<text x="{min(max(xv, 22), width-30):.1f}" y="14" font-size="10.5" '
        f'text-anchor="middle" font-weight="700" fill="var(--fg,#18181b)">'
        f'{value_fmt.format(value)}</text>'
        '</svg>'
    )


# ── regime map ──────────────────────────────────────────────────────────────

REGIME_CELLS = [
    # (regime_value, trend_row, vol_col, play_short, tradeable)
    ("trending_up_calm",   "Trending up",   "Calm",     "Bull spread",      True),
    ("trending_down_calm", "Trending down", "Calm",     "Bear spread",      True),
    ("trending_high_vol",  "Trending",      "High vol", "SKIP (19% win)",   False),
    ("choppy_low_vol",     "Choppy",        "Calm",     "Iron condor",      True),
    ("choppy_transition",  "Choppy",        "VIX 18-22", "Half-size condor", True),
    ("choppy_high_vol",    "Choppy",        "High vol", "SKIP (condor poison)", False),
]


# ── stress-tested structures per regime ────────────────────────────────────

DTE = 45

# Backtested win rates under live management (70% target / 21-DTE close).
HIST = {
    "iron_condor":   ("74%", "5-yr tuned backtest (choppy regimes)"),
    "butterfly":     ("61%", "STRUCTURE_COMPARISON — same management as the condor"),
    "put_credit":    ("79%", "DIRECTIONAL_SPREAD_STUDY (trending-up-calm days)"),
    "call_debit":    ("68%", "DIRECTIONAL_SPREAD_STUDY (85% in the 7-9% extension band)"),
}


def build_structures(spot: float, vix: float, regime_value: str) -> list[dict]:
    """All four stress-tested structures at the current price, each tagged
    validated / off-regime / stand-down for the CURRENT regime."""
    sigma = (vix or 16.0) / 100.0
    t = DTE / 365.0
    out = []

    def status_for(name):
        if regime_value.endswith("high_vol"):
            return "stand-down"
        neutral = name in ("iron_condor", "butterfly")
        choppy = regime_value.startswith("choppy")
        return "validated" if (neutral == choppy) else "off-regime"

    # iron condor (0.20Δ shorts, $5 wings — the live builder)
    try:
        c = build_condor(spot, vix, dte=DTE)
        legs = [("call", c["short_call"], -1), ("call", c["long_call"], +1),
                ("put", c["short_put"], -1), ("put", c["long_put"], +1)]
        out.append({
            "key": "iron_condor", "name": "Iron condor",
            "desc": f"sell {c['short_put']:g}P/{c['short_call']:g}C, $5 wings",
            "legs": legs, "net_debit": -c["credit"],
            "max_profit": c["max_profit"], "max_loss": c["max_loss"],
            "pop": pop_between(spot, vix, DTE, c["breakeven_low"], c["breakeven_high"]),
            "hist": HIST["iron_condor"], "status": status_for("iron_condor"),
        })
    except Exception:
        pass

    # long call butterfly (low-capital neutral)
    try:
        b = build_butterfly(spot, vix, dte=DTE)
        legs = [("call", b["lower"], +1), ("call", b["center"], -2),
                ("call", b["upper"], +1)]
        out.append({
            "key": "butterfly", "name": "Long call butterfly",
            "desc": f"{b['lower']:g}/{b['center']:g}/{b['upper']:g}, 1-2-1",
            "legs": legs, "net_debit": b["capital"] / 100.0,
            "max_profit": b["max_profit"], "max_loss": b["capital"],
            "pop": pop_between(spot, vix, DTE, b["breakeven_low"], b["breakeven_high"]),
            "hist": HIST["butterfly"], "status": status_for("butterfly"),
        })
    except Exception:
        pass

    # bull put credit spread (sell 0.40Δ put, $5 wing — what the engine opened)
    try:
        sk = _strike_for_delta("put", spot, t, sigma, 0.40)
        lk = sk - 5.0
        credit = bs_price("put", spot, sk, t, sigma) - bs_price("put", spot, lk, t, sigma)
        be = sk - credit
        out.append({
            "key": "put_credit", "name": "Bull put credit spread",
            "desc": f"sell {sk:g}P / buy {lk:g}P",
            "legs": [("put", sk, -1), ("put", lk, +1)], "net_debit": -credit,
            "max_profit": credit * 100, "max_loss": (5.0 - credit) * 100,
            "pop": pop_above(spot, vix, DTE, be),
            "hist": HIST["put_credit"],
            "status": ("validated" if regime_value == "trending_up_calm"
                       else "stand-down" if regime_value.endswith("high_vol")
                       else "off-regime"),
        })
    except Exception:
        pass

    # bull call debit spread (buy 0.55Δ / sell 0.30Δ — the regime card's pick)
    try:
        lk = _strike_for_delta("call", spot, t, sigma, 0.55)
        sk = _strike_for_delta("call", spot, t, sigma, 0.30)
        debit = bs_price("call", spot, lk, t, sigma) - bs_price("call", spot, sk, t, sigma)
        be = lk + debit
        out.append({
            "key": "call_debit", "name": "Bull call debit spread",
            "desc": f"buy {lk:g}C / sell {sk:g}C",
            "legs": [("call", lk, +1), ("call", sk, -1)], "net_debit": debit,
            "max_profit": (sk - lk - debit) * 100, "max_loss": debit * 100,
            "pop": pop_above(spot, vix, DTE, be),
            "hist": HIST["call_debit"],
            "status": ("validated" if regime_value == "trending_up_calm"
                       else "stand-down" if regime_value.endswith("high_vol")
                       else "off-regime"),
        })
    except Exception:
        pass

    order = {"validated": 0, "off-regime": 1, "stand-down": 2}
    out.sort(key=lambda d: order.get(d["status"], 9))
    return out

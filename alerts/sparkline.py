"""alerts/sparkline.py -- tiny inline-SVG sparkline + delta chip for stat cards.

Pure string helpers (no deps) used by the dashboard stat cards. SVG keeps the
charts free, scalable, theme-aware (stroke uses a CSS var), and server-rendered.
"""
from __future__ import annotations


def sparkline_svg(values, width: int = 120, height: int = 32,
                  stroke: str = "var(--accent)") -> str:
    """An inline SVG polyline of `values`. Empty string if fewer than 2 points."""
    vals = [float(v) for v in (values or []) if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    n = len(vals)
    pad = 2.0
    pts = []
    for i, v in enumerate(vals):
        x = pad + i / (n - 1) * (width - 2 * pad)
        y = pad + (1 - (v - lo) / rng) * (height - 2 * pad)
        pts.append(f"{x:.1f},{y:.1f}")
    lx, ly = pts[-1].split(",")
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'preserveAspectRatio="none" aria-hidden="true">'
        f'<polyline points="{" ".join(pts)}" fill="none" stroke="{stroke}" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="{lx}" cy="{ly}" r="2.4" fill="{stroke}"/>'
        f'</svg>'
    )


def gauge_svg(pct, size: int = 96, thickness: int = 10,
              stroke: str = "var(--accent)", track: str = "var(--border)") -> str:
    """A donut gauge (track ring + value arc + center %), like the reference's
    'Weekly Goal 80%'. Empty string on bad input; clamps 0-100."""
    import math
    try:
        p = max(0.0, min(100.0, float(pct)))
    except (TypeError, ValueError):
        return ""
    r = (size - thickness) / 2
    circ = 2 * math.pi * r
    dash = circ * p / 100
    c = size / 2
    return (
        f'<svg class="gauge" viewBox="0 0 {size} {size}" width="{size}" height="{size}" aria-hidden="true">'
        f'<circle cx="{c}" cy="{c}" r="{r:.1f}" fill="none" stroke="{track}" stroke-width="{thickness}"/>'
        f'<circle cx="{c}" cy="{c}" r="{r:.1f}" fill="none" stroke="{stroke}" stroke-width="{thickness}" '
        f'stroke-linecap="round" stroke-dasharray="{dash:.1f} {circ:.1f}" '
        f'transform="rotate(-90 {c} {c})"/>'
        f'<text x="{c}" y="{c}" text-anchor="middle" dominant-baseline="central" '
        f'fill="var(--fg)" font-size="{size * 0.26:.0f}" font-weight="600">{p:.0f}%</text>'
        f'</svg>'
    )


def delta_chip(pct, suffix: str = "%") -> str:
    """A colored up/down/flat delta pill, e.g. ↑ 11.5%."""
    try:
        p = float(pct)
    except (TypeError, ValueError):
        return ""
    if p > 0:
        cls, arrow = "delta-up", "↑"
    elif p < 0:
        cls, arrow = "delta-down", "↓"
    else:
        cls, arrow = "delta-flat", "→"
    return f'<span class="delta-chip {cls}">{arrow} {abs(p):g}{suffix}</span>'

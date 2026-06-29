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

"""tests/test_sparkline.py -- inline SVG sparkline for the dashboard stat cards."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def test_sparkline_renders_polyline_with_all_points():
    from alerts.sparkline import sparkline_svg
    svg = sparkline_svg([1, 2, 3, 4, 5], width=100, height=20)
    assert svg.startswith("<svg") and "polyline" in svg
    # 5 points -> 5 "x,y" pairs in the polyline
    pts = svg.split('points="')[1].split('"')[0].strip().split(" ")
    assert len(pts) == 5
    # rising series: last y should be the smallest (SVG y grows downward)
    ys = [float(p.split(",")[1]) for p in pts]
    assert ys[-1] == min(ys) and ys[0] == max(ys)


def test_sparkline_flat_series_is_safe():
    from alerts.sparkline import sparkline_svg
    svg = sparkline_svg([3.0, 3.0, 3.0])   # zero range must not divide-by-zero
    assert "<svg" in svg and "polyline" in svg


def test_sparkline_too_few_points_returns_empty():
    from alerts.sparkline import sparkline_svg
    assert sparkline_svg([]) == ""
    assert sparkline_svg([5]) == ""


def test_gauge_renders_arc_and_percent():
    from alerts.sparkline import gauge_svg
    svg = gauge_svg(68)
    assert svg.startswith("<svg") and "68%" in svg
    # two rings: a track + the value arc (stroke-dasharray)
    assert svg.count("<circle") == 2 and "stroke-dasharray" in svg


def test_gauge_clamps_and_handles_bad_input():
    from alerts.sparkline import gauge_svg
    assert "100%" in gauge_svg(150)        # clamp high
    assert "0%" in gauge_svg(-5)           # clamp low
    assert gauge_svg(None) == ""


def test_delta_chip_direction_and_class():
    from alerts.sparkline import delta_chip
    up = delta_chip(11.5)
    assert "delta-up" in up and "↑" in up and "11.5" in up
    down = delta_chip(-4.2)
    assert "delta-down" in down and "↓" in down
    flat = delta_chip(0.0)
    assert "delta-flat" in flat

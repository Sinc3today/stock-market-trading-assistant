"""Smoke test: every place that pins a Sonnet model name uses the current stable."""

import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import learning.reflector            as reflector
import learning.hypothesis_engine    as hyp_engine
import learning.off_hours_learner    as off_hours
import signals.morning_briefer       as briefer

CURRENT_SONNET = "claude-sonnet-4-6"


def test_all_sonnet_pins_are_current():
    assert reflector.CLAUDE_MODEL.startswith(CURRENT_SONNET)
    assert hyp_engine.CLAUDE_MODEL.startswith(CURRENT_SONNET)
    assert off_hours.CLAUDE_MODEL.startswith(CURRENT_SONNET)
    assert briefer.CLAUDE_MODEL.startswith(CURRENT_SONNET)

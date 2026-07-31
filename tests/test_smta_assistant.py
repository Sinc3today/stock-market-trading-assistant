"""tests/test_smta_assistant.py -- local-LLM SMTA assistant + HANDOFF routing."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from alerts.smta_assistant import parse_handoff, ask


def test_parse_handoff_detects_and_extracts():
    ok, refined = parse_handoff("HANDOFF: Run an OOS backtest of a 0.15-delta condor in choppy_transition")
    assert ok and refined.startswith("Run an OOS backtest")


def test_parse_handoff_tolerates_fence_and_label():
    ok, refined = parse_handoff("```\nHANDOFF: change the IVR gate threshold\n```")
    assert ok and "IVR gate" in refined


def test_parse_handoff_normal_answer_is_not_handoff():
    ok, refined = parse_handoff("Your Aug-3 condor's short put is 722, ~1% below spot.")
    assert ok is False and refined == ""


def test_ask_returns_local_answer(monkeypatch):
    # stub the LLM so the test is offline + deterministic
    import alerts.smta_assistant as sa
    monkeypatch.setattr(sa, "build_context", lambda: "SPY: 729.46 | VIX: 19.6")
    monkeypatch.setattr("data.llm_client.call_llm",
                        lambda *a, **k: "The regime is choppy transition; sit tight.")
    out = ask("what's the market?")
    assert out["handoff"] is False
    assert "choppy" in out["reply"].lower()


def test_ask_routes_hard_question_to_handoff(monkeypatch):
    import alerts.smta_assistant as sa
    monkeypatch.setattr(sa, "build_context", lambda: "ctx")
    monkeypatch.setattr("data.llm_client.call_llm",
                        lambda *a, **k: "HANDOFF: Backtest a 7DTE 0.15-delta condor, OOS + haircut")
    out = ask("is a 7dte condor profitable in transition, backtested?")
    assert out["handoff"] is True
    assert "Backtest" in out["refined_prompt"]
    assert out["reply"] == ""


def test_ask_handles_dead_model(monkeypatch):
    import alerts.smta_assistant as sa
    monkeypatch.setattr(sa, "build_context", lambda: "ctx")
    monkeypatch.setattr("data.llm_client.call_llm", lambda *a, **k: "")
    out = ask("hi")
    assert out["handoff"] is False and "local model" in out["reply"].lower()

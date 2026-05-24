"""
tests/test_llm_client_caching.py -- Prompt caching + model_preference routing.

Four tests:
  1. Default call produces no cache_control in the system payload.
  2. cache_static_system=True wraps system in a list with ephemeral cache_control.
  3. model_preference='phi4_first' calls Ollama before Anthropic.
  4. phi4_first falls back to Anthropic (Sonnet) when Ollama raises.

All HTTP is mocked; no live endpoints are touched.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from data import llm_client


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def enable_fallback(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_FALLBACK_ENABLED", True)
    monkeypatch.setattr(config, "OLLAMA_HOST", "http://fake-nucbox:11434")
    monkeypatch.setattr(config, "OLLAMA_MODEL", "phi4:14b")


# ── Helpers ────────────────────────────────────────────────────────────────

class _OkAnthropicResp:
    def raise_for_status(self): pass
    def json(self):
        return {"content": [{"type": "text", "text": "sonnet reply"}]}


class _OkOllamaResp:
    def raise_for_status(self): pass
    def json(self):
        return {"message": {"content": "ollama reply"}}


# ── Tests ──────────────────────────────────────────────────────────────────

def test_call_llm_default_no_cache_control(monkeypatch):
    """Default call sends system as a plain string (no cache_control wrapper)."""
    captured_payloads = []

    def fake_post(url, **kw):
        captured_payloads.append(kw.get("json", {}))
        return _OkAnthropicResp()

    monkeypatch.setattr(llm_client.requests, "post", fake_post)

    result = llm_client.call_llm(
        system="My system prompt",
        user="My user prompt",
        anthropic_model="claude-sonnet-4-6",
        api_key="sk-fake",
        max_tokens=100,
    )

    assert result == "sonnet reply"
    assert len(captured_payloads) == 1
    payload = captured_payloads[0]
    # system should be a plain string, not a list
    assert isinstance(payload["system"], str)
    assert payload["system"] == "My system prompt"


def test_call_llm_cache_static_system_true_marks_system_cacheable(monkeypatch):
    """cache_static_system=True wraps system in ephemeral cache_control list."""
    captured_payloads = []

    def fake_post(url, **kw):
        captured_payloads.append(kw.get("json", {}))
        return _OkAnthropicResp()

    monkeypatch.setattr(llm_client.requests, "post", fake_post)

    result = llm_client.call_llm(
        system="My system prompt",
        user="My user prompt",
        anthropic_model="claude-sonnet-4-6",
        api_key="sk-fake",
        max_tokens=100,
        cache_static_system=True,
    )

    assert result == "sonnet reply"
    assert len(captured_payloads) == 1
    payload = captured_payloads[0]
    # system should be a list with ephemeral cache_control
    assert isinstance(payload["system"], list)
    assert len(payload["system"]) == 1
    block = payload["system"][0]
    assert block["type"] == "text"
    assert block["text"] == "My system prompt"
    assert block["cache_control"] == {"type": "ephemeral"}


def test_call_llm_routes_phi4_first_when_requested(monkeypatch):
    """model_preference='phi4_first' calls Ollama first, skipping Anthropic."""
    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        if "ollama" in url or "api/chat" in url:
            return _OkOllamaResp()
        return _OkAnthropicResp()

    monkeypatch.setattr(llm_client.requests, "post", fake_post)

    result = llm_client.call_llm(
        system="sys",
        user="user",
        anthropic_model="claude-sonnet-4-6",
        api_key="sk-fake",
        model_preference="phi4_first",
    )

    assert result == "ollama reply"
    # Anthropic should NOT have been called
    assert all("anthropic.com" not in u for u in calls), \
        f"Anthropic was called but should not have been: {calls}"
    # Ollama should have been called
    assert any("ollama" in u or "api/chat" in u for u in calls), \
        f"Ollama was not called: {calls}"


def test_call_llm_phi4_first_falls_back_on_failure(monkeypatch):
    """phi4_first: Ollama raises -> escalates to Anthropic (Sonnet)."""
    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        if "anthropic.com" in url:
            return _OkAnthropicResp()
        raise RuntimeError("Ollama offline")

    monkeypatch.setattr(llm_client.requests, "post", fake_post)

    result = llm_client.call_llm(
        system="sys",
        user="user",
        anthropic_model="claude-sonnet-4-6",
        api_key="sk-fake",
        model_preference="phi4_first",
    )

    assert result == "sonnet reply"
    # Anthropic must have been called as the fallback
    assert any("anthropic.com" in u for u in calls), \
        f"Anthropic fallback was not called: {calls}"

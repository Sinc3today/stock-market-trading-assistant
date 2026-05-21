"""
tests/test_llm_client.py -- Anthropic -> Ollama fallback logic.

All HTTP is mocked; no live endpoints are touched.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from data import llm_client


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")
    def json(self):
        return self._payload


def _anthropic_ok(text):
    return _Resp({"content": [{"type": "text", "text": text}]})


def _ollama_ok(text):
    return _Resp({"message": {"content": text}})


@pytest.fixture(autouse=True)
def enable_fallback(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_FALLBACK_ENABLED", True)
    monkeypatch.setattr(config, "OLLAMA_HOST", "http://fake-nucbox:11434")
    monkeypatch.setattr(config, "OLLAMA_MODEL", "phi4:14b")


def test_anthropic_success_skips_ollama(monkeypatch):
    calls = []
    def fake_post(url, **kw):
        calls.append(url)
        return _anthropic_ok("hosted reply")
    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    out = llm_client.call_llm("sys", "user", "claude-x", api_key="sk-real")
    assert out == "hosted reply"
    assert len(calls) == 1                 # only the Anthropic call
    assert "anthropic.com" in calls[0]


def test_falls_back_to_ollama_on_anthropic_error(monkeypatch):
    def fake_post(url, **kw):
        if "anthropic.com" in url:
            return _Resp({"error": "cap"}, status=400)
        return _ollama_ok("local reply")
    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    out = llm_client.call_llm("sys", "user", "claude-x", api_key="sk-real")
    assert out == "local reply"


def test_falls_back_when_anthropic_returns_empty(monkeypatch):
    def fake_post(url, **kw):
        if "anthropic.com" in url:
            return _anthropic_ok("")          # empty hosted reply
        return _ollama_ok("local reply")
    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    out = llm_client.call_llm("sys", "user", "claude-x", api_key="sk-real")
    assert out == "local reply"


def test_no_api_key_goes_straight_to_ollama(monkeypatch):
    calls = []
    def fake_post(url, **kw):
        calls.append(url)
        return _ollama_ok("local reply")
    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    out = llm_client.call_llm("sys", "user", "claude-x", api_key=None)
    assert out == "local reply"
    assert all("anthropic.com" not in u for u in calls)   # never tried hosted


def test_fallback_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_FALLBACK_ENABLED", False)
    def fake_post(url, **kw):
        return _Resp({"error": "cap"}, status=400)
    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    out = llm_client.call_llm("sys", "user", "claude-x", api_key="sk-real")
    assert out == ""


def test_both_backends_down_returns_empty(monkeypatch):
    def boom(url, **kw):
        raise RuntimeError("network down")
    monkeypatch.setattr(llm_client.requests, "post", boom)
    out = llm_client.call_llm("sys", "user", "claude-x", api_key="sk-real")
    assert out == ""


def test_ollama_think_blocks_are_stripped(monkeypatch):
    def fake_post(url, **kw):
        return _ollama_ok("<think>reasoning here</think>{\"summary\": \"ok\"}")
    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    out = llm_client.call_llm("sys", "user", "claude-x", api_key=None)
    assert "<think>" not in out
    assert '{"summary": "ok"}' in out

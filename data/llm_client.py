"""
data/llm_client.py -- Unified LLM caller with Anthropic -> local Ollama fallback.

Tries the hosted Anthropic API first; on ANY failure (monthly usage cap,
network error, empty reply) it falls back to the local Ollama stack on the
nucbox so the self-learning loop keeps producing reflections / briefs when
the hosted API is unavailable.

Why this exists: on 2026-05-20 the Anthropic account hit its monthly usage
cap (400 "regain access on 2026-06-01"), which silently killed the
reflector's KB generation. The bot is meant to learn autonomously, so it
shouldn't go dark for ~11 days waiting on a billing reset.

Endpoint + model are config-driven (OLLAMA_HOST, OLLAMA_MODEL), per the
global "default to the local LLM" convention. Set OLLAMA_FALLBACK_ENABLED
false to disable (tests set it false to stay hermetic).

Callers extract their own JSON from the returned text -- this module is
prompt-agnostic and just returns whatever text the model produced.
"""

from __future__ import annotations

import re

import requests
from loguru import logger

import config

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
# qwen3 and other reasoning models emit <think>...</think> blocks; strip them
# so downstream JSON extraction isn't confused by the chain-of-thought.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _call_anthropic(system: str, user: str, model: str, max_tokens: int,
                    api_key: str) -> str:
    resp = requests.post(
        ANTHROPIC_URL,
        headers={
            "Content-Type":      "application/json",
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
        },
        json={
            "model":      model,
            "max_tokens": max_tokens,
            "system":     system,
            "messages":   [{"role": "user", "content": user}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return "".join(
        b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
    )


def _call_ollama(system: str, user: str, max_tokens: int) -> str:
    resp = requests.post(
        f"{config.OLLAMA_HOST}/api/chat",
        json={
            "model":    config.OLLAMA_MODEL,
            "stream":   False,
            "think":    False,   # suppress reasoning-model <think> output
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "options":  {"num_predict": max_tokens},
        },
        timeout=180,   # local CPU/iGPU inference is slower than the hosted API
    )
    resp.raise_for_status()
    data = resp.json()
    text = (data.get("message") or {}).get("content", "") or ""
    return _THINK_RE.sub("", text).strip()


def call_llm(system: str, user: str, anthropic_model: str,
             api_key: str | None = None, max_tokens: int = 1500) -> str:
    """
    Return model text for (system, user). Tries Anthropic first when a key is
    given; on failure or empty reply, falls back to the local Ollama stack
    (unless config.OLLAMA_FALLBACK_ENABLED is false). Returns "" if every
    backend fails -- callers degrade gracefully on empty.
    """
    if api_key:
        try:
            text = _call_anthropic(system, user, anthropic_model, max_tokens, api_key)
            if text.strip():
                return text
            logger.warning("llm_client: Anthropic returned empty; trying local fallback")
        except Exception as e:
            logger.warning(f"llm_client: Anthropic call failed ({e}); trying local fallback")

    if not config.OLLAMA_FALLBACK_ENABLED:
        return ""

    try:
        text = _call_ollama(system, user, max_tokens)
        if text.strip():
            logger.info(f"llm_client: served by local Ollama ({config.OLLAMA_MODEL})")
            return text
        logger.warning("llm_client: Ollama returned empty")
    except Exception as e:
        logger.error(f"llm_client: Ollama fallback failed: {e}")
    return ""

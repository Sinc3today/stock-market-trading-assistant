"""tests/test_fred_resilience.py -- say what went wrong, and retry what's worth retrying.

16 FRED failures over three days, reported as:

    FRED observation error for UNRATE: HTTPError

That is the exception CLASS NAME and nothing else. The handler discards the
status code AND the body, and FRED puts a plain-English reason in the body
("Variable api_key is not set", "Too Many Requests"). So these are
indistinguishable from the log:

    400  bad or missing key  -> permanent; fix it now, retrying is pointless
    429  rate limited        -> back off and retry
    503  FRED is down        -> wait and retry

Same defect as the "add it to _CREDIT_STRATEGIES" ERROR fixed on 2026-09-09:
one message covering situations that need opposite responses.

There was also no retry at all, on a 10-second timeout, against a free public
API. 6 of the 16 failures were ReadTimeout — the kind a single retry usually
clears. PolygonClient has retried for months; this one never has.
"""
from __future__ import annotations

import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.fred_client import FREDClient


class _Resp:
    def __init__(self, status, body="", payload=None):
        self.status_code, self.text = status, body
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error", response=self)


_OK = {"observations": [{"date": "2026-08-01", "value": "4.1"},
                        {"date": "2026-07-01", "value": "4.0"}]}


def _client():
    c = FREDClient.__new__(FREDClient)
    c.api_key = "x" * 32
    c.BASE_URL = FREDClient.BASE_URL
    c._cache = {}
    c._BACKOFF_SEC = 0        # real backoff belongs in production, not the suite
    return c


def _patch(monkeypatch, responses):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params.get("series_id"))
        r = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(requests, "get", fake_get)
    return calls


def test_a_transient_timeout_is_retried_and_can_succeed(monkeypatch):
    calls = _patch(monkeypatch, [requests.ReadTimeout("slow"), _Resp(200, payload=_OK)])
    out = _client().get_latest_observation("UNRATE")
    assert out is not None and out["current_value"] == "4.1"
    assert len(calls) == 2


def test_a_429_is_retried(monkeypatch):
    calls = _patch(monkeypatch, [_Resp(429, "Too Many Requests"), _Resp(200, payload=_OK)])
    assert _client().get_latest_observation("UNRATE") is not None
    assert len(calls) == 2


def test_a_500_is_retried(monkeypatch):
    calls = _patch(monkeypatch, [_Resp(503, "unavailable"), _Resp(200, payload=_OK)])
    assert _client().get_latest_observation("UNRATE") is not None
    assert len(calls) == 2


def test_a_400_is_not_retried(monkeypatch):
    """A bad key will be just as bad on the third attempt. Retrying a
    permanent error wastes the rate budget that the 429 retry needs."""
    calls = _patch(monkeypatch, [_Resp(400, '{"error_message":"Variable api_key is not set."}')])
    assert _client().get_latest_observation("UNRATE") is None
    assert len(calls) == 1


def test_the_log_names_the_status_and_the_reason(monkeypatch, caplog):
    """400 and 503 must not read identically."""
    import logging
    from loguru import logger as _lg
    seen = []
    sink = _lg.add(lambda m: seen.append(str(m)), level="ERROR")
    try:
        _patch(monkeypatch, [_Resp(400, '{"error_message":"Variable api_key is not set."}')])
        _client().get_latest_observation("UNRATE")
    finally:
        _lg.remove(sink)
    blob = " ".join(seen)
    assert "400" in blob
    assert "api_key" in blob


def test_exhausted_retries_still_return_none_not_a_guess(monkeypatch):
    _patch(monkeypatch, [requests.ReadTimeout("slow")])
    assert _client().get_latest_observation("UNRATE") is None

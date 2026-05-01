"""
tests/test_pushover_live.py — LIVE Pushover smoke test (NOT a pytest file).

Sends a real Pushover notification to verify:
    1. PUSHOVER_USER_KEY / PUSHOVER_API_TOKEN are configured
    2. priority=1 and sound="cashregister" are accepted
    3. The clickable url + url_title render on the device
    4. The link points at the Cloudflare host where the per-alert web app
       will eventually live (config.PUSHOVER_BASE_URL)

Run:
    python tests/test_pushover_live.py

This script bypasses alerts.pushover_client.PushoverClient.send() because that
method does not currently accept a `sound` parameter — we POST directly to the
Pushover REST API so the exact payload requested is exercised end-to-end.
"""

from __future__ import annotations

import json
import os
import sys

# Make project root importable when this file is run as a standalone script
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import requests

import config


PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"


def main() -> int:
    # ── 1. Credentials ────────────────────────────────────────
    if not config.PUSHOVER_USER_KEY or not config.PUSHOVER_API_TOKEN:
        print("ERROR: PUSHOVER_USER_KEY and/or PUSHOVER_API_TOKEN missing from .env")
        return 1

    # ── 2. Build link to the per-alert web app ────────────────
    base = (config.PUSHOVER_BASE_URL or "").rstrip("/")
    if not base or "yourdomain.com" in base:
        print("WARNING: PUSHOVER_BASE_URL is unset or still a placeholder -- "
              "the link in the notification will not resolve to a real host.")
        base = base or "https://yourdomain.com"
    url = f"{base}/alerts/test-001"

    # ── 3. Payload ────────────────────────────────────────────
    payload = {
        "token":     config.PUSHOVER_API_TOKEN,
        "user":      config.PUSHOVER_USER_KEY,
        "title":     "SPY Daily Play -- Live Test",
        "message":   "IRON CONDOR | VIX 14.2 | ADX 28 | IVR 31 | "
                     "Regime: CHOPPY_LOW_VOL | Conf: 85%",
        "url":        url,
        "url_title":  "View Trade + Chat",
        "priority":   1,
        "sound":      "cashregister",
    }

    print(f"Sending Pushover notification to user_key={config.PUSHOVER_USER_KEY[:4]}...")
    print(f"  Link -> {url}")

    # ── 4. Send ──────────────────────────────────────────────
    try:
        resp = requests.post(PUSHOVER_API_URL, data=payload, timeout=10)
    except requests.RequestException as e:
        print(f"ERROR: HTTP request failed: {e}")
        return 1

    # ── 5. Report ────────────────────────────────────────────
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text}

    if resp.status_code == 200 and body.get("status") == 1:
        print("SUCCESS -- Pushover accepted the message.")
        print(f"  request_id: {body.get('request')}")
        print("  Check your phone -- the notification should arrive within a few seconds.")
        return 0

    print(f"FAILURE -- HTTP {resp.status_code}")
    print("Full API response:")
    print(json.dumps(body, indent=2))
    return 1


if __name__ == "__main__":
    sys.exit(main())

"""
test_alert_pipeline.py — Fire a fake alert through the full pipeline.

Tests:
  1. Pushover notification lands on your phone
  2. Link opens https://alerts.nexus-lab.work/alert/<id>
  3. Full trade card renders correctly
  4. Claude chat tab works

Run from project root (with venv active):
    python test_alert_pipeline.py
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from alerts.pushover_client import PushoverClient
from alerts.notifier import Notifier

# ── Fake alert ────────────────────────────────────────────────────────────────
alert = {
    "ticker":               "AAPL",
    "direction":            "BULLISH",
    "mode":                 "SWING",
    "tier":                 "high_conviction",
    "final_score":          82,
    "entry":                192.50,
    "stop":                 188.00,
    "target":               202.00,
    "rr_ratio":             2.1,
    "rsi":                  58.4,
    "rvol":                 1.8,
    "ma20":                 190.0,
    "ma50":                 185.0,
    "ma200":                175.0,
    "setup_tags":           [
        "✅ Break above 20-day high",
        "✅ Volume spike 1.8x average",
        "✅ RSI momentum confirm",
    ],
    "layer_scores":         {
        "trend":  {"score": 28, "max": 35},
        "setup":  {"score": 30, "max": 35},
        "volume": {"score": 24, "max": 30},
    },
    "confluence":           True,
    "confluence_timeframes": ["Daily", "4H"],
    "exit_type":            "Target or trailing stop",
    "timestamp":            "2026-04-29 09:15 AM ET",
}

discord_message = """\
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 **AAPL** — BULLISH SWING  [HIGH CONVICTION]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
**Score:** 82/100  |  **R/R:** 2.1:1

**Entry:** $192.50  →  **Stop:** $188.00  →  **Target:** $202.00

**Setup Triggers:**
✅ Break above 20-day high
✅ Volume spike 1.8x average
✅ RSI momentum confirm

**Indicators:** RSI 58.4  |  RVOL 1.8x
**Confluence:** Daily + 4H aligned
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# ── Send through full pipeline ────────────────────────────────────────────────
def main():
    pushover = PushoverClient()

    notifier = Notifier(
        pushover           = pushover,
        discord_alert_fn   = None,   # skip Discord for this test
        discord_message_fn = None,
    )

    print("Firing test alert through pipeline...")
    notifier.alert(alert, discord_message)

    alert_id = alert.get("alert_id", "unknown")
    print(f"\n✅ Done!")
    print(f"   Alert ID : {alert_id}")
    print(f"   Detail page: https://alerts.nexus-lab.work/alert/{alert_id}")
    print(f"\nCheck your phone for the Pushover notification.")
    print(f"Tap the link to test the full trade card + Claude chat.")


if __name__ == "__main__":
    main()

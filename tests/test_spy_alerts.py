"""
tests/test_spy_alerts.py

Diagnostic test for the SPY options engine.
Uses Polygon (already in your stack) instead of yfinance.

Usage:
    python tests/test_spy_alerts.py

Expected: 1-3 setups depending on current market conditions.
  - Bullish day  → Call Debit Spread fires
  - Bearish day  → Put Debit Spread fires
  - Sideways day → Iron Condor fires
  - Strong trend → Both a directional spread AND maybe IC fire

If ZERO setups fire, the script shows raw scores so you
can see exactly how close each strategy is to the threshold.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from data.polygon_client import PolygonClient
from signals.spy_options_engine import (
    SPYOptionsEngine,
    _score_call_spread,
    _score_put_spread,
    _score_iron_condor,
    _extract_context,
)


def run():
    print("\n" + "=" * 58)
    print("  SPY OPTIONS ENGINE — DIAGNOSTIC TEST")
    print(f"  Standard alert:     score >= {config.SCORE_ALERT_MINIMUM}")
    print(f"  High conviction:    score >= {config.SCORE_HIGH_CONVICTION}")
    print("=" * 58)

    client = PolygonClient()

    print("\n⏳ Fetching SPY daily data from Polygon...")
    df_daily = client.get_bars("SPY", timeframe="day", limit=60, days_back=90)
    if df_daily is None or len(df_daily) < 30:
        print("❌ Could not fetch SPY daily data.")
        print("   Check POLYGON_API_KEY in your .env file.")
        return

    print("⏳ Fetching SPY 15m intraday data...")
    df_intraday = client.get_bars("SPY", timeframe="15min", limit=100, days_back=5)

    price = float(df_daily["close"].iloc[-1])
    print(f"\n✅ SPY daily bars:    {len(df_daily)}")
    print(f"✅ SPY 15m bars:      {len(df_intraday) if df_intraday is not None else 0}")
    print(f"✅ SPY current price: ${price:.2f}")

    # Show raw scores first so you always see what the engine sees
    ctx = _extract_context(df_daily)
    cs, _ = _score_call_spread(ctx)
    ps, _ = _score_put_spread(ctx)
    ics, _ = _score_iron_condor(ctx)

    print(f"\n{'─'*58}")
    print(f"  RAW SCORES (before confluence bonus)")
    print(f"{'─'*58}")
    print(f"  📈 Call Debit Spread:  {cs:3d}/100  ", end="")
    print("✅ FIRES" if cs >= config.SCORE_ALERT_MINIMUM else f"❌ needs {config.SCORE_ALERT_MINIMUM}")
    print(f"  📉 Put Debit Spread:   {ps:3d}/100  ", end="")
    print("✅ FIRES" if ps >= config.SCORE_ALERT_MINIMUM else f"❌ needs {config.SCORE_ALERT_MINIMUM}")
    print(f"  🦅 Iron Condor:        {ics:3d}/100  ", end="")
    print("✅ FIRES" if ics >= config.SCORE_ALERT_MINIMUM else f"❌ needs {config.SCORE_ALERT_MINIMUM}")
    print(f"{'─'*58}")

    # Now run the full engine
    engine = SPYOptionsEngine()
    setups = engine.analyze(df_daily, df_intraday)

    print(f"\n{'=' * 58}")
    print(f"  ALERT RESULTS: {len(setups)} setup(s) firing")
    print(f"{'=' * 58}")

    if not setups:
        print("\n  No setups above threshold today.")
        print(f"  Highest score: {max(cs, ps, ics)}/100")
        if max(cs, ps, ics) >= 35:
            print(f"\n  ⚠️  Scores are close but below threshold ({config.SCORE_ALERT_MINIMUM}).")
            print(f"  You can lower SCORE_ALERT_MINIMUM in config.py to 35")
            print(f"  to confirm the engine works, then raise it back.")
        return

    for setup in setups:
        icon = "🔥" if setup.conviction == "high" else "📌"
        strat = setup.strategy.upper().replace("_", " ")
        print(f"\n{icon} {strat}")
        print(f"   Conviction:  {setup.conviction.upper()}")
        print(f"   Score:       {setup.score}/100")
        print(f"   Timeframe:   {setup.timeframe}")
        print(f"   Direction:   {setup.direction}")
        print(f"\n   Why it fired:")
        for r in setup.reasons:
            print(f"     • {r}")

        if setup.strategy in ("call_debit_spread", "put_debit_spread"):
            opt = "CALL" if setup.strategy == "call_debit_spread" else "PUT"
            print(f"\n   Estimated Legs:")
            print(f"     BUY  {opt} ${setup.long_strike:.0f}")
            print(f"     SELL {opt} ${setup.short_strike:.0f}")
            print(f"     Est. debit:   ~${setup.est_debit:.2f}/share")
            print(f"     Max profit:   ~${setup.max_profit:.0f}/contract")
            print(f"     Max loss:     ~${setup.max_loss:.0f}/contract")
            print(f"     Spread R/R:   ~{setup.spread_rr:.1f}:1")

        elif setup.strategy == "iron_condor":
            print(f"\n   Estimated Legs:")
            print(f"     BUY  PUT  ${setup.ic_put_long:.0f}")
            print(f"     SELL PUT  ${setup.ic_put_short:.0f}")
            print(f"     SELL CALL ${setup.ic_call_short:.0f}")
            print(f"     BUY  CALL ${setup.ic_call_long:.0f}")
            print(f"     Profit zone:  {setup.ic_profit_zone}")
            print(f"     Est. credit:  ~${setup.ic_credit:.2f}/share per side")

    print(f"\n{'─'*58}")
    print("  ✅ Discord message preview for top setup:\n")
    print(setups[0].to_discord_msg())
    print(f"{'=' * 58}\n")


if __name__ == "__main__":
    run()

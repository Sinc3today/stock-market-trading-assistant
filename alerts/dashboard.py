"""
alerts/dashboard.py — Streamlit Dashboard
Web UI for monitoring alerts, managing watchlist, and reviewing journal.

Run with:
    streamlit run alerts/dashboard.py

Opens at: http://localhost:8501
"""

import json
import os
import sys
from datetime import datetime

import pandas as pd
import streamlit as st
import plotly.graph_objects as go

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import config

# ─────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────

st.set_page_config(
    page_title="Trading Assistant",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────

def load_watchlist() -> dict:
    try:
        with open(config.WATCHLIST_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {"swing": [], "intraday": [], "options_enabled": []}


def save_watchlist(watchlist: dict):
    with open(config.WATCHLIST_PATH, "w") as f:
        json.dump(watchlist, f, indent=2)


def load_alerts() -> list:
    """Load alerts from the journal log file."""
    log_path = os.path.join(config.LOG_DIR, "alerts.json")
    if not os.path.exists(log_path):
        return []
    try:
        with open(log_path, "r") as f:
            return json.load(f)
    except Exception:
        return []


def tier_color(tier: str) -> str:
    return {
        "high_conviction": "🔴",
        "standard":        "🟡",
        "watchlist":       "⚪",
    }.get(tier, "⚪")


def score_color(score: int) -> str:
    if score >= 90:
        return "#ff4444"
    elif score >= 75:
        return "#ffaa00"
    elif score >= 60:
        return "#4488ff"
    else:
        return "#888888"


# ─────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────

def render_sidebar():
    st.sidebar.image("https://img.icons8.com/emoji/96/chart-increasing-emoji.png", width=60)
    st.sidebar.title("Trading Assistant")
    st.sidebar.caption(f"Last refresh: {datetime.now().strftime('%I:%M %p')}")

    page = st.sidebar.radio(
        "Navigate",
        ["📡 Live Alerts", "📰 News", "📋 Watchlist", "📓 Journal", "📊 Performance", "📒 Trade Recorder", "🧠 Lessons", "🤖 AI Advisor", "⚙️ Settings"],
        label_visibility="collapsed"
    )

    st.sidebar.divider()

    # Quick score check
    st.sidebar.subheader("Quick Score Check")
    quick_ticker = st.sidebar.text_input("Ticker", placeholder="AAPL").upper()
    quick_mode   = st.sidebar.selectbox("Mode", ["swing", "intraday"])

    if st.sidebar.button("Run Score", use_container_width=True):
        if quick_ticker:
            with st.spinner(f"Scoring {quick_ticker}..."):
                result = run_quick_score(quick_ticker, quick_mode)
                if result:
                    st.sidebar.metric(
                        label=f"{quick_ticker} Score",
                        value=f"{result['final_score']}/100",
                        delta=result["direction"].upper()
                    )
                    st.sidebar.caption(f"Tier: {result['tier'].replace('_', ' ').title()}")
                else:
                    st.sidebar.error(f"Could not score {quick_ticker}")

    return page


def run_quick_score(ticker: str, mode: str) -> dict | None:
    """Run a live score check for the sidebar quick-check."""
    try:
        from data.polygon_client import PolygonClient
        from indicators.moving_averages import MovingAverages
        from indicators.donchian import DonchianChannels
        from indicators.volume import VolumeAnalysis
        from indicators.cvd import CVDAnalysis
        from indicators.rsi import RSIAnalysis
        from signals.scorer import SignalScorer

        timeframe = config.SWING_PRIMARY_TIMEFRAME if mode == "swing" \
                    else config.INTRADAY_PRIMARY_TIMEFRAME

        client = PolygonClient()
        df = client.get_bars(ticker, timeframe=timeframe, limit=300, days_back=400)
        if df is None or len(df) < 50:
            return None

        result = SignalScorer().score(
            MovingAverages(df).analyze(),
            DonchianChannels(df).analyze(),
            VolumeAnalysis(df).analyze(),
            CVDAnalysis(df).analyze(),
            RSIAnalysis(df).analyze(),
        )
        return result
    except Exception as e:
        st.sidebar.error(str(e))
        return None


# ─────────────────────────────────────────
# PAGES
# ─────────────────────────────────────────

def render_live_alerts():
    st.title("📡 Live Alerts")

    alerts = load_alerts()

    if not alerts:
        st.info("No alerts yet. The scanner will populate this when it runs.")
        st.caption("Make sure the scanner is running: `python main.py`")
        return

    # Summary metrics
    today = datetime.now().strftime("%Y-%m-%d")
    today_alerts = [a for a in alerts if today in a.get("timestamp", "")]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Alerts Today",       len(today_alerts))
    col2.metric("High Conviction 🔴", sum(1 for a in today_alerts if a.get("tier") == "high_conviction"))
    col3.metric("Standard 🟡",        sum(1 for a in today_alerts if a.get("tier") == "standard"))
    col4.metric("Avg Score",          f"{sum(a.get('final_score',0) for a in today_alerts) // max(len(today_alerts),1)}/100")

    st.divider()

    # Filter controls
    col_f1, col_f2, col_f3 = st.columns(3)
    filter_tier = col_f1.selectbox("Filter by Tier", ["All", "High Conviction", "Standard"])
    filter_dir  = col_f2.selectbox("Filter by Direction", ["All", "Bullish", "Bearish"])
    filter_mode = col_f3.selectbox("Filter by Mode", ["All", "Swing", "Intraday"])

    filtered = alerts[-50:]  # Show last 50
    if filter_tier != "All":
        filtered = [a for a in filtered if a.get("tier","").replace("_"," ").title() == filter_tier]
    if filter_dir != "All":
        filtered = [a for a in filtered if a.get("direction","").upper() == filter_dir.upper()]
    if filter_mode != "All":
        filtered = [a for a in filtered if a.get("mode","").lower() == filter_mode.lower()]

    # Render each alert card
    for alert in reversed(filtered):
        render_alert_card(alert)


def render_alert_card(alert: dict):
    """Render a single alert as an expandable card."""
    emoji     = alert.get("emoji", "⚪")
    ticker    = alert.get("ticker", "—")
    score     = alert.get("final_score", 0)
    direction = alert.get("direction", "—")
    timestamp = alert.get("timestamp", "—")
    tier      = alert.get("tier", "none")
    mode      = alert.get("mode", "—")

    dir_arrow = "📈" if direction == "BULLISH" else "📉"
    color     = score_color(score)

    with st.expander(
        f"{emoji} **{ticker}** — Score: {score}/100 | {direction} {dir_arrow} | {mode} | {timestamp}",
        expanded=(tier == "high_conviction")
    ):
        col1, col2, col3 = st.columns(3)

        # Trade levels
        col1.subheader("📍 Trade Levels")
        col1.write(f"**Entry:**  ${alert.get('entry', '—')}")
        col1.write(f"**Stop:**   ${alert.get('stop', '—')}")
        col1.write(f"**Target:** ${alert.get('target', '—')}")
        col1.write(f"**R/R:**    {alert.get('rr_ratio', '—')} : 1")
        col1.write(f"**Exit:**   {alert.get('exit_type', '—')}")

        # Score breakdown
        col2.subheader("📊 Score Breakdown")
        layers = alert.get("layer_scores", {})
        for layer_name, layer_data in layers.items():
            s   = layer_data.get("score", 0)
            mx  = layer_data.get("max", 0)
            pct = int((s / mx * 100)) if mx > 0 else 0
            col2.write(f"**{layer_name.title()}:** {s}/{mx}")
            col2.progress(pct / 100)

        # Indicator snapshot
        col3.subheader("📈 Indicators")
        col3.write(f"**RSI:**   {alert.get('rsi', '—')}")
        col3.write(f"**RVOL:**  {alert.get('rvol', '—')}x")
        col3.write(f"**CVD:**   {alert.get('cvd_slope', '—')}")
        col3.write(f"**MA20:**  ${alert.get('ma20', '—')}")
        col3.write(f"**MA50:**  ${alert.get('ma50', '—')}")
        col3.write(f"**MA200:** ${alert.get('ma200', '—')}")

        # Setup tags
        tags = alert.get("setup_tags", [])
        if tags:
            st.write("**Setup Triggers:**")
            for tag in tags:
                st.write(f"  {tag}")

        # Confluence note
        if alert.get("confluence"):
            tfs = " + ".join(alert.get("confluence_timeframes", []))
            st.success(f"⚡ Confluence confirmed on: {tfs}")


def _validate_ticker(ticker: str) -> tuple[bool, str]:
    """
    Validate a ticker symbol before adding to watchlist.
    Checks format then verifies it exists via Polygon.
    Returns (is_valid, error_message)
    """
    if not ticker or len(ticker.strip()) == 0:
        return False, "Please enter a ticker symbol"
    ticker = ticker.upper().strip()
    if len(ticker) > 5:
        return False, f"`{ticker}` — ticker symbols are 1-5 characters"
    if not ticker.isalpha():
        return False, f"`{ticker}` — ticker should only contain letters"
    # Verify ticker exists via Polygon
    try:
        from data.polygon_client import PolygonClient
        price = PolygonClient().get_latest_price(ticker)
        if price is None:
            return False, f"❌ `{ticker}` not found — check the symbol and try again"
        return True, f"${price:.2f}"
    except Exception:
        return True, ""  # If API fails, allow it through


def render_watchlist():
    st.title("📋 Watchlist Manager")

    watchlist = load_watchlist()
    changed   = False

    col1, col2 = st.columns(2)

    # ── Swing Watchlist ──────────────────
    with col1:
        st.subheader("📅 Swing Watchlist")
        swing_tickers = watchlist.get("swing", [])

        for ticker in swing_tickers:
            c1, c2 = st.columns([4, 1])
            c1.write(f"• **{ticker}**")
            if c2.button("✕", key=f"rm_swing_{ticker}"):
                watchlist["swing"].remove(ticker)
                changed = True

        st.divider()
        # st.form submits on Enter key press
        with st.form(key="add_swing_form", clear_on_submit=True):
            new_swing = st.text_input(
                "Add to Swing",
                placeholder="Type ticker + press Enter or click Add"
            ).upper().strip()
            submitted = st.form_submit_button("➕ Add to Swing", use_container_width=True)
            if submitted:
                valid, err = _validate_ticker(new_swing)
                if not valid:
                    st.error(err)
                elif new_swing in watchlist["swing"]:
                    st.warning(f"`{new_swing}` is already in the swing watchlist")
                else:
                    watchlist["swing"].append(new_swing)
                    changed = True
                    price_str = f" (${valid})" if valid and valid.startswith("$") else ""
                    st.success(f"✅ Added `{new_swing}` to swing watchlist{price_str}")

    # ── Intraday Watchlist ───────────────
    with col2:
        st.subheader("⚡ Intraday Watchlist")
        intraday_tickers = watchlist.get("intraday", [])

        for ticker in intraday_tickers:
            c1, c2 = st.columns([4, 1])
            c1.write(f"• **{ticker}**")
            if c2.button("✕", key=f"rm_intraday_{ticker}"):
                watchlist["intraday"].remove(ticker)
                changed = True

        st.divider()
        with st.form(key="add_intraday_form", clear_on_submit=True):
            new_intraday = st.text_input(
                "Add to Intraday",
                placeholder="Type ticker + press Enter or click Add"
            ).upper().strip()
            submitted2 = st.form_submit_button("➕ Add to Intraday", use_container_width=True)
            if submitted2:
                valid, err = _validate_ticker(new_intraday)
                if not valid:
                    st.error(err)
                elif new_intraday in watchlist["intraday"]:
                    st.warning(f"`{new_intraday}` is already in the intraday watchlist")
                else:
                    watchlist["intraday"].append(new_intraday)
                    changed = True
                    price_str = f" (${valid})" if valid and valid.startswith("$") else ""
                    st.success(f"✅ Added `{new_intraday}` to intraday watchlist{price_str}")

    if changed:
        save_watchlist(watchlist)
        st.rerun()


def render_journal():
    st.title("📓 Trade Journal")

    alerts = load_alerts()
    if not alerts:
        st.info("No trades logged yet.")
        return

    df = pd.DataFrame(alerts)

    if "final_score" not in df.columns:
        st.warning("Journal data format incomplete.")
        return

    # Summary stats
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Alerts",  len(df))
    col2.metric("Avg Score",     f"{df['final_score'].mean():.0f}/100")
    col3.metric("Bullish",       len(df[df["direction"] == "BULLISH"]))
    col4.metric("Bearish",       len(df[df["direction"] == "BEARISH"]))

    st.divider()

    # Score distribution chart
    st.subheader("Score Distribution")
    fig = go.Figure(data=[
        go.Histogram(
            x=df["final_score"],
            nbinsx=20,
            marker_color="#4488ff",
            opacity=0.8,
        )
    ])
    fig.update_layout(
        xaxis_title="Confidence Score",
        yaxis_title="Count",
        showlegend=False,
        height=300,
        margin=dict(l=0, r=0, t=20, b=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    fig.add_vline(x=75, line_dash="dash", line_color="gold",   annotation_text="Standard")
    fig.add_vline(x=90, line_dash="dash", line_color="red",    annotation_text="High Conviction")
    st.plotly_chart(fig, use_container_width=True)

    # Raw table
    st.subheader("Alert History")
    display_cols = ["timestamp", "ticker", "direction", "final_score",
                    "tier", "mode", "entry", "stop", "target", "rr_ratio"]
    available = [c for c in display_cols if c in df.columns]
    st.dataframe(
        df[available].sort_values("timestamp", ascending=False),
        use_container_width=True,
        hide_index=True,
    )


def render_settings():
    st.title("⚙️ Settings")

    st.subheader("Alert Thresholds")
    col1, col2 = st.columns(2)
    col1.metric("Minimum Alert Score",    config.SCORE_ALERT_MINIMUM)
    col1.metric("High Conviction Score",  config.SCORE_HIGH_CONVICTION)
    col2.metric("Min Risk/Reward",        f"{config.MIN_RISK_REWARD_RATIO}:1")
    col2.metric("Earnings Block (days)",  config.EARNINGS_BLOCK_DAYS)

    st.subheader("Indicator Settings")
    col3, col4 = st.columns(2)
    col3.metric("MA Short",   config.MA_SHORT)
    col3.metric("MA Mid",     config.MA_MID)
    col3.metric("MA Long",    config.MA_LONG)
    col4.metric("RSI Period", config.RSI_PERIOD)
    col4.metric("Donchian Period", config.DONCHIAN_PERIOD)
    col4.metric("Volume Spike Multiplier", f"{config.VOLUME_SPIKE_MULTIPLIER}x")

    st.info("To change these values, edit `config.py` and restart the dashboard.")

    st.subheader("Environment")
    st.code(f"Environment: {config.ENVIRONMENT}\nLog Level: {config.LOG_LEVEL}")


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

def render_performance():
    st.title("📊 Performance")

    from journal.performance import PerformanceTracker
    from journal.trade_recorder import TradeRecorder

    stats      = PerformanceTracker().calculate()
    tr_stats   = TradeRecorder().get_summary_stats()

    # Use trade recorder stats as primary source — richer and always populated
    has_trades  = tr_stats["total"] > 0
    has_closed  = tr_stats["closed"] > 0
    has_alerts  = stats["total_alerts"] > 0

    if not has_trades and not has_alerts:
        st.info("No trade data yet. Log trades in 📒 Trade Recorder to see performance stats.")
        return

    # ── Summary metrics — combines both sources ───────────────
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total Trades",  tr_stats["total"])
    col2.metric("Closed",        tr_stats["closed"])
    col3.metric("Open",          tr_stats["open"])
    col4.metric("Win Rate",      f"{tr_stats['win_rate']}%")
    col5.metric("Total P&L",     f"${tr_stats['total_pnl']}")

    col6, col7, col8 = st.columns(3)
    col6.metric("Wins",          tr_stats["wins"])
    col7.metric("Losses",        tr_stats["losses"])
    col8.metric("Avg P&L %",     f"{tr_stats['avg_pnl_pct']}%")

    # Scanner alert stats if available
    if has_alerts:
        st.divider()
        st.subheader("📡 Scanner Alert Stats")
        col_a1, col_a2, col_a3 = st.columns(3)
        col_a1.metric("Alerts Fired",  stats["total_alerts"])
        col_a2.metric("Avg Score",     f"{stats['avg_score']}/100")
        col_a3.metric("Avg R/R",       f"{stats['avg_rr_ratio']}:1")

    st.divider()

    # ── Score accuracy table ──────────────────────────────────
    if stats["score_accuracy"]:
        st.subheader("🎯 Score Accuracy — Does Higher Score = More Wins?")
        st.caption("This validates the scoring model. Higher score buckets should show higher win rates over time.")

        acc_data = []
        for bucket, data in stats["score_accuracy"].items():
            acc_data.append({
                "Score Range": bucket,
                "Trades":      data["total"],
                "Wins":        data["wins"],
                "Win Rate":    f"{data['win_rate']}%",
            })
        st.dataframe(acc_data, use_container_width=True, hide_index=True)
        st.divider()

    # ── By tier ──────────────────────────────────────────────
    col_a, col_b = st.columns(2)

    with col_a:
        if stats["by_tier"]:
            st.subheader("By Alert Tier")
            tier_data = []
            for tier, data in stats["by_tier"].items():
                tier_data.append({
                    "Tier":     tier.replace("_", " ").title(),
                    "Trades":   data["total"],
                    "Win Rate": f"{data['win_rate']}%",
                    "Avg P&L":  f"{data['avg_pnl']}%",
                })
            st.dataframe(tier_data, use_container_width=True, hide_index=True)

    with col_b:
        if stats["by_mode"]:
            st.subheader("By Mode")
            mode_data = []
            for mode, data in stats["by_mode"].items():
                mode_data.append({
                    "Mode":     mode.title(),
                    "Trades":   data["total"],
                    "Win Rate": f"{data['win_rate']}%",
                    "Avg P&L":  f"{data['avg_pnl']}%",
                })
            st.dataframe(mode_data, use_container_width=True, hide_index=True)

    st.divider()

    # ── Outcome logger ────────────────────────────────────────
    st.subheader("📝 Log Trade Outcome")
    st.caption("Mark a trade as won, lost, or breakeven after it closes.")

    from journal.trade_logger import TradeLogger
    open_trades = TradeLogger().get_open_trades()

    if not open_trades:
        st.info("No open trades to log outcomes for.")
        return

    trade_options = {
        f"{t['ticker']} — {t['timestamp']} — Score: {t.get('final_score')}/100": t
        for t in reversed(open_trades)
    }

    selected_label = st.selectbox("Select Trade", list(trade_options.keys()))
    selected_trade = trade_options[selected_label]

    col_o1, col_o2, col_o3 = st.columns(3)
    outcome    = col_o1.selectbox("Outcome", ["win", "loss", "breakeven"])
    exit_price = col_o2.number_input("Exit Price", min_value=0.0, step=0.01)
    notes      = col_o3.text_input("Notes (optional)")

    if st.button("💾 Save Outcome", use_container_width=True):
        tl = TradeLogger()
        success = tl.mark_outcome(
            ticker=selected_trade["ticker"],
            timestamp=selected_trade["timestamp"],
            outcome=outcome,
            exit_price=exit_price if exit_price > 0 else None,
            notes=notes,
        )
        if success:
            st.success(f"✅ Outcome saved: {selected_trade['ticker']} → {outcome}")
            st.rerun()
        else:
            st.error("Could not find trade to update.")


# ─────────────────────────────────────────
# TRADE RECORDER PAGE (added Session 9)
# ─────────────────────────────────────────

def render_trade_recorder():
    st.title("📒 Trade Recorder")

    from journal.trade_recorder import TradeRecorder
    tr = TradeRecorder()

    # ── Summary bar ──────────────────────────────────────────
    stats = tr.get_summary_stats()
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total Trades",  stats["total"])
    col2.metric("Open",          stats["open"])
    col3.metric("Win Rate",      f"{stats['win_rate']}%")
    col4.metric("Total P&L",     f"${stats['total_pnl']}")
    col5.metric("Avg P&L %",     f"{stats['avg_pnl_pct']}%")

    st.divider()

    tab1, tab2, tab3 = st.tabs(["📥 Log Entry", "📤 Log Exit", "📋 All Trades"])

    # ── TAB 1: Log Entry ─────────────────────────────────────
    with tab1:
        st.subheader("Log a New Trade Entry")

        col_a, col_b = st.columns(2)
        ticker       = col_a.text_input("Ticker", placeholder="AAPL").upper()
        trade_type   = col_b.selectbox("Type", ["stock", "options"])

        col_c, col_d, col_e = st.columns(3)
        entry_price  = col_c.number_input("Entry Price ($)", min_value=0.0, step=0.01)
        size         = col_d.number_input(
            "Size (shares or contracts)", min_value=0.0, step=1.0
        )
        direction    = col_e.selectbox("Direction", ["bullish", "bearish"])

        col_f, col_g = st.columns(2)
        mode         = col_f.selectbox("Mode", ["swing", "intraday"])

        # Link to alert
        st.caption("Optional — link to a system alert")
        open_alerts = []
        try:
            from journal.trade_logger import TradeLogger
            open_alerts = TradeLogger().get_today_alerts()
        except Exception:
            pass

        alert_options = {"None — manual trade": None}
        for a in open_alerts:
            label = f"{a.get('ticker')} — {a.get('timestamp')} — Score: {a.get('final_score')}"
            alert_options[label] = a

        selected_alert_label = st.selectbox(
            "Link to Alert (optional)", list(alert_options.keys())
        )
        selected_alert = alert_options[selected_alert_label]

        # Options fields
        option_type = strike = expiry = None
        if trade_type == "options":
            st.caption("Options Details")
            col_h, col_i, col_j = st.columns(3)
            option_type = col_h.selectbox("Option Type", ["CALL", "PUT"])
            strike      = col_i.number_input("Strike ($)", min_value=0.0, step=0.5)
            expiry      = col_j.text_input("Expiry (YYYY-MM-DD)", placeholder="2024-03-15")

        notes = st.text_area("Entry Notes", placeholder="Why are you taking this trade?")

        if st.button("📥 Log Entry", use_container_width=True):
            if not ticker:
                st.error("Please enter a ticker symbol")
            elif entry_price <= 0:
                st.error("Please enter a valid entry price")
            elif size <= 0:
                st.error("Please enter a valid size")
            else:
                trade_id = tr.log_entry(
                    ticker=ticker,
                    entry_price=entry_price,
                    size=size,
                    trade_type=trade_type,
                    direction=direction,
                    mode=mode,
                    alert_timestamp=selected_alert.get("timestamp") if selected_alert else None,
                    alert_score=selected_alert.get("final_score") if selected_alert else None,
                    option_type=option_type,
                    strike=strike if strike and strike > 0 else None,
                    expiry=expiry if expiry else None,
                    notes=notes,
                )
                st.success(f"✅ Trade logged! ID: **{trade_id}**")
                st.info("Save this ID to log your exit later.")
                st.rerun()

    # ── TAB 2: Log Exit ──────────────────────────────────────
    with tab2:
        st.subheader("Log a Trade Exit")

        open_trades = tr.get_open_trades()

        if not open_trades:
            st.info("No open trades to close.")
        else:
            trade_options = {
                f"[{t['trade_id']}] {t['ticker']} "
                f"@ ${t['entry_price']} — {t['entry_date']}": t
                for t in reversed(open_trades)
            }

            selected_label = st.selectbox(
                "Select Open Trade", list(trade_options.keys())
            )
            selected_trade = trade_options[selected_label]

            # Show trade summary
            col_s1, col_s2, col_s3 = st.columns(3)
            col_s1.metric("Entry Price", f"${selected_trade['entry_price']}")
            col_s2.metric("Size",        selected_trade["size"])
            col_s3.metric("Direction",   selected_trade["direction"])

            col_ex1, col_ex2 = st.columns(2)
            exit_price  = col_ex1.number_input(
                "Exit Price ($)", min_value=0.0, step=0.01
            )
            exit_notes  = col_ex2.text_input(
                "Exit Notes", placeholder="Why did you exit?"
            )

            # Live P&L preview
            if exit_price > 0:
                ep    = selected_trade["entry_price"]
                size  = selected_trade["size"]
                if selected_trade["direction"] == "BULLISH":
                    pnl_d = (exit_price - ep) * size
                    pnl_p = ((exit_price - ep) / ep) * 100
                else:
                    pnl_d = (ep - exit_price) * size
                    pnl_p = ((ep - exit_price) / ep) * 100

                pnl_color = "green" if pnl_d >= 0 else "red"
                st.markdown(
                    f"**Estimated P&L:** "
                    f"<span style='color:{pnl_color}'>"
                    f"${pnl_d:.2f} ({pnl_p:.1f}%)"
                    f"</span>",
                    unsafe_allow_html=True
                )

            if st.button("📤 Log Exit", use_container_width=True):
                if exit_price <= 0:
                    st.error("Please enter a valid exit price")
                else:
                    success = tr.log_exit(
                        selected_trade["trade_id"],
                        exit_price=exit_price,
                        notes=exit_notes,
                    )
                    if success:
                        trade = tr.get_trade_by_id(selected_trade["trade_id"])
                        outcome_emoji = "✅" if trade["outcome"] == "win" else \
                                        "❌" if trade["outcome"] == "loss" else "➡️"
                        st.success(
                            f"{outcome_emoji} Trade closed: "
                            f"{trade['outcome'].upper()} | "
                            f"P&L: ${trade['pnl_dollars']} ({trade['pnl_pct']}%)"
                        )
                        st.rerun()
                    else:
                        st.error("Could not find trade to update")

    # ── TAB 3: All Trades ────────────────────────────────────
    with tab3:
        st.subheader("Trade History")

        all_trades = tr.get_all_trades()
        if not all_trades:
            st.info("No trades logged yet.")
        else:
            # Clear test data option
            with st.expander("⚠️ Data Management"):
                st.caption("Use this to remove test trades before going live.")
                if st.button("🗑️ Clear ALL trade data", type="secondary"):
                    if st.session_state.get("confirm_clear_trades"):
                        import os
                        trades_path = os.path.join(config.LOG_DIR, "trades.json")
                        if os.path.exists(trades_path):
                            os.remove(trades_path)
                        st.success("✅ All trade data cleared")
                        st.session_state["confirm_clear_trades"] = False
                        st.rerun()
                    else:
                        st.session_state["confirm_clear_trades"] = True
                        st.warning("⚠️ Click again to confirm — this cannot be undone")

            import pandas as pd
            df = pd.DataFrame(all_trades)

            # Color outcome column
            display_cols = [
                "trade_id", "ticker", "direction", "trade_type",
                "entry_price", "exit_price", "size",
                "pnl_dollars", "pnl_pct", "outcome",
                "entry_date", "alert_score"
            ]
            available = [c for c in display_cols if c in df.columns]

            st.dataframe(
                df[available].sort_values("entry_date", ascending=False),
                use_container_width=True,
                hide_index=True,
            )

            # Export button
            csv = df.to_csv(index=False)
            col_exp1, col_exp2 = st.columns(2)
            col_exp1.download_button(
                label="⬇️ Export to CSV",
                data=csv,
                file_name="trades.csv",
                mime="text/csv",
                use_container_width=True,
            )

            # ── Test data cleanup ──────────────────────────
            st.divider()
            st.caption("⚠️ Danger Zone")
            with st.expander("🗑️ Clear Test Trades"):
                st.warning(
                    "This will permanently delete ALL trades from the log. "
                    "Use this to clean up test data before going live."
                )
                confirm = st.text_input(
                    "Type DELETE to confirm",
                    key="confirm_delete_trades"
                )
                if st.button("🗑️ Clear All Trades", use_container_width=True):
                    if confirm == "DELETE":
                        import os
                        trades_path = os.path.join(config.LOG_DIR, "trades.json")
                        if os.path.exists(trades_path):
                            os.remove(trades_path)
                        st.success("✅ All trades cleared.")
                        st.rerun()
                    else:
                        st.error("Type DELETE in the box above to confirm")

# ─────────────────────────────────────────
# LESSONS LEARNED PAGE (added Session 10)
# ─────────────────────────────────────────

def render_lessons():
    st.title("🧠 Lessons Learned")

    from journal.lessons import LessonsJournal, EMOTION_OPTIONS
    from journal.trade_recorder import TradeRecorder

    lj = LessonsJournal()
    tr = TradeRecorder()

    tab1, tab2, tab3 = st.tabs(["📝 Log Lesson", "📊 My Patterns", "📋 Lesson History"])

    # ── TAB 1: Log a Lesson ──────────────────────────────────
    with tab1:
        st.subheader("Post-Trade Debrief")
        st.caption("Complete this after every closed trade. Patterns emerge over time.")

        # Select a closed trade that has no lesson yet
        closed_trades = tr.get_closed_trades()
        already_logged = {l["trade_id"] for l in lj.get_recent_lessons(limit=500)}
        pending = [t for t in closed_trades if t["trade_id"] not in already_logged]

        if not pending:
            st.success("✅ All closed trades have lessons logged.")
            st.caption("Close more trades in the Trade Recorder to log lessons.")
        else:
            trade_options = {
                f"[{t['trade_id']}] {t['ticker']} — "
                f"{t['outcome'].upper()} {t['pnl_pct']}% — {t['entry_date']}": t
                for t in reversed(pending)
            }

            selected_label = st.selectbox("Select Trade to Debrief", list(trade_options.keys()))
            selected_trade = trade_options[selected_label]

            # Show trade summary
            col1, col2, col3, col4 = st.columns(4)
            outcome_emoji = "✅" if selected_trade["outcome"] == "win" else \
                           "❌" if selected_trade["outcome"] == "loss" else "➡️"
            col1.metric("Outcome",    f"{outcome_emoji} {selected_trade['outcome'].upper()}")
            col2.metric("P&L",        f"{selected_trade.get('pnl_pct', 0)}%")
            col3.metric("Entry",      f"${selected_trade['entry_price']}")
            col4.metric("Alert Score",selected_trade.get('alert_score', 'N/A'))

            st.divider()

            # Debrief form
            col_a, col_b = st.columns(2)
            followed_system = col_a.radio(
                "Did you take this trade from a system alert?",
                ["Yes — followed the alert", "No — my own idea"],
                horizontal=True
            )
            emotion = col_b.selectbox("How did you feel during this trade?", EMOTION_OPTIONS)

            col_c, col_d, col_e = st.columns(3)
            entry_quality  = col_c.slider("Entry Quality",   1, 5, 3,
                                          help="1=terrible, 5=perfect")
            exit_quality   = col_d.slider("Exit Quality",    1, 5, 3,
                                          help="1=terrible, 5=perfect")
            exec_score     = col_e.slider("Overall Execution",1, 5, 3,
                                          help="1=terrible, 5=perfect")

            what_right  = st.text_area("What went right?",
                                       placeholder="e.g. Waited for confirmation, clean entry")
            what_wrong  = st.text_area("What went wrong?",
                                       placeholder="e.g. Exited too early, ignored stop loss")
            differently = st.text_area("What would you do differently?",
                                       placeholder="e.g. Let the trade breathe more")
            summary     = st.text_input("One sentence lesson",
                                        placeholder="e.g. Trust the MA stack when CVD confirms")

            if st.button("💾 Save Lesson", use_container_width=True):
                if not summary:
                    st.error("Please enter a one sentence lesson summary")
                else:
                    lj.log_lesson(
                        trade_id=selected_trade["trade_id"],
                        ticker=selected_trade["ticker"],
                        outcome=selected_trade["outcome"],
                        pnl_pct=selected_trade.get("pnl_pct", 0),
                        followed_system="Yes" in followed_system,
                        entry_quality=entry_quality,
                        exit_quality=exit_quality,
                        emotion_during=emotion,
                        what_went_right=what_right,
                        what_went_wrong=what_wrong,
                        would_do_differently=differently,
                        lesson_summary=summary,
                        execution_score=exec_score,
                        alert_score=selected_trade.get("alert_score"),
                    )
                    st.success("✅ Lesson saved!")
                    st.rerun()

    # ── TAB 2: My Patterns ───────────────────────────────────
    with tab2:
        st.subheader("Pattern Analysis")
        patterns = lj.get_patterns()

        if patterns["total_lessons"] == 0:
            st.info("Log lessons after closing trades to see your patterns here.")
            return

        # Key metrics
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Lessons Logged",    patterns["total_lessons"])
        col2.metric("Avg Execution",     f"{patterns['avg_execution_score']}/5")
        col3.metric("System Win Rate",   f"{patterns['followed_win_rate']}%")
        col4.metric("Override Win Rate", f"{patterns['override_win_rate']}%")

        st.divider()

        # Insights
        st.subheader("💡 Your Insights")
        for insight in patterns["insights"]:
            if insight.startswith("✅"):
                st.success(insight)
            elif insight.startswith("⚠️"):
                st.warning(insight)
            else:
                st.info(insight)

        st.divider()

        col_p1, col_p2 = st.columns(2)

        # System adherence
        with col_p1:
            st.subheader("System Adherence")
            st.write(f"Followed system: **{patterns['followed_system_count']}** trades "
                     f"→ **{patterns['followed_win_rate']}%** win rate")
            st.write(f"Overrode system: **{patterns['overrode_system_count']}** trades "
                     f"→ **{patterns['override_win_rate']}%** win rate")

            st.subheader("Execution Quality")
            st.write(f"Entry:     **{patterns['avg_entry_quality']}/5**")
            st.write(f"Exit:      **{patterns['avg_exit_quality']}/5**")
            st.write(f"Overall:   **{patterns['avg_execution_score']}/5**")

        # Emotion breakdown
        with col_p2:
            st.subheader("Emotions on Wins")
            if patterns["win_emotions"]:
                for emotion, count in patterns["win_emotions"].items():
                    st.write(f"  **{emotion}**: {count} trades")
            else:
                st.write("Not enough data yet")

            st.subheader("Emotions on Losses")
            if patterns["loss_emotions"]:
                for emotion, count in patterns["loss_emotions"].items():
                    st.write(f"  **{emotion}**: {count} trades")
            else:
                st.write("Not enough data yet")

        # Top flags
        if patterns["top_flags"]:
            st.divider()
            st.subheader("🚩 Most Common Flags")
            for flag, count in patterns["top_flags"].items():
                flag_label = flag.replace("_", " ").title()
                st.write(f"  **{flag_label}**: {count}x")

    # ── TAB 3: Lesson History ────────────────────────────────
    with tab3:
        st.subheader("All Lessons")
        lessons = lj.get_recent_lessons(limit=100)

        if not lessons:
            st.info("No lessons logged yet.")
            return

        import pandas as pd
        df = pd.DataFrame(lessons)
        display_cols = [
            "logged_at", "ticker", "outcome", "pnl_pct",
            "followed_system", "emotion_during",
            "entry_quality", "exit_quality", "execution_score",
            "lesson_summary"
        ]
        available = [c for c in display_cols if c in df.columns]
        st.dataframe(
            df[available].sort_values("logged_at", ascending=False),
            use_container_width=True,
            hide_index=True,
        )


# ─────────────────────────────────────────
# AI ADVISOR PAGE (added Session 11)
# ─────────────────────────────────────────


# ─────────────────────────────────────────
# AI ADVISOR PAGE (Session 11 — updated with context + history)
# ─────────────────────────────────────────

def _get_advisor():
    """
    Get or create AIAdvisor instance stored in Streamlit session state.
    This preserves conversation context across tab switches.
    """
    from alerts.ai_advisor import AIAdvisor
    if "ai_advisor" not in st.session_state:
        st.session_state["ai_advisor"] = AIAdvisor()
    return st.session_state["ai_advisor"]


def render_ai_advisor():
    st.title("🤖 AI Trade Advisor")
    st.caption("Powered by Claude. Remembers your conversation within this session.")

    advisor = _get_advisor()

    # ── Session context indicator ─────────────────────────────
    turn_count = len(advisor.conversation) // 2
    col_s1, col_s2 = st.columns([4, 1])
    col_s1.caption(
        f"💬 Session turns: {turn_count} | "
        f"Started: {advisor.session_start.strftime('%I:%M %p EST')}"
    )
    if col_s2.button("🔄 New Session", help="Clear conversation and start fresh"):
        advisor.reset_conversation()
        st.success("Conversation cleared — starting fresh")
        st.rerun()

    st.divider()

    tab1, tab2, tab3, tab4 = st.tabs([
        "📊 Pre-Trade", "📝 Post-Trade Review",
        "💬 Ask a Question", "📜 Chat History"
    ])

    # ── TAB 1: PRE-TRADE ─────────────────────────────────────
    with tab1:
        st.subheader("Pre-Trade Setup Analysis")

        col1, col2 = st.columns(2)
        ticker   = col1.text_input("Ticker", placeholder="AAPL", key="pre_ticker").upper()
        mode     = col2.selectbox("Mode", ["swing", "intraday"], key="pre_mode")
        question = st.text_input(
            "Specific question (optional)",
            placeholder="e.g. Is the volume confirmation strong enough?",
            key="pre_question"
        )

        if st.button("🔍 Analyze Setup", use_container_width=True, key="pre_btn"):
            if not ticker:
                st.error("Please enter a ticker")
            else:
                with st.spinner(f"Scoring {ticker} and building analysis..."):
                    try:
                        from data.polygon_client import PolygonClient
                        from indicators.moving_averages import MovingAverages
                        from indicators.donchian import DonchianChannels
                        from indicators.volume import VolumeAnalysis
                        from indicators.cvd import CVDAnalysis
                        from indicators.rsi import RSIAnalysis
                        from signals.scorer import SignalScorer
                        from signals.options_layer import OptionsLayer
                        from journal.trade_recorder import TradeRecorder

                        tf  = config.SWING_PRIMARY_TIMEFRAME if mode == "swing" \
                              else config.INTRADAY_PRIMARY_TIMEFRAME
                        df  = PolygonClient().get_bars(
                            ticker, timeframe=tf, limit=300, days_back=400
                        )
                        if df is None or len(df) < 50:
                            st.error(
                                f"❌ Could not fetch data for `{ticker}`. "
                                f"Check the ticker symbol is correct and try again."
                            )
                        else:
                            ma_r  = MovingAverages(df).analyze()
                            dc_r  = DonchianChannels(df).analyze()
                            vol_r = VolumeAnalysis(df).analyze()
                            cvd_r = CVDAnalysis(df).analyze()
                            rsi_r = RSIAnalysis(df).analyze()
                            score_result = SignalScorer().score(
                                ma_r, dc_r, vol_r, cvd_r, rsi_r
                            )

                            watchlist   = load_watchlist()
                            options_ctx = None
                            if ticker in watchlist.get("options_enabled", []):
                                close = float(df["close"].iloc[-1])
                                options_ctx = OptionsLayer().analyze(
                                    ticker, score_result,
                                    stock_price=close,
                                    target=close * 1.05,
                                    stop=close * 0.97,
                                    mode=mode,
                                )

                            history = TradeRecorder().get_trades_for_ticker(ticker)
                            score   = score_result["final_score"]
                            layers  = score_result["layer_scores"]

                            col_s1, col_s2, col_s3, col_s4 = st.columns(4)
                            col_s1.metric("Score",     f"{score}/100")
                            col_s2.metric("Direction", score_result["direction"].upper())
                            col_s3.metric("Trend",     f"{layers['trend']['score']}/35")
                            col_s4.metric("Tier",      score_result["tier"].replace("_"," ").title())

                            st.divider()
                            with st.spinner("Getting AI analysis..."):
                                analysis = advisor.pre_trade_analysis(
                                    ticker=ticker,
                                    score_result=score_result,
                                    ma_result=ma_r,
                                    donchian_result=dc_r,
                                    volume_result=vol_r,
                                    cvd_result=cvd_r,
                                    rsi_result=rsi_r,
                                    options_context=options_ctx,
                                    trade_history=history,
                                    user_question=question,
                                )

                            st.subheader(f"🤖 Analysis — {ticker}")
                            st.markdown(analysis)

                            turns = len(advisor.conversation) // 2
                            st.caption(
                                f"Session turn {turns} — "
                                f"the advisor remembers this analysis if you ask follow-up questions"
                            )

                    except Exception as e:
                        st.error(f"Error: {e}")

    # ── TAB 2: POST-TRADE REVIEW ─────────────────────────────
    with tab2:
        st.subheader("Post-Trade Review")

        from journal.trade_recorder import TradeRecorder
        from journal.lessons import LessonsJournal

        tr = TradeRecorder()
        lj = LessonsJournal()
        closed_trades = tr.get_closed_trades()

        if not closed_trades:
            st.info("No closed trades yet.")
        else:
            trade_options = {
                f"[{t['trade_id']}] {t['ticker']} — "
                f"{t.get('outcome','?').upper()} {t.get('pnl_pct',0)}% — "
                f"{t['entry_date']}": t
                for t in reversed(closed_trades)
            }
            selected_label = st.selectbox(
                "Select Trade", list(trade_options.keys()), key="post_select"
            )
            selected_trade = trade_options[selected_label]
            lesson   = lj.get_lessons_for_trade(selected_trade["trade_id"])
            patterns = lj.get_patterns()

            if lesson:
                st.success("✅ Lesson logged — AI will include your debrief")
            else:
                st.warning("⚠️ No lesson logged — add one in 🧠 Lessons for richer feedback")

            post_q = st.text_input(
                "Specific question (optional)",
                placeholder="e.g. Did I exit too early?",
                key="post_q"
            )

            if st.button("🤖 Get Review", use_container_width=True, key="post_btn"):
                col_p1, col_p2, col_p3 = st.columns(3)
                emoji = "✅" if selected_trade.get("outcome") == "win" else "❌"
                col_p1.metric("Outcome", f"{emoji} {selected_trade.get('outcome','?').upper()}")
                col_p2.metric("P&L $",   f"${selected_trade.get('pnl_dollars',0)}")
                col_p3.metric("P&L %",   f"{selected_trade.get('pnl_pct',0)}%")

                with st.spinner("Getting AI review..."):
                    review = advisor.post_trade_review(
                        trade=selected_trade,
                        lesson=lesson,
                        patterns=patterns if patterns["total_lessons"] >= 3 else None,
                        user_question=post_q,
                    )
                st.subheader("🤖 Trade Review")
                st.markdown(review)

                turns = len(advisor.conversation) // 2
                st.caption(f"Session turn {turns} — ask follow-up questions in the Ask tab")

    # ── TAB 3: ASK A QUESTION ────────────────────────────────
    with tab3:
        st.subheader("Ask the Trading Advisor")
        st.caption(
            "The advisor remembers earlier analysis from this session. "
            "Ask follow-ups like 'What about that AAPL setup we just looked at?'"
        )

        # Show recent context if available
        if advisor.conversation:
            with st.expander(f"📝 Session context ({len(advisor.conversation)//2} turns)", expanded=False):
                for i, msg in enumerate(advisor.conversation[-6:]):
                    role  = "You" if msg["role"] == "user" else "Advisor"
                    short = msg["content"][:200] + "..." if len(msg["content"]) > 200 else msg["content"]
                    st.markdown(f"**{role}:** {short}")
                    if i < len(advisor.conversation[-6:]) - 1:
                        st.divider()

        col_c1, col_c2, col_c3 = st.columns(3)
        include_stats    = col_c1.checkbox("My trade stats",    key="ctx_stats")
        include_patterns = col_c2.checkbox("My patterns",       key="ctx_patterns")
        include_watchlist= col_c3.checkbox("My watchlist",      key="ctx_watch")

        question = st.text_area(
            "Your question",
            placeholder=(
                "e.g. When should I use a debit spread vs a credit spread?\n"
                "e.g. That AAPL setup we looked at — should I wait for a pullback?\n"
                "e.g. My RSI divergence trades keep failing — what am I missing?"
            ),
            height=100,
            key="gen_q"
        )

        if st.button("💬 Ask Advisor", use_container_width=True, key="gen_btn"):
            if not question.strip():
                st.error("Please enter a question")
            else:
                context = {}
                if include_stats:
                    from journal.trade_recorder import TradeRecorder
                    context["trade_stats"] = TradeRecorder().get_summary_stats()
                if include_patterns:
                    from journal.lessons import LessonsJournal
                    context["patterns"] = LessonsJournal().get_patterns()
                if include_watchlist:
                    context["watchlist"] = load_watchlist()

                with st.spinner("Getting answer..."):
                    answer = advisor.ask(
                        question=question,
                        context_data=context if context else None,
                    )

                st.subheader("🤖 Advisor Response")
                st.markdown(answer)

    # ── TAB 4: CHAT HISTORY ──────────────────────────────────
    with tab4:
        st.subheader("📜 Conversation History")
        st.caption("All AI Advisor conversations saved to logs/ai_conversations.json")

        history = advisor.get_history(limit=100)

        if not history:
            st.info("No conversation history yet. Use the other tabs to start talking to the advisor.")
            return

        # Filter controls
        col_f1, col_f2 = st.columns(2)
        filter_mode   = col_f1.selectbox(
            "Filter by type",
            ["All", "pre_trade", "post_trade", "general"],
            key="hist_filter"
        )
        filter_ticker = col_f2.text_input(
            "Filter by ticker", placeholder="e.g. AAPL", key="hist_ticker"
        ).upper()

        filtered = list(reversed(history))
        if filter_mode != "All":
            filtered = [h for h in filtered if h.get("mode") == filter_mode]
        if filter_ticker:
            filtered = [h for h in filtered if h.get("ticker") == filter_ticker]

        if not filtered:
            st.info("No conversations match your filter.")
            return

        st.caption(f"Showing {len(filtered)} conversation(s)")

        for entry in filtered:
            mode_emoji = {
                "pre_trade":  "📊",
                "post_trade": "📝",
                "general":    "💬",
            }.get(entry.get("mode"), "💬")

            ticker_str = f" — {entry['ticker']}" if entry.get("ticker") else ""
            meta       = entry.get("metadata", {})
            meta_str   = ""
            if meta.get("score"):
                meta_str = f" | Score: {meta['score']}/100"
            if meta.get("outcome"):
                meta_str += f" | {meta['outcome'].upper()} {meta.get('pnl_pct','')}%"

            with st.expander(
                f"{mode_emoji} {entry.get('mode','').replace('_',' ').title()}"
                f"{ticker_str}{meta_str} — {entry.get('timestamp','')}",
                expanded=False
            ):
                st.markdown("**🤖 AI Response:**")
                st.markdown(entry.get("ai_response", ""))


# ─────────────────────────────────────────
# NEWS BRIEFINGS PAGE
# ─────────────────────────────────────────

def render_news():
    st.title("📰 News Briefings")
    st.caption("AI-synthesized market news delivered 3x daily")

    from scanners.news_scanner import NewsScanner
    ns = NewsScanner()

    # ── Manual trigger ───────────────────────────────────────
    st.subheader("Run a Briefing Now")
    st.caption("Saves briefing here AND posts to Discord #news-briefings automatically.")

    col1, col2, col3 = st.columns(3)

    if col1.button("🌅 Morning Briefing", use_container_width=True):
        with st.spinner("Fetching news and synthesizing..."):
            result = ns.run(briefing_type="morning", post_to_discord=False)
        if result:
            st.success(f"✅ Done — {result.get('total_articles',0)} articles found")
            st.rerun()

    if col2.button("☀️ Midday Update", use_container_width=True):
        with st.spinner("Fetching news and synthesizing..."):
            result = ns.run(briefing_type="midday", post_to_discord=False)
        if result:
            st.success(f"✅ Done — {result.get('total_articles',0)} articles found")
            st.rerun()

    if col3.button("🌆 End of Day Wrap", use_container_width=True):
        with st.spinner("Fetching news and synthesizing..."):
            result = ns.run(briefing_type="eod", post_to_discord=False)
        if result:
            st.success(f"✅ Done — {result.get('total_articles',0)} articles found")
            st.rerun()

    st.caption("Scheduled: 🌅 7:45 AM  •  ☀️ 12:00 PM  •  🌆 3:45 PM  (weekdays EST)")
    st.info("💡 Discord: use **/news** to post directly to #news-briefings from your phone")


    st.divider()

    # ── Recent briefings ─────────────────────────────────────
    briefings = ns.get_recent_briefings(limit=15)

    if not briefings:
        st.info(
            "No briefings yet. Click a button above to run one now, "
            "or they will run automatically on schedule:\n\n"
            "• 🌅 Morning: 7:45 AM EST\n"
            "• ☀️ Midday: 12:00 PM EST\n"
            "• 🌆 EOD: 3:45 PM EST"
        )
        return

    # Filter
    filter_type = st.selectbox(
        "Filter", ["All", "Morning", "Midday", "EOD"], key="news_filter"
    )

    filtered = briefings
    if filter_type != "All":
        type_map = {"Morning": "morning", "Midday": "midday", "EOD": "eod"}
        filtered = [b for b in briefings if b.get("type") == type_map[filter_type]]

    for briefing in filtered:
        emoji    = briefing.get("emoji", "📰")
        btype    = briefing.get("type", "").upper()
        ts       = briefing.get("timestamp", "")
        n_tickers = len(briefing.get("tickers_with_news", []))
        n_articles = briefing.get("total_articles", 0)

        title_map = {"MORNING": "Morning Briefing", "MIDDAY": "Midday Update", "EOD": "End of Day Wrap"}
        title = title_map.get(btype, "Briefing")

        with st.expander(
            f"{emoji} {title} — {ts} | {n_tickers} tickers | {n_articles} articles",
            expanded=False
        ):
            # AI synthesis
            ai_text = briefing.get("ai_synthesis", "")
            if ai_text and "unavailable" not in ai_text.lower():
                st.subheader("🤖 AI Analysis")
                st.markdown(ai_text)
                st.divider()

            # Headlines by ticker
            ticker_news = briefing.get("ticker_news", {})
            if ticker_news:
                st.subheader("📰 Headlines")
                for ticker, articles in ticker_news.items():
                    if articles:
                        st.markdown(f"**{ticker}**")
                        for a in articles[:3]:
                            pub   = f" — _{a.get('publisher', '')}_" if a.get('publisher') else ""
                            url   = a.get("url", "")
                            title_text = a.get("title", "")
                            if url:
                                st.markdown(f"• [{title_text}]({url}){pub}")
                            else:
                                st.markdown(f"• {title_text}{pub}")

            # Market news
            market_news = briefing.get("market_news", [])
            if market_news:
                st.divider()
                st.subheader("🌍 Market-Wide")
                for a in market_news[:5]:
                    url  = a.get("url", "")
                    titl = a.get("title", "")
                    if url:
                        st.markdown(f"• [{titl}]({url})")
                    else:
                        st.markdown(f"• {titl}")

def main():
    page = render_sidebar()

    if page == "📡 Live Alerts":
        render_live_alerts()
    elif page == "📰 News":
        render_news()
    elif page == "📋 Watchlist":
        render_watchlist()
    elif page == "📓 Journal":
        render_journal()
    elif page == "📊 Performance":
        render_performance()
    elif page == "📒 Trade Recorder":
        render_trade_recorder()
    elif page == "🧠 Lessons":
        render_lessons()
    elif page == "🤖 AI Advisor":
        render_ai_advisor()
    elif page == "⚙️ Settings":
        render_settings()


if __name__ == "__main__":
    main()
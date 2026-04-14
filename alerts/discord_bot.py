"""
alerts/discord_bot.py — Discord Bot for Trading Alerts
Posts alert cards to Discord channels and handles slash commands.

Channels:
    #standard-alerts      → 🟡 score 75-89
    #high-conviction      → 🔴 score 90+

Slash Commands:
    /add TICKER           → add to watchlist (swing, intraday, or both)
    /remove TICKER        → remove from watchlist (swing, intraday, or both)
    /watchlist            → show current watchlist
    /score TICKER         → manually trigger a score check
    /status               → show scanner status
    /log TICKER ...       → log a trade entry
    /exit                 → close an open trade (select from list)
    /trades               → show open trades + stats
"""

import json
import os
import sys
import asyncio
from datetime import datetime
from loguru import logger

import discord
from discord import app_commands
from discord.ext import commands

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import config

# ─────────────────────────────────────────
# BOT SETUP
# ─────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True

bot  = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

scanner_status = {
    "running":      False,
    "last_scan":    None,
    "alerts_today": 0,
}

# ─────────────────────────────────────────
# WATCHLIST HELPERS
# ─────────────────────────────────────────

def load_watchlist() -> dict:
    try:
        with open(config.WATCHLIST_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load watchlist: {e}")
        return {"swing": [], "intraday": [], "options_enabled": []}


def save_watchlist(watchlist: dict):
    try:
        with open(config.WATCHLIST_PATH, "w") as f:
            json.dump(watchlist, f, indent=2)
        logger.info("Watchlist saved")
    except Exception as e:
        logger.error(f"Failed to save watchlist: {e}")


def get_all_tickers() -> list[str]:
    """Return all unique tickers across swing and intraday lists."""
    wl = load_watchlist()
    return sorted(set(wl.get("swing", []) + wl.get("intraday", [])))


# ─────────────────────────────────────────
# AUTOCOMPLETE FUNCTIONS
# ─────────────────────────────────────────

async def ticker_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for tickers in the watchlist."""
    tickers = get_all_tickers()
    # Add common tickers not in watchlist as suggestions
    common  = ["AAPL", "MSFT", "NVDA", "SPY", "QQQ", "TSLA", "AMZN", "GOOGL", "META"]
    all_tickers = list(dict.fromkeys(tickers + common))  # Watchlist first, no duplicates
    filtered = [t for t in all_tickers if current.upper() in t][:10]
    return [app_commands.Choice(name=t, value=t) for t in filtered]


async def open_trade_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for open trade IDs."""
    try:
        from journal.trade_recorder import TradeRecorder
        open_trades = TradeRecorder().get_open_trades()
        choices = []
        for t in reversed(open_trades[-10:]):
            label = (
                f"{t['trade_id']} — {t['ticker']} "
                f"{t['direction']} @ ${t['entry_price']}"
            )
            if current.upper() in t["trade_id"] or current.upper() in t["ticker"]:
                choices.append(app_commands.Choice(name=label, value=t["trade_id"]))
            elif not current:
                choices.append(app_commands.Choice(name=label, value=t["trade_id"]))
        return choices[:10]
    except Exception:
        return []


# ─────────────────────────────────────────
# BOT EVENTS
# ─────────────────────────────────────────

@bot.event
async def on_ready():
    logger.info(f"Discord bot connected as {bot.user}")
    try:
        synced = await tree.sync()
        logger.info(f"Synced {len(synced)} slash commands")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")


# ─────────────────────────────────────────
# WATCHLIST COMMANDS
# ─────────────────────────────────────────

@tree.command(name="add", description="Add a ticker to the watchlist")
@app_commands.describe(
    ticker="Stock symbol e.g. AAPL — autocomplete shows current watchlist",
    mode="swing | intraday | both (default: both — adds to swing AND intraday)"
)
@app_commands.autocomplete(ticker=ticker_autocomplete)
@app_commands.choices(mode=[
    app_commands.Choice(name="Both (swing + intraday)", value="both"),
    app_commands.Choice(name="Swing only",              value="swing"),
    app_commands.Choice(name="Intraday only",           value="intraday"),
])
async def add_ticker(
    interaction: discord.Interaction,
    ticker: str,
    mode: str = "both",
):
    ticker = ticker.upper().strip()
    mode   = mode.lower().strip()

    if mode not in ("swing", "intraday", "both"):
        await interaction.response.send_message(
            "❌ Mode must be `swing`, `intraday`, or `both`", ephemeral=True
        )
        return

    watchlist  = load_watchlist()
    added_to   = []
    lists      = ["swing", "intraday"] if mode == "both" else [mode]

    for lst in lists:
        if ticker not in watchlist.get(lst, []):
            watchlist[lst].append(ticker)
            added_to.append(lst)

    if not added_to:
        await interaction.response.send_message(
            f"⚠️ `{ticker}` is already in all selected lists.", ephemeral=True
        )
        return

    save_watchlist(watchlist)

    added_str     = " and ".join(f"**{a}**" for a in added_to)
    swing_list    = ", ".join(f"`{t}`" for t in watchlist.get("swing",    [])) or "_empty_"
    intraday_list = ", ".join(f"`{t}`" for t in watchlist.get("intraday", [])) or "_empty_"

    await interaction.response.send_message(
        f"✅ Added `{ticker}` to {added_str}.\n"
        f"📅 Swing:    {swing_list}\n"
        f"⚡ Intraday: {intraday_list}"
    )
    logger.info(f"Added {ticker} to {added_to} via Discord")


@tree.command(name="remove", description="Remove a ticker from the watchlist")
@app_commands.describe(
    ticker="Stock symbol to remove",
    mode="Which list: swing, intraday, or both (default: both)"
)
@app_commands.autocomplete(ticker=ticker_autocomplete)
@app_commands.choices(mode=[
    app_commands.Choice(name="Both (swing + intraday)", value="both"),
    app_commands.Choice(name="Swing only",              value="swing"),
    app_commands.Choice(name="Intraday only",           value="intraday"),
])
async def remove_ticker(
    interaction: discord.Interaction,
    ticker: str,
    mode: str = "both",
):
    ticker = ticker.upper().strip()
    mode   = mode.lower().strip()

    if mode not in ("swing", "intraday", "both"):
        await interaction.response.send_message(
            "❌ Mode must be `swing`, `intraday`, or `both`", ephemeral=True
        )
        return

    watchlist    = load_watchlist()
    removed_from = []
    lists        = ["swing", "intraday"] if mode == "both" else [mode]

    for lst in lists:
        if ticker in watchlist.get(lst, []):
            watchlist[lst].remove(ticker)
            removed_from.append(lst)

    if not removed_from:
        await interaction.response.send_message(
            f"❌ `{ticker}` not found in any watchlist.", ephemeral=True
        )
        return

    save_watchlist(watchlist)

    removed_str   = " and ".join(f"**{r}**" for r in removed_from)
    swing_list    = ", ".join(f"`{t}`" for t in watchlist.get("swing",    [])) or "_empty_"
    intraday_list = ", ".join(f"`{t}`" for t in watchlist.get("intraday", [])) or "_empty_"

    await interaction.response.send_message(
        f"🗑️ Removed `{ticker}` from {removed_str}.\n"
        f"📅 Swing:    {swing_list}\n"
        f"⚡ Intraday: {intraday_list}"
    )
    logger.info(f"Removed {ticker} from {removed_from} via Discord")


@tree.command(name="watchlist", description="Show the current watchlist")
async def show_watchlist(interaction: discord.Interaction):
    watchlist = load_watchlist()

    swing_list    = ", ".join(f"`{t}`" for t in watchlist.get("swing",    [])) or "_empty_"
    intraday_list = ", ".join(f"`{t}`" for t in watchlist.get("intraday", [])) or "_empty_"

    embed = discord.Embed(
        title="📋 Current Watchlist",
        color=discord.Color.blue(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="📅 Swing",    value=swing_list,    inline=False)
    embed.add_field(name="⚡ Intraday", value=intraday_list, inline=False)
    await interaction.response.send_message(embed=embed)


# ─────────────────────────────────────────
# SCORE COMMAND
# ─────────────────────────────────────────

@tree.command(name="score", description="Run a live score check on a ticker")
@app_commands.describe(
    ticker="Stock symbol to score",
    mode="swing or intraday (default: swing)"
)
@app_commands.autocomplete(ticker=ticker_autocomplete)
async def manual_score(
    interaction: discord.Interaction,
    ticker: str,
    mode: str = "swing"
):
    ticker = ticker.upper().strip()
    await interaction.response.defer()

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
            await interaction.followup.send(f"❌ Could not fetch data for `{ticker}`")
            return

        ma_r  = MovingAverages(df).analyze()
        dc_r  = DonchianChannels(df).analyze()
        vol_r = VolumeAnalysis(df).analyze()
        cvd_r = CVDAnalysis(df).analyze()
        rsi_r = RSIAnalysis(df).analyze()

        result = SignalScorer().score(ma_r, dc_r, vol_r, cvd_r, rsi_r)

        score  = result["final_score"]
        tier   = result["tier"]
        layers = result["layer_scores"]

        tier_color = {
            "high_conviction": discord.Color.red(),
            "standard":        discord.Color.gold(),
            "watchlist":       discord.Color.blue(),
            "none":            discord.Color.dark_grey(),
        }.get(tier, discord.Color.dark_grey())

        emoji = result.get("alert_emoji", "⚪") or "⚪"

        embed = discord.Embed(
            title=f"{emoji} Score Check — {ticker}",
            color=tier_color,
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Score",     value=f"**{score}/100**",                    inline=True)
        embed.add_field(name="Direction", value=result["direction"].upper(),            inline=True)
        embed.add_field(name="Tier",      value=tier.replace("_"," ").title(),         inline=True)
        embed.add_field(
            name="Layer Breakdown",
            value=(
                f"Trend:  {layers['trend']['score']}/{layers['trend']['max']}\n"
                f"Setup:  {layers['setup']['score']}/{layers['setup']['max']}\n"
                f"Volume: {layers['volume']['score']}/{layers['volume']['max']}"
            ),
            inline=False
        )
        embed.add_field(
            name="Indicators",
            value=(
                f"RSI: {rsi_r.get('rsi_current','N/A')} | "
                f"RVOL: {vol_r.get('rvol','N/A')}x | "
                f"CVD: {cvd_r.get('cvd_slope','N/A')}"
            ),
            inline=False
        )
        embed.set_footer(text=f"Mode: {mode.capitalize()} | TF: {timeframe}")
        await interaction.followup.send(embed=embed)
        logger.info(f"Score check: {ticker} = {score}/100")

    except Exception as e:
        logger.error(f"Score error for {ticker}: {e}")
        await interaction.followup.send(f"❌ Error scoring `{ticker}`: {e}")


# ─────────────────────────────────────────
# STATUS COMMAND
# ─────────────────────────────────────────

@tree.command(name="status", description="Show scanner and bot status")
async def scanner_status_cmd(interaction: discord.Interaction):
    running   = scanner_status["running"]
    last_scan = scanner_status["last_scan"] or "Never"
    alerts    = scanner_status["alerts_today"]
    status_emoji = "🟢" if running else "🔴"

    embed = discord.Embed(
        title="🤖 Trading Assistant Status",
        color=discord.Color.green() if running else discord.Color.red(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Scanner",      value=f"{status_emoji} {'Running' if running else 'Stopped'}", inline=True)
    embed.add_field(name="Last Scan",    value=str(last_scan), inline=True)
    embed.add_field(name="Alerts Today", value=str(alerts),    inline=True)

    watchlist = load_watchlist()
    embed.add_field(
        name="Watchlist",
        value=f"Swing: {len(watchlist['swing'])} | Intraday: {len(watchlist['intraday'])}",
        inline=False
    )
    await interaction.response.send_message(embed=embed)


# ─────────────────────────────────────────
# TRADE RECORDER COMMANDS
# ─────────────────────────────────────────

@tree.command(name="log", description="Log a new trade entry")
@app_commands.describe(
    ticker="Stock symbol e.g. AAPL",
    entry="Entry price e.g. 170.50",
    size="Shares or contracts e.g. 10",
    direction="Trade direction",
    strategy="Trade strategy type",
    notes="Why you're entering this trade (optional)"
)
@app_commands.autocomplete(ticker=ticker_autocomplete)
@app_commands.choices(
    direction=[
        app_commands.Choice(name="📈 Bullish",       value="bullish"),
        app_commands.Choice(name="📉 Bearish",       value="bearish"),
    ],
    strategy=[
        app_commands.Choice(name="Stock",            value="stock"),
        app_commands.Choice(name="Debit Spread",     value="debit_spread"),
        app_commands.Choice(name="Credit Spread",    value="credit_spread"),
        app_commands.Choice(name="Iron Condor",      value="iron_condor"),
        app_commands.Choice(name="Single Leg Option",value="single_leg"),
    ]
)
async def log_trade(
    interaction: discord.Interaction,
    ticker:    str,
    entry:     float,
    size:      float,
    direction: str = "bullish",
    strategy:  str = "stock",
    notes:     str = "",
):
    ticker    = ticker.upper().strip()
    direction = direction.lower().strip()
    strategy  = strategy.lower().strip()

    try:
        from journal.trade_recorder import TradeRecorder
        tr       = TradeRecorder()
        trade_id = tr.log_entry(
            ticker=ticker,
            entry_price=entry,
            size=size,
            strategy=strategy,
            direction=direction,
            notes=notes,
        )

        dir_emoji = "📈" if direction == "bullish" else "📉"

        embed = discord.Embed(
            title=f"📥 Trade Logged — {ticker}",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Trade ID",    value=f"`{trade_id}`",             inline=True)
        embed.add_field(name="Direction",   value=f"{dir_emoji} {direction.upper()}", inline=True)
        embed.add_field(name="Strategy",    value=strategy.replace("_"," ").title(), inline=True)
        embed.add_field(name="Entry Price", value=f"${entry}",                 inline=True)
        embed.add_field(name="Size",        value=str(size),                   inline=True)
        embed.add_field(name="Entry Value", value=f"${round(entry*size, 2)}",  inline=True)
        if notes:
            embed.add_field(name="Notes",   value=notes, inline=False)
        embed.set_footer(text="Use /exit to close this trade — it will appear in /trades")
        await interaction.response.send_message(embed=embed)
        logger.info(f"Trade logged via Discord: [{trade_id}] {ticker} @ ${entry}")

    except Exception as e:
        logger.error(f"Discord log trade error: {e}")
        await interaction.response.send_message(f"❌ Error logging trade: {e}")


@tree.command(name="exit", description="Close an open trade")
@app_commands.describe(
    trade="Select your open trade from the list",
    exit_price="Price you exited at",
    notes="Optional exit notes"
)
@app_commands.autocomplete(trade=open_trade_autocomplete)
async def exit_trade(
    interaction: discord.Interaction,
    trade:      str,
    exit_price: float,
    notes:      str = "",
):
    trade_id = trade.upper().strip()

    try:
        from journal.trade_recorder import TradeRecorder
        tr      = TradeRecorder()
        success = tr.log_exit(trade_id, exit_price, notes)

        if not success:
            await interaction.response.send_message(
                f"❌ Trade `{trade_id}` not found. Use `/trades` to see open trades.",
                ephemeral=True
            )
            return

        t = tr.get_trade_by_id(trade_id)
        outcome       = t["outcome"]
        pnl_d         = t["pnl_dollars"]
        pnl_p         = t["pnl_pct"]
        outcome_emoji = "✅" if outcome == "win" else "❌" if outcome == "loss" else "➡️"
        color         = discord.Color.green() if outcome == "win" else \
                        discord.Color.red()   if outcome == "loss" else \
                        discord.Color.blue()

        embed = discord.Embed(
            title=f"{outcome_emoji} Trade Closed — {t['ticker']}",
            color=color,
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Outcome",   value=outcome.upper(),        inline=True)
        embed.add_field(name="Strategy",  value=t.get("strategy","stock").replace("_"," ").title(), inline=True)
        embed.add_field(name="Direction", value=t["direction"],         inline=True)
        embed.add_field(name="Entry",     value=f"${t['entry_price']}", inline=True)
        embed.add_field(name="Exit",      value=f"${exit_price}",       inline=True)
        embed.add_field(name="Size",      value=str(t["size"]),         inline=True)
        embed.add_field(name="P&L $",     value=f"${pnl_d}",            inline=True)
        embed.add_field(name="P&L %",     value=f"{pnl_p}%",            inline=True)
        if notes:
            embed.add_field(name="Notes", value=notes, inline=False)
        embed.set_footer(text="Log a lesson in the dashboard → 🧠 Lessons")

        await interaction.response.send_message(embed=embed)
        logger.info(f"Trade closed via Discord: [{trade_id}] {t['ticker']} → {outcome} P&L ${pnl_d}")

    except Exception as e:
        logger.error(f"Discord exit trade error: {e}")
        await interaction.response.send_message(f"❌ Error: {e}")


@tree.command(name="trades", description="Show your open trades and stats")
async def show_trades(interaction: discord.Interaction):
    try:
        from journal.trade_recorder import TradeRecorder
        tr          = TradeRecorder()
        open_trades = tr.get_open_trades()
        stats       = tr.get_summary_stats()

        embed = discord.Embed(
            title="📒 My Trades",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Total",    value=stats["total"],              inline=True)
        embed.add_field(name="Win Rate", value=f"{stats['win_rate']}%",     inline=True)
        embed.add_field(name="P&L",      value=f"${stats['total_pnl']}",   inline=True)

        if open_trades:
            lines = []
            for t in reversed(open_trades[-8:]):
                dir_e = "📈" if t["direction"] == "BULLISH" else "📉"
                strat = t.get("strategy","stock").replace("_"," ").title()
                lines.append(
                    f"`{t['trade_id']}` {dir_e} **{t['ticker']}** "
                    f"${t['entry_price']} × {t['size']} — {strat}"
                )
            embed.add_field(
                name=f"Open Trades ({len(open_trades)})",
                value="\n".join(lines),
                inline=False
            )
        else:
            embed.add_field(name="Open Trades", value="None open", inline=False)

        embed.set_footer(text="Use /exit to close a trade | /log to open one")
        await interaction.response.send_message(embed=embed)

    except Exception as e:
        logger.error(f"Discord trades error: {e}")
        await interaction.response.send_message(f"❌ Error: {e}")


# ─────────────────────────────────────────
# ALERT POSTING
# ─────────────────────────────────────────

async def post_alert(alert: dict, message: str):
    tier = alert.get("tier", "none")
    if tier == "high_conviction":
        channel_id = config.DISCORD_CHANNEL_ID_HIGH_CONVICTION
    elif tier == "standard":
        channel_id = config.DISCORD_CHANNEL_ID_STANDARD
    else:
        return

    try:
        channel = bot.get_channel(channel_id)
        if channel is None:
            logger.error(f"Discord channel {channel_id} not found")
            return
        await channel.send(message)
        scanner_status["alerts_today"] += 1
        logger.info(f"Alert posted: {alert['ticker']} {alert.get('emoji','')}")
    except Exception as e:
        logger.error(f"Failed to post Discord alert: {e}")


def post_alert_sync(alert: dict, message: str):
    if bot.loop and bot.loop.is_running():
        asyncio.run_coroutine_threadsafe(post_alert(alert, message), bot.loop)
    else:
        logger.warning("Discord bot loop not running — alert not posted")


# ─────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────

def run_bot():
    if not config.DISCORD_BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN not set in .env")
        return
    logger.info("Starting Discord bot...")
    bot.run(config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    run_bot()
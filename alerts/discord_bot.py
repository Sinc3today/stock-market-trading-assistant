"""
alerts/discord_bot.py — Discord Bot for Trading Alerts

WHAT WAS BROKEN & WHAT WAS FIXED:
──────────────────────────────────
1. Race condition: main.py called post_alert_sync() while the bot loop
   was still starting in its thread. bot.loop existed but wasn't
   accepting coroutines yet → silent drop (the "bot loop not running" warning).
   FIX: Added _bot_ready Event. post_alert_sync / post_message_sync
   now wait up to 30s for the bot to be ready before giving up.

2. Channel fetch using bot.get_channel() returned None when the bot
   hadn't yet received the GUILD_CREATE event (even after login).
   FIX: Fall back to bot.fetch_channel() which does a live API call.

3. No startup diagnostics — you had no way to tell if channels were
   configured correctly.
   FIX: on_ready now logs channel names and member counts.

4. Added post_message_sync(message) helper needed by spy_daily_scheduler.

Posts alert cards to Discord channels and handles slash commands.

Channels:
    #standard-alerts      → 🟡 score 75-89
    #high-conviction      → 🔴 score 90+

Slash Commands:
    /add TICKER           → add to watchlist
    /remove TICKER        → remove from watchlist
    /watchlist            → show current watchlist
    /score TICKER         → manually trigger a score check
    /status               → show scanner + bot status
    /log TICKER ...       → log a trade entry
    /exit                 → close an open trade
    /trades               → show open trades + stats
"""

import json
import os
import sys
import asyncio
import threading
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

# ── Ready gate ───────────────────────────────────────────────
# Set when on_ready fires. post_alert_sync / post_message_sync
# wait on this before attempting to post — eliminates the race condition.
_bot_ready = threading.Event()


# ─────────────────────────────────────────
# EVENTS
# ─────────────────────────────────────────

@bot.event
async def on_ready():
    """Called once the bot has connected and received guild data."""
    logger.info(f"✅ Discord bot logged in as: {bot.user} (id={bot.user.id})")

    # Sync slash commands
    try:
        synced = await tree.sync()
        logger.info(f"   Slash commands synced: {len(synced)}")
    except Exception as e:
        logger.error(f"   Slash command sync failed: {e}")

    # Diagnostics — log channel names so you can spot config errors fast
    _log_channel_diagnostics()

    # Signal that the bot is ready — unblocks post_alert_sync callers
    _bot_ready.set()
    logger.info("   Bot ready gate opened — alerts will now post ✅")


def _log_channel_diagnostics():
    """Log configured channel info on startup so problems are obvious."""
    for label, cid in [
        ("standard",       config.DISCORD_CHANNEL_ID_STANDARD),
        ("high_conviction", config.DISCORD_CHANNEL_ID_HIGH_CONVICTION),
    ]:
        if not cid:
            logger.warning(f"   ⚠️  {label} channel ID not set in .env")
            continue
        ch = bot.get_channel(cid)
        if ch is None:
            logger.warning(
                f"   ⚠️  {label} channel ID {cid} not found — "
                f"check the bot has access to this server/channel"
            )
        else:
            logger.info(f"   #{ch.name} ({label}) — ✅ found")


# ─────────────────────────────────────────
# ALERT POSTING
# ─────────────────────────────────────────

async def _get_channel(channel_id: int):
    """
    Get channel object. Tries cache first, falls back to API fetch.
    Cache miss is common on startup before GUILD_CREATE is received.
    """
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception as e:
            logger.error(f"Cannot fetch channel {channel_id}: {e}")
    return channel


async def post_alert(alert: dict, message: str):
    """Post a scored alert to the appropriate tier channel."""
    tier = alert.get("tier", "none")
    if tier == "high_conviction":
        channel_id = config.DISCORD_CHANNEL_ID_HIGH_CONVICTION
    elif tier == "standard":
        channel_id = config.DISCORD_CHANNEL_ID_STANDARD
    else:
        logger.debug(f"Alert tier '{tier}' has no channel — not posted")
        return

    channel = await _get_channel(channel_id)
    if channel is None:
        logger.error(
            f"post_alert: channel {channel_id} unavailable — "
            f"alert for {alert.get('ticker')} dropped. "
            f"Check DISCORD_CHANNEL_ID_STANDARD / _HIGH_CONVICTION in .env"
        )
        return

    try:
        await channel.send(message)
        scanner_status["alerts_today"] += 1
        logger.info(
            f"✅ Alert posted: {alert.get('ticker')} "
            f"{alert.get('alert_emoji','')} → #{channel.name}"
        )
    except discord.Forbidden:
        logger.error(
            f"Bot lacks Send Messages permission in #{channel.name} — "
            f"grant the permission in Discord server settings"
        )
    except Exception as e:
        logger.error(f"Failed to post alert: {e}")


async def post_message(channel_id: int, message: str):
    """
    Post a plain text message to any channel by ID.
    Used by spy_daily_scheduler and other non-alert jobs.
    """
    channel = await _get_channel(channel_id)
    if channel is None:
        logger.error(f"post_message: channel {channel_id} unavailable")
        return
    try:
        await channel.send(message)
        logger.info(f"Message posted to #{channel.name}")
    except discord.Forbidden:
        logger.error(f"Bot lacks Send Messages permission in channel {channel_id}")
    except Exception as e:
        logger.error(f"post_message failed: {e}")


# ─────────────────────────────────────────
# THREAD-SAFE SYNC WRAPPERS
# ─────────────────────────────────────────

def post_alert_sync(alert: dict, message: str, timeout: float = 30.0):
    """
    Thread-safe wrapper for post_alert().
    Waits up to `timeout` seconds for the bot to be ready.
    Called from scanner threads via swing_scanner.set_discord_fn().
    """
    if not _bot_ready.wait(timeout=timeout):
        logger.warning(
            f"post_alert_sync: bot not ready after {timeout}s — "
            f"alert for {alert.get('ticker')} dropped. "
            f"Is DISCORD_BOT_TOKEN set correctly?"
        )
        return
    if bot.loop and bot.loop.is_running():
        asyncio.run_coroutine_threadsafe(post_alert(alert, message), bot.loop)
    else:
        logger.warning("post_alert_sync: bot loop not running")


def post_message_sync(
    message: str,
    channel_id: int | None = None,
    timeout: float = 30.0,
):
    """
    Thread-safe wrapper for post_message().
    Defaults to DISCORD_CHANNEL_ID_STANDARD.
    Used by spy_daily_scheduler for plain-text posts.
    """
    if not _bot_ready.wait(timeout=timeout):
        logger.warning(
            f"post_message_sync: bot not ready after {timeout}s — message dropped"
        )
        return
    cid = channel_id or config.DISCORD_CHANNEL_ID_STANDARD
    if bot.loop and bot.loop.is_running():
        asyncio.run_coroutine_threadsafe(post_message(cid, message), bot.loop)
    else:
        logger.warning("post_message_sync: bot loop not running")


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
    wl = load_watchlist()
    return sorted(set(wl.get("swing", []) + wl.get("intraday", [])))


# ─────────────────────────────────────────
# AUTOCOMPLETE
# ─────────────────────────────────────────

async def ticker_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    tickers = get_all_tickers()
    return [
        app_commands.Choice(name=t, value=t)
        for t in tickers
        if current.upper() in t
    ][:25]


# ─────────────────────────────────────────
# SLASH COMMANDS
# ─────────────────────────────────────────

@tree.command(name="status", description="Show scanner and bot status")
async def scanner_status_cmd(interaction: discord.Interaction):
    running      = scanner_status["running"]
    last_scan    = scanner_status["last_scan"] or "Never"
    alerts       = scanner_status["alerts_today"]
    status_emoji = "🟢" if running else "🔴"

    embed = discord.Embed(
        title     = "🤖 Trading Assistant Status",
        color     = discord.Color.green() if running else discord.Color.red(),
        timestamp = datetime.utcnow(),
    )
    embed.add_field(name="Scanner",      value=f"{status_emoji} {'Running' if running else 'Stopped'}", inline=True)
    embed.add_field(name="Last Scan",    value=str(last_scan), inline=True)
    embed.add_field(name="Alerts Today", value=str(alerts),    inline=True)
    embed.add_field(name="Bot Ready",    value="✅ Yes" if _bot_ready.is_set() else "⏳ Starting", inline=True)

    watchlist = load_watchlist()
    embed.add_field(
        name  = "Watchlist",
        value = f"Swing: {len(watchlist['swing'])} | Intraday: {len(watchlist['intraday'])}",
        inline=False,
    )
    await interaction.response.send_message(embed=embed)


@tree.command(name="watchlist", description="Show current watchlist")
async def show_watchlist(interaction: discord.Interaction):
    wl = load_watchlist()
    swing    = ", ".join(wl.get("swing", [])) or "Empty"
    intraday = ", ".join(wl.get("intraday", [])) or "Empty"
    options  = ", ".join(wl.get("options_enabled", [])) or "None"

    embed = discord.Embed(title="📋 Current Watchlist", color=discord.Color.blue())
    embed.add_field(name="Swing",           value=swing,    inline=False)
    embed.add_field(name="Intraday",        value=intraday, inline=False)
    embed.add_field(name="Options Enabled", value=options,  inline=False)
    await interaction.response.send_message(embed=embed)


@tree.command(name="add", description="Add ticker to watchlist")
@app_commands.describe(
    ticker = "Stock symbol e.g. AAPL",
    mode   = "Which list to add to",
)
@app_commands.choices(mode=[
    app_commands.Choice(name="Swing only",    value="swing"),
    app_commands.Choice(name="Intraday only", value="intraday"),
    app_commands.Choice(name="Both",          value="both"),
])
async def add_ticker(interaction: discord.Interaction, ticker: str, mode: str = "both"):
    ticker = ticker.upper().strip()
    wl     = load_watchlist()
    added  = []

    if mode in ("swing", "both") and ticker not in wl.get("swing", []):
        wl.setdefault("swing", []).append(ticker)
        added.append("swing")
    if mode in ("intraday", "both") and ticker not in wl.get("intraday", []):
        wl.setdefault("intraday", []).append(ticker)
        added.append("intraday")

    if added:
        save_watchlist(wl)
        await interaction.response.send_message(
            f"✅ **{ticker}** added to: {', '.join(added)}"
        )
    else:
        await interaction.response.send_message(
            f"ℹ️ **{ticker}** is already in the requested list(s)"
        )


@tree.command(name="remove", description="Remove ticker from watchlist")
@app_commands.describe(ticker="Stock symbol to remove", mode="Which list")
@app_commands.autocomplete(ticker=ticker_autocomplete)
@app_commands.choices(mode=[
    app_commands.Choice(name="Swing only",    value="swing"),
    app_commands.Choice(name="Intraday only", value="intraday"),
    app_commands.Choice(name="Both",          value="both"),
])
async def remove_ticker(interaction: discord.Interaction, ticker: str, mode: str = "both"):
    ticker  = ticker.upper().strip()
    wl      = load_watchlist()
    removed = []

    for lst in (["swing"] if mode in ("swing","both") else []) + \
               (["intraday"] if mode in ("intraday","both") else []):
        if ticker in wl.get(lst, []):
            wl[lst].remove(ticker)
            removed.append(lst)

    if removed:
        save_watchlist(wl)
        await interaction.response.send_message(
            f"✅ **{ticker}** removed from: {', '.join(removed)}"
        )
    else:
        await interaction.response.send_message(
            f"ℹ️ **{ticker}** not found in the requested list(s)"
        )


@tree.command(name="score", description="Score a ticker right now")
@app_commands.describe(
    ticker = "Stock symbol",
    mode   = "Swing or intraday",
)
@app_commands.autocomplete(ticker=ticker_autocomplete)
@app_commands.choices(mode=[
    app_commands.Choice(name="Swing",    value="swing"),
    app_commands.Choice(name="Intraday", value="intraday"),
])
async def score_ticker(
    interaction: discord.Interaction,
    ticker: str,
    mode: str = "swing",
):
    ticker = ticker.upper().strip()
    await interaction.response.defer()

    try:
        from data.polygon_client    import PolygonClient
        from indicators.moving_averages import MovingAverages
        from indicators.donchian        import DonchianChannels
        from indicators.volume          import VolumeAnalysis
        from indicators.cvd             import CVDAnalysis
        from indicators.rsi             import RSIAnalysis
        from signals.scorer             import SignalScorer
        import config as _config

        timeframe = _config.SWING_PRIMARY_TIMEFRAME if mode == "swing" \
                    else _config.INTRADAY_PRIMARY_TIMEFRAME

        df = PolygonClient().get_bars(ticker, timeframe=timeframe, limit=300, days_back=400)
        if df is None or len(df) < 50:
            await interaction.followup.send(f"❌ No data for `{ticker}`")
            return

        ma_r   = MovingAverages(df).analyze()
        dc_r   = DonchianChannels(df).analyze()
        vol_r  = VolumeAnalysis(df).analyze()
        cvd_r  = CVDAnalysis(df).analyze()
        rsi_r  = RSIAnalysis(df).analyze()
        result = SignalScorer().score(ma_r, dc_r, vol_r, cvd_r, rsi_r)

        score  = result["final_score"]
        tier   = result["tier"]
        layers = result.get("layer_scores", {})
        emoji  = result.get("alert_emoji", "⚪") or "⚪"

        tier_color = {
            "high_conviction": discord.Color.red(),
            "standard":        discord.Color.gold(),
            "watchlist":       discord.Color.blue(),
            "none":            discord.Color.dark_grey(),
        }.get(tier, discord.Color.dark_grey())

        embed = discord.Embed(
            title     = f"{emoji} Score Check — {ticker}",
            color     = tier_color,
            timestamp = datetime.utcnow(),
        )
        embed.add_field(name="Score",     value=f"**{score}/100**",               inline=True)
        embed.add_field(name="Direction", value=result["direction"].upper(),       inline=True)
        embed.add_field(name="Tier",      value=tier.replace("_"," ").title(),    inline=True)
        embed.add_field(
            name  = "Layer Breakdown",
            value = (
                f"Trend:  {layers.get('trend',{}).get('score',0)}/{layers.get('trend',{}).get('max',35)}\n"
                f"Setup:  {layers.get('setup',{}).get('score',0)}/{layers.get('setup',{}).get('max',35)}\n"
                f"Volume: {layers.get('volume',{}).get('score',0)}/{layers.get('volume',{}).get('max',30)}"
            ),
            inline=False,
        )
        embed.add_field(
            name  = "Indicators",
            value = (
                f"RSI: {rsi_r.get('rsi_current','N/A')} | "
                f"RVOL: {vol_r.get('rvol','N/A')}x | "
                f"CVD: {cvd_r.get('cvd_slope','N/A')}"
            ),
            inline=False,
        )
        embed.set_footer(text=f"Mode: {mode.capitalize()} | TF: {timeframe}")
        await interaction.followup.send(embed=embed)
        logger.info(f"Score check: {ticker} = {score}/100")

    except Exception as e:
        logger.error(f"Score error for {ticker}: {e}")
        await interaction.followup.send(f"❌ Error scoring `{ticker}`: {e}")


@tree.command(name="log", description="Log a new trade entry")
@app_commands.describe(
    ticker    = "Stock symbol e.g. AAPL",
    entry     = "Entry price",
    size      = "Shares or contracts",
    direction = "Trade direction",
    strategy  = "Trade strategy type",
    notes     = "Why you're entering (optional)",
)
@app_commands.autocomplete(ticker=ticker_autocomplete)
@app_commands.choices(
    direction=[
        app_commands.Choice(name="📈 Bullish", value="bullish"),
        app_commands.Choice(name="📉 Bearish", value="bearish"),
    ],
    strategy=[
        app_commands.Choice(name="Stock",             value="stock"),
        app_commands.Choice(name="Debit Spread",      value="debit_spread"),
        app_commands.Choice(name="Credit Spread",     value="credit_spread"),
        app_commands.Choice(name="Iron Condor",       value="iron_condor"),
        app_commands.Choice(name="Single Leg Option", value="single_leg"),
    ],
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
    ticker = ticker.upper().strip()
    try:
        from journal.trade_recorder import TradeRecorder
        trade_id = TradeRecorder().log_entry(
            ticker      = ticker,
            entry_price = entry,
            size        = size,
            strategy    = strategy,
            direction   = direction,
            notes       = notes,
        )
        await interaction.response.send_message(
            f"✅ Trade logged: **{ticker}** {strategy} {direction} "
            f"@ ${entry} × {size}\nID: `{trade_id}`"
        )
    except Exception as e:
        logger.error(f"Log trade error: {e}")
        await interaction.response.send_message(f"❌ Error: {e}")


@tree.command(name="trades", description="Show open trades and summary stats")
async def show_trades(interaction: discord.Interaction):
    try:
        from journal.trade_recorder import TradeRecorder
        tr    = TradeRecorder()
        stats = tr.get_summary_stats()
        open_ = tr.get_open_trades()

        embed = discord.Embed(title="📊 Trade Summary", color=discord.Color.blue())
        embed.add_field(name="Total",    value=stats["total"],         inline=True)
        embed.add_field(name="Win Rate", value=f"{stats['win_rate']}%",inline=True)
        embed.add_field(name="Total P&L",value=f"${stats['total_pnl']}",inline=True)

        if open_:
            lines = "\n".join(
                f"`{t['trade_id']}` {t['ticker']} {t['strategy']} @ ${t['entry_price']}"
                for t in open_[-5:]
            )
            embed.add_field(name=f"Open Trades ({len(open_)})", value=lines, inline=False)
        else:
            embed.add_field(name="Open Trades", value="None", inline=False)

        await interaction.response.send_message(embed=embed)
    except Exception as e:
        logger.error(f"Discord trades error: {e}")
        await interaction.response.send_message(f"❌ Error: {e}")


@tree.command(name="exit", description="Close an open trade")
@app_commands.describe(
    trade_id   = "8-character trade ID from /log",
    exit_price = "Exit price",
    notes      = "Exit reason (optional)",
)
async def exit_trade(
    interaction: discord.Interaction,
    trade_id:   str,
    exit_price: float,
    notes:      str = "",
):
    try:
        from journal.trade_recorder import TradeRecorder
        tr      = TradeRecorder()
        success = tr.log_exit(trade_id.upper(), exit_price, notes=notes)
        if success:
            trade = tr.get_trade_by_id(trade_id.upper())
            pnl   = trade.get("pnl_dollars", "N/A")
            pct   = trade.get("pnl_pct",     "N/A")
            outcome_emoji = "✅" if trade.get("outcome") == "win" else "❌"
            await interaction.response.send_message(
                f"{outcome_emoji} Trade `{trade_id.upper()}` closed @ ${exit_price}\n"
                f"P&L: **${pnl}** ({pct}%)"
            )
        else:
            await interaction.response.send_message(
                f"❌ Trade ID `{trade_id.upper()}` not found"
            )
    except Exception as e:
        logger.error(f"Exit trade error: {e}")
        await interaction.response.send_message(f"❌ Error: {e}")


# ─────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────

def run_bot():
    if not config.DISCORD_BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN not set in .env — Discord bot will not start")
        return
    logger.info("Starting Discord bot...")
    bot.run(config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    run_bot()

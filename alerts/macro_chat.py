"""
alerts/macro_chat.py -- Macro-aware Claude chat (general, not per-alert).

Companion to the per-alert chat at /alerts/{id}/chat. While that one is
scoped to a single alert, this one has the FULL daily picture:

    - Today's morning brief (regime, play, skip/watch, narrative)
    - Latest macro snapshots (VIX term structure + sector breadth)
    - Today's events from event_calendar (next 48h)
    - Last 30 knowledge-base entries (the self-learning loop's findings)
    - Recent trades (last 20 from TradeRecorder)
    - 60-day prediction accuracy

Use cases:
    "Should I take today's play given the Fed at 2pm?"
    "What happened the last 3 times we saw choppy_low_vol with VIX > 16?"
    "How did my last 5 iron condor trades go?"
    "Is the edge weakening this month?"

Persistence:
    Chat turns go to logs/macro_chat.jsonl (one JSON object per line:
    {role, content, ts}). File-based so we don't add a new SQL table.
    History is small (hundreds of turns max) so we load it all on each call.

Prompt caching:
    The big context bundle (preamble + today's brief + KB) is cached so
    successive turns within a session stay cheap.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger

from journal.plan_logger      import PlanLogger
from journal.trade_recorder   import TradeRecorder
from learning.knowledge_base  import KnowledgeBase
from learning.portfolio_greeks import PortfolioGreeks
from learning.predictions     import PredictionLog
from signals                  import macro_runner


# ── CONFIG ────────────────────────────────────────────
CLAUDE_MODEL       = "claude-sonnet-4-6"
KB_RECENT_DAYS     = 30
TRADES_RECENT_N    = 20
PREDICTIONS_WINDOW = 60   # days
MAX_HISTORY_TURNS  = 50   # cap sent to Claude


CHAT_LOG_FILE = "macro_chat.jsonl"


# ── SYSTEM PROMPT (static — cacheable) ────────────────

_SYSTEM_PROMPT = """You are the trader's daily decision-support assistant.

You see a structured snapshot of everything the trading system knows
about today and the recent past: the morning brief, current macro
state, the self-learning knowledge base, recent trades, and prediction
accuracy. Use that context to answer questions specific to TODAY's
situation.

RULES
- Be specific to the provided context. Reference actual numbers,
  regime names, KB entries by date, trades by ticker.
- Do NOT invent data. If the answer isn't in the context, say "I don't
  have that in today's bundle" -- don't speculate.
- If asked about historical patterns, search the KB entries provided.
  Cite them by date and category.
- Be honest about contradictions: if the morning brief says iron condor
  but VIX TS is in stress flag, flag the conflict.
- Keep replies under 300 words unless the user asks for more depth.
- Do NOT tell the user to "buy now" or place orders. You inform; they
  decide. Always recommend paper trading first when discussing live execution.
- No generic trading advice. No "always have a stop loss" filler.
"""


# ─────────────────────────────────────────
# MACRO CHAT
# ─────────────────────────────────────────

class MacroChat:
    """Aggregates all context, calls Claude, persists history."""

    def __init__(
        self,
        plan_logger:        PlanLogger | None     = None,
        trade_recorder:     TradeRecorder | None  = None,
        knowledge_base:     KnowledgeBase | None  = None,
        prediction_log:     PredictionLog | None  = None,
        event_calendar    = None,
        earnings_calendar = None,
        api_key:            str | None            = None,
    ):
        self.plans       = plan_logger    or PlanLogger()
        self.trades      = trade_recorder or TradeRecorder()
        self.kb          = knowledge_base or KnowledgeBase()
        self.predictions = prediction_log or PredictionLog()
        self.events      = event_calendar
        self.earnings    = earnings_calendar
        self.api_key     = api_key or os.getenv("ANTHROPIC_API_KEY")

    # ── HISTORY ───────────────────────────────────────

    @property
    def _history_path(self) -> str:
        os.makedirs(config.LOG_DIR, exist_ok=True)
        return os.path.join(config.LOG_DIR, CHAT_LOG_FILE)

    def history(self, limit: int = MAX_HISTORY_TURNS) -> list[dict]:
        path = self._history_path
        if not os.path.exists(path):
            return []
        out: list[dict] = []
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError as e:
            logger.warning(f"MacroChat: history read failed: {e}")
            return []
        return out[-limit:]

    def append_turn(self, role: str, content: str) -> None:
        if role not in ("user", "assistant"):
            raise ValueError(f"role must be 'user' or 'assistant', got {role!r}")
        row = {
            "role":    role,
            "content": content,
            "ts":      datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            with open(self._history_path, "a") as f:
                f.write(json.dumps(row) + "\n")
        except OSError as e:
            logger.warning(f"MacroChat: history append failed: {e}")

    def reset_history(self) -> None:
        path = self._history_path
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError as e:
                logger.warning(f"MacroChat: history reset failed: {e}")

    # ── CONTEXT BUILDING ──────────────────────────────

    def build_context(self) -> dict:
        """Aggregate every source. Pure dict so it's easy to inspect / test."""
        today_iso  = date.today().isoformat()
        plan       = self.plans.get_plan(today_iso) or {}
        vix_ts     = macro_runner.get_latest_vix()    or {}
        sector     = macro_runner.get_latest_sector() or {}
        events     = self._safe_events(days=2)
        earnings   = self._safe_earnings(days=7)
        kb_recent  = self._safe(lambda: self.kb.recent(KB_RECENT_DAYS)) or []
        trades     = self._safe(lambda: self.trades.get_all_trades()) or []
        recent_t   = list(reversed(trades))[:TRADES_RECENT_N]
        pred_acc   = self._safe(lambda: self.predictions.accuracy(PREDICTIONS_WINDOW)) or {}

        greeks = self._safe(lambda: PortfolioGreeks(trade_recorder=self.trades).compute()) or {}

        return {
            "today":              today_iso,
            "morning_brief":      plan,
            "vix_term_structure": vix_ts,
            "sector_breadth":     sector,
            "events_next_48h":    events,
            "earnings_next_7d":   earnings,
            "portfolio_greeks":   greeks,
            "kb_recent":          kb_recent,
            "recent_trades":      recent_t,
            "prediction_accuracy": pred_acc,
        }

    def context_summary(self, ctx: dict | None = None) -> str:
        """One-line breadcrumb shown above the chat input ('what Claude sees')."""
        ctx = ctx or self.build_context()
        brief = ctx.get("morning_brief") or {}
        vix   = ctx.get("vix_term_structure") or {}
        sect  = ctx.get("sector_breadth") or {}
        evts  = ctx.get("events_next_48h") or []
        kb_n  = len(ctx.get("kb_recent") or [])
        tr_n  = len(ctx.get("recent_trades") or [])
        pa    = ctx.get("prediction_accuracy") or {}

        bits = []
        regime = brief.get("regime") or "no brief"
        bits.append(f"brief: {regime}")
        if vix.get("flag"):
            bits.append(f"VIX TS {vix['flag']}")
        if sect.get("signal"):
            bits.append(f"sectors {sect['signal']}")
        if evts:
            bits.append(f"events {len(evts)} in 48h")
        ern_n = len(ctx.get("earnings_next_7d") or [])
        if ern_n:
            bits.append(f"earnings {ern_n}/7d")
        gk = ctx.get("portfolio_greeks") or {}
        if gk.get("open_trade_count"):
            bits.append(f"Δ {(gk.get('total') or {}).get('delta', 0):+.0f}")
        bits.append(f"KB {kb_n}/30d")
        bits.append(f"trades {tr_n}")
        if pa.get("sample"):
            bits.append(f"pred {pa.get('accuracy')}% n={pa.get('sample')}")
        return " | ".join(bits)

    # ── CLAUDE CALL ───────────────────────────────────

    def ask(self, user_message: str) -> str:
        if not user_message or not user_message.strip():
            return "Empty message — type something to ask."
        if not self.api_key:
            return "ANTHROPIC_API_KEY is not configured — chat is unavailable."

        ctx     = self.build_context()
        history = self.history()

        try:
            import anthropic
        except ImportError:
            return "anthropic SDK not installed — chat is unavailable."

        client = anthropic.Anthropic(api_key=self.api_key)
        system_blocks = [
            {"type": "text", "text": _SYSTEM_PROMPT,           "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": self._format_context_block(ctx)},
        ]
        messages = [{"role": h["role"], "content": h["content"]} for h in history]
        messages.append({"role": "user", "content": user_message.strip()})

        try:
            resp = client.messages.create(
                model      = CLAUDE_MODEL,
                max_tokens = 1024,
                system     = system_blocks,
                messages   = messages,
            )
            text = "".join(
                b.text for b in resp.content if getattr(b, "type", None) == "text"
            ).strip()
            reply = text or "(empty response)"
        except anthropic.AuthenticationError:
            return "Invalid ANTHROPIC_API_KEY — check your .env file."
        except Exception as e:
            logger.error(f"MacroChat Claude call failed: {e}")
            return f"Chat error: {e}"

        # Persist both turns AFTER a successful reply so failed calls don't
        # pollute the conversation.
        self.append_turn("user",      user_message.strip())
        self.append_turn("assistant", reply)
        return reply

    # ── INTERNAL FORMATTERS ───────────────────────────

    @staticmethod
    def _format_context_block(ctx: dict) -> str:
        brief = ctx.get("morning_brief") or {}
        vix   = ctx.get("vix_term_structure") or {}
        sect  = ctx.get("sector_breadth") or {}
        evts  = ctx.get("events_next_48h") or []
        kb    = ctx.get("kb_recent") or []
        trs   = ctx.get("recent_trades") or []
        pa    = ctx.get("prediction_accuracy") or {}

        parts = [
            f"# TODAY ({ctx.get('today')})", "",
            "## MORNING BRIEF",
            json.dumps({
                "regime":           brief.get("regime"),
                "play":             brief.get("play") or brief.get("action"),
                "strategy":         brief.get("strategy"),
                "rr_ratio":         brief.get("rr_ratio"),
                "recommended_dte":  brief.get("recommended_dte"),
                "max_profit":       brief.get("max_profit"),
                "max_loss":         brief.get("max_loss"),
                "exit_rule":        brief.get("exit_rule"),
                "narrative":        brief.get("narrative"),
                "skip_conditions":  brief.get("skip_conditions"),
                "watch_conditions": brief.get("watch_conditions"),
                "thesis":           brief.get("thesis"),
            }, indent=2, default=str),
            "",
            "## MACRO",
            json.dumps({
                "vix_term_structure": {
                    "VIX":  vix.get("VIX"),
                    "VIX3M": vix.get("VIX3M"),
                    "ratio": vix.get("ratio"),
                    "flag":  vix.get("flag"),
                },
                "sector_breadth": {
                    "signal":     sect.get("signal"),
                    "dispersion": sect.get("dispersion"),
                    "leaders":    sect.get("leaders"),
                    "laggards":   sect.get("laggards"),
                },
                "events_next_48h": evts,
            }, indent=2, default=str),
            "",
        ]

        ern = ctx.get("earnings_next_7d") or []
        parts += [
            f"## EARNINGS NEXT 7D ({len(ern)} watchlist tickers)",
            *([f"  - {e.get('ticker')}: {e.get('earnings_date')} "
               f"({e.get('days_away')}d away)" for e in ern] or ["  (none)"]),
            "",
            f"## KNOWLEDGE BASE (last {len(kb)} entries, {KB_RECENT_DAYS}d window)",
        ]

        for e in kb:
            parts.append(
                f"- [{e.get('date')} | {e.get('category')} | conf {e.get('confidence', 0):.2f}] "
                f"{(e.get('claim') or '')[:240]}"
            )

        greeks = ctx.get("portfolio_greeks") or {}
        total  = greeks.get("total") or {}
        parts += [
            "",
            "## PORTFOLIO GREEKS (open positions)",
            f"  open_trades: {greeks.get('open_trade_count', 0)}",
            f"  total_delta: {total.get('delta', 0)}  (share-equivalents)",
            f"  total_theta: {total.get('theta', 0)}  (dollars/day)",
            f"  total_vega:  {total.get('vega', 0)}",
        ]

        parts += [
            "",
            f"## RECENT TRADES (last {len(trs)})",
        ]
        for t in trs:
            parts.append(
                f"- {t.get('ticker')} | {t.get('strategy') or t.get('trade_type')} "
                f"| outcome={t.get('outcome')} | pnl=${t.get('pnl_dollars')}"
            )

        parts += [
            "",
            f"## PREDICTIONS",
            f"60d accuracy: {pa.get('accuracy', '?')}% over {pa.get('sample', 0)} resolved",
        ]
        return "\n".join(parts)

    # ── HELPERS ───────────────────────────────────────

    def _safe_events(self, days: int) -> list[dict]:
        if not self.events:
            return []
        try:
            upcoming = self.events.get_next_events(days=days) or []
        except Exception as e:
            logger.warning(f"MacroChat: event_calendar fetch failed: {e}")
            return []
        return [e for e in upcoming if e.get("days_away", 99) <= days]

    def _safe_earnings(self, days: int) -> list[dict]:
        if not self.earnings:
            return []
        try:
            return self.earnings.get_upcoming(days=days) or []
        except Exception as e:
            logger.warning(f"MacroChat: earnings_calendar fetch failed: {e}")
            return []

    @staticmethod
    def _safe(fn):
        try:
            return fn()
        except Exception as e:
            logger.warning(f"MacroChat: data source failed: {e}")
            return None

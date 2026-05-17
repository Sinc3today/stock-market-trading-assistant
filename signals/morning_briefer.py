"""
signals/morning_briefer.py -- Morning play card synthesizer.

Wraps SPYDailyStrategy with macro context (VIX term structure + sector
breadth + today's events) and asks Claude to produce three things the
raw strategy can't:

    1. A one-paragraph narrative thesis -- why this play, given everything.
    2. Skip conditions    -- when NOT to take the play today.
    3. Watch conditions   -- contingencies ("if X happens, switch to Y").

The output is a richer dict that subsumes the SPY daily play card. It
flows to:
    - PlanLogger    (so /today route and tomorrow's tools can read it)
    - Pushover      (short summary -- phone-first delivery)
    - Discord       (rich card with all sections)
    - logs/morning_briefs/<date>.json  (archival)

Usage:
    briefer = MorningBriefer(
        spy_strategy   = SPYDailyStrategy(polygon, vix, ivr),
        event_calendar = EventCalendar(),
    )
    brief = briefer.build_today()
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from loguru import logger

from signals       import macro_runner
from journal.plan_logger import PlanLogger


CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL   = "claude-sonnet-4-5-20250929"

BRIEFER_SYSTEM = """You are the trading assistant's morning brief synthesizer.

You receive:
- Today's regime classification + recommended SPY options play
- VIX term structure (flag, contango ratio)
- Sector breadth (signal, leaders, laggards from yesterday)
- Today's high-impact events (FOMC, CPI, NFP)

You must return ONLY a JSON object with these three fields:

{
  "narrative":        "<= 240 chars. One-paragraph thesis. Reference actual numbers from the inputs. Honest about contradictions.",
  "skip_conditions":  ["concrete skip rule 1", "...up to 3 items"],
  "watch_conditions": ["concrete contingency 1", "...up to 3 items"]
}

Rules:
- Skip and watch conditions are short imperative phrases referencing actual prices, indicators, or events. Example: "Skip if VIX opens > 17", "Switch to iron condor if SPY gaps less than 0.3%".
- If the macro context CONTRADICTS the recommended play (e.g. VIX backwardation flag with an iron condor play), flag that explicitly in skip_conditions.
- If there's a high-impact event today (FOMC, CPI), skip_conditions should mention skipping until after the release.
- Do NOT recommend a different play -- the regime detector has already chosen. Your job is to add context, not override.
- Do NOT include disclaimers, intros, or markdown. Pure JSON only.
"""


class MorningBriefer:
    """
    Enriches the daily SPY play with macro context + Claude-synthesized
    narrative, skip, and watch conditions.

    Deterministic without a Claude API key -- falls back to rule-based
    skip/watch conditions so the brief still gets produced.
    """

    def __init__(
        self,
        spy_strategy,
        event_calendar = None,
        plan_logger:   PlanLogger | None = None,
        api_key:       str | None = None,
    ):
        self.spy_strategy   = spy_strategy
        self.event_calendar = event_calendar
        self.plans          = plan_logger or PlanLogger()
        self.api_key        = api_key or os.getenv("ANTHROPIC_API_KEY")

    # ── MAIN ──────────────────────────────────────────

    def build_today(self, today: date | None = None) -> dict:
        today     = today or date.today()
        today_str = today.isoformat()

        base = self.spy_strategy.build_today(today=today)
        macro = self._gather_macro_context()

        prompt = self._format_claude_prompt(today_str, base, macro)
        raw    = self._call_claude(prompt)
        parsed, parse_err = self._parse_reply(raw)

        if parsed:
            narrative        = parsed.get("narrative", "").strip()
            skip_conditions  = self._clean_list(parsed.get("skip_conditions", []))
            watch_conditions = self._clean_list(parsed.get("watch_conditions", []))
        else:
            narrative, skip_conditions, watch_conditions = self._fallback_synthesis(base, macro)
            if parse_err:
                logger.warning(f"MorningBriefer: Claude parse failed ({parse_err}) -- using fallback")

        brief = {
            **base,
            "macro_context":    macro,
            "narrative":        narrative,
            "skip_conditions":  skip_conditions,
            "watch_conditions": watch_conditions,
            "pushover_message": self._format_pushover(base, narrative, skip_conditions),
            "discord_message":  self._format_discord(base, macro, narrative,
                                                     skip_conditions, watch_conditions),
        }

        # Persist: PlanLogger + archival JSON
        self._save_plan(today_str, brief)
        self._save_archive(today_str, brief)

        logger.info(
            f"MorningBriefer: {today_str} | regime={brief['regime']} | "
            f"tradeable={brief['tradeable']} | skip={len(skip_conditions)} | "
            f"watch={len(watch_conditions)}"
        )
        return brief

    # ── CONTEXT GATHERING ─────────────────────────────

    def _gather_macro_context(self) -> dict:
        vix    = macro_runner.get_latest_vix()    or {}
        sector = macro_runner.get_latest_sector() or {}
        events = self._get_today_events()
        return {
            "vix_ts":   vix,
            "sector":   sector,
            "events":   events,
        }

    def _get_today_events(self) -> list[dict]:
        if not self.event_calendar:
            return []
        try:
            upcoming = self.event_calendar.get_next_events(days=2) or []
        except Exception as e:
            logger.warning(f"MorningBriefer: event_calendar fetch failed: {e}")
            return []
        # Keep only events 0-1 days away (today + tomorrow)
        return [e for e in upcoming if e.get("days_away", 99) <= 1]

    # ── CLAUDE ────────────────────────────────────────

    def _format_claude_prompt(self, today_str: str, base: dict, macro: dict) -> str:
        regime    = base.get("regime")
        play      = base.get("play")
        tradeable = base.get("tradeable")
        opts      = base.get("options") or {}
        metrics   = base.get("metrics") or {}
        reasons   = base.get("reasons") or []

        vix_ts   = macro.get("vix_ts")   or {}
        sector   = macro.get("sector")   or {}
        events   = macro.get("events")   or []

        ctx_lines = [
            f"DATE: {today_str}",
            f"",
            f"REGIME: {regime}  (tradeable={tradeable})",
            f"RECOMMENDED PLAY: {play}",
            f"Reasons:",
            *[f"  - {r}" for r in reasons],
            f"",
            f"REGIME METRICS:",
            f"  SPY close:  {metrics.get('spy_close')}",
            f"  VIX:        {metrics.get('vix')}",
            f"  IVR:        {metrics.get('ivr')}",
            f"  ADX:        {metrics.get('adx')}",
            f"  MA200 dist: {metrics.get('ma200_dist_%')}%",
            f"",
            f"OPTIONS STRUCTURE:",
            f"  strategy:    {opts.get('strategy')}",
            f"  max_profit:  {opts.get('max_profit')}",
            f"  max_loss:    {opts.get('max_loss')}",
            f"  R/R:         {opts.get('rr_ratio')}",
            f"  DTE:         {opts.get('recommended_dte')}",
            f"  exit rule:   {opts.get('exit_rule')}",
            f"",
            f"VIX TERM STRUCTURE:",
            f"  flag:   {vix_ts.get('flag')}",
            f"  ratio:  {vix_ts.get('ratio')}",
            f"  VIX={vix_ts.get('VIX')}  VIX3M={vix_ts.get('VIX3M')}",
            f"",
            f"SECTOR BREADTH (from yesterday):",
            f"  signal:     {sector.get('signal')}",
            f"  dispersion: {sector.get('dispersion')}",
            f"  leaders:    {sector.get('leaders')}",
            f"  laggards:   {sector.get('laggards')}",
            f"",
            f"EVENTS NEXT 48H:",
            *([f"  - {e.get('event')} ({e.get('days_away')}d away)"
               for e in events] or ["  (none)"]),
            f"",
            f"Produce the JSON now.",
        ]
        return "\n".join(ctx_lines)

    def _call_claude(self, prompt: str) -> str:
        if not self.api_key:
            logger.info("MorningBriefer: no API key -- skipping Claude pass")
            return ""
        import requests
        try:
            resp = requests.post(
                CLAUDE_API_URL,
                headers = {
                    "Content-Type":      "application/json",
                    "x-api-key":         self.api_key,
                    "anthropic-version": "2023-06-01",
                },
                json = {
                    "model":      CLAUDE_MODEL,
                    "max_tokens": 1200,
                    "system":     BRIEFER_SYSTEM,
                    "messages":   [{"role": "user", "content": prompt}],
                },
                timeout = 60,
            )
            resp.raise_for_status()
            data = resp.json()
            return "".join(
                b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
            )
        except Exception as e:
            logger.error(f"MorningBriefer Claude call failed: {e}")
            return ""

    @staticmethod
    def _parse_reply(text: str) -> tuple[dict | None, str | None]:
        if not text:
            return None, "empty reply"
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None, "no JSON object found"
        try:
            return json.loads(m.group(0)), None
        except json.JSONDecodeError as e:
            return None, f"json error: {e}"

    @staticmethod
    def _clean_list(items) -> list[str]:
        if not isinstance(items, list):
            return []
        return [str(x).strip() for x in items if str(x).strip()][:3]

    # ── FALLBACK SYNTHESIS (no Claude / parse fail) ─

    @staticmethod
    def _fallback_synthesis(base: dict, macro: dict) -> tuple[str, list[str], list[str]]:
        """Rule-based brief used when Claude is unavailable."""
        regime    = base.get("regime", "unknown")
        play      = base.get("play", "")
        tradeable = base.get("tradeable", False)
        vix_flag  = (macro.get("vix_ts") or {}).get("flag")
        events    = macro.get("events") or []

        skip:  list[str] = []
        watch: list[str] = []

        if not tradeable:
            narrative = f"Regime is {regime} — skip conditions met. {play}."
            return narrative, skip, watch

        narrative = f"{regime}: {play}. Conditions look constructive."

        if vix_flag in ("stress", "extreme_stress"):
            skip.append(f"Skip: VIX term structure {vix_flag} contradicts the play")
        if events:
            for e in events:
                if e.get("days_away") == 0:
                    skip.append(f"Skip until after {e.get('event')} today")

        return narrative, skip, watch

    # ── PERSISTENCE ───────────────────────────────────

    def _save_plan(self, today_str: str, brief: dict) -> None:
        """Persist a plan payload so /today and other tools can read it."""
        try:
            payload = brief.get("plan_payload") or {}
            payload.update({
                "narrative":        brief.get("narrative"),
                "skip_conditions":  brief.get("skip_conditions"),
                "watch_conditions": brief.get("watch_conditions"),
                "macro_context":    brief.get("macro_context"),
            })
            self.plans.save_plan(payload)
        except Exception as e:
            logger.warning(f"MorningBriefer: plan save failed: {e}")

    def _save_archive(self, today_str: str, brief: dict) -> None:
        path = os.path.join(config.LOG_DIR, "morning_briefs", f"{today_str}.json")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(brief, f, indent=2, default=str)
        except OSError as e:
            logger.warning(f"MorningBriefer: archive save failed ({path}): {e}")

    # ── FORMATTERS ────────────────────────────────────

    @staticmethod
    def _format_pushover(base: dict, narrative: str, skip: list[str]) -> str:
        """Short body for Pushover. Title is set by the notifier."""
        regime  = base.get("regime", "?")
        play    = base.get("play", "?")
        opts    = base.get("options") or {}
        strat   = opts.get("strategy") or "—"
        rr      = opts.get("rr_ratio") or "—"
        head = f"{regime}: {play}\nStrategy: {strat} (R/R {rr})"
        if narrative:
            head += f"\n\n{narrative[:240]}"
        if skip:
            head += "\n\nSkip if:\n" + "\n".join(f"• {s}" for s in skip[:3])
        return head[:1024]

    @staticmethod
    def _format_discord(
        base: dict, macro: dict, narrative: str,
        skip: list[str], watch: list[str],
    ) -> str:
        # Start from the existing rich Discord message in `base`, then
        # append the new sections so we keep all the metric formatting
        # SPYDailyStrategy already produces.
        out = base.get("discord_message") or ""
        if narrative:
            out += f"\n\n**Thesis:** {narrative}"

        vix_ts = macro.get("vix_ts")   or {}
        sector = macro.get("sector")   or {}
        events = macro.get("events")   or []

        macro_bits = []
        if vix_ts.get("flag"):
            macro_bits.append(
                f"VIX TS `{vix_ts.get('flag')}` (ratio {vix_ts.get('ratio')})"
            )
        if sector.get("signal"):
            macro_bits.append(f"Sectors `{sector.get('signal')}`")
        if events:
            events_str = ", ".join(
                f"{e.get('event')} ({e.get('days_away')}d)" for e in events
            )
            macro_bits.append(f"Events: {events_str}")
        if macro_bits:
            out += "\n\n**Macro:** " + " | ".join(macro_bits)

        if skip:
            out += "\n\n**Skip conditions:**\n" + "\n".join(f"• {s}" for s in skip)
        if watch:
            out += "\n\n**Watch conditions:**\n" + "\n".join(f"• {w}" for w in watch)
        return out

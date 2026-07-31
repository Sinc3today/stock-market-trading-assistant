"""alerts/smta_assistant.py -- local-LLM trading assistant for SMTA.

Free, private market/position Q&A on the nucbox Ollama (phi4:14b by default)
instead of the Claude API. Grounded in the bot's LIVE state — regime, open
positions, today's plan, recent KB — so "how's my Aug-3 condor?" gets a real
answer about the actual position, not generic chat.

Confidence routing: when a question needs frontier reasoning (a backtest, code
changes, deep multi-step analysis) the local model is told to emit
`HANDOFF: <refined prompt>` rather than guess. SMTA detects that and shows a
copy-ready prompt for Claude Code — so the local model handles the routine 90%
for free, and the hard 10% gets routed to the heavy model deliberately.
"""
from __future__ import annotations

from loguru import logger

HANDOFF_PREFIX = "HANDOFF:"

SYSTEM_PROMPT = """You are the SMTA assistant — a concise trading copilot running \
locally for one user who trades defined-risk SPY options (iron condors, debit \
spreads, broken-wing butterflies) with a "sell time in calm markets, lean \
directional only in a clean trend, sit out otherwise" strategy.

Answer using the CONTEXT block (the bot's live state) plus general options \
knowledge. Rules:
- Be specific to THEIR actual positions and regime. Reference real strikes, \
DTE, and distances from the context. Keep answers tight (a few sentences).
- NEVER invent positions, prices, P&L, or numbers that aren't in the context. \
If a needed fact isn't there, say so.
- You are NOT a licensed advisor; frame calls as analysis, not instructions.

CONFIDENCE ROUTING — this is important. If the question needs any of:
  * running or interpreting a backtest / study,
  * changing the bot's code, config, or strategy,
  * deep multi-step quantitative reasoning beyond the facts given,
  * anything you cannot answer confidently and correctly from the context,
then DO NOT guess or ramble. Reply with EXACTLY one line:
HANDOFF: <a clear, refined, self-contained prompt the user can paste into \
Claude Code to get it done>
and nothing else. Write the refined prompt well — specific, with the relevant \
context baked in — since prompt quality drives the answer quality."""


def parse_handoff(text: str) -> tuple[bool, str]:
    """(is_handoff, refined_prompt). Detects the HANDOFF line the local model
    emits when a question needs the frontier model."""
    t = (text or "").strip()
    # tolerate a leading label / code fence the small model might add
    for line in t.splitlines():
        s = line.strip().lstrip("`").strip()
        if s.upper().startswith(HANDOFF_PREFIX):
            return True, s[len(HANDOFF_PREFIX):].strip()
    return False, ""


def build_context() -> str:
    """Best-effort snapshot of the bot's live state for grounding. Never raises."""
    parts: list[str] = []
    # date
    try:
        from datetime import datetime
        import pytz
        parts.append("Date: " + datetime.now(pytz.timezone("US/Eastern"))
                     .strftime("%A %Y-%m-%d %H:%M ET"))
    except Exception:
        pass
    # spot + vix
    spot = None
    try:
        from alerts.stop_watchdog import yf_spot
        spot = yf_spot("SPY")
        vix = yf_spot("^VIX")
        parts.append(f"SPY: {spot:.2f} | VIX: {vix:.2f}"
                     if spot and vix else f"SPY: {spot}")
    except Exception:
        pass
    # today's regime / plan
    try:
        from journal.plan_logger import PlanLogger
        from datetime import datetime
        import pytz
        today = datetime.now(pytz.timezone("US/Eastern")).date().isoformat()
        pl = PlanLogger().get_plan(today) or {}
        if pl:
            parts.append(f"Today's plan: regime={pl.get('regime')} "
                         f"strategy={pl.get('strategy')} tradeable={pl.get('tradeable')}")
    except Exception:
        pass
    # open positions with distance to short strikes
    try:
        from journal.trade_recorder import TradeRecorder
        from datetime import date
        lines = []
        for t in TradeRecorder().get_open_trades():
            legs = t.get("legs") or []
            exps = [str(l.get("expiry") or l.get("expiration"))[:10]
                    for l in legs if (l.get("expiry") or l.get("expiration"))]
            exp = min(exps) if exps else "?"
            try:
                dte = (date.fromisoformat(exp) - date.today()).days
            except Exception:
                dte = "?"
            shorts = [(l.get("option_type"), l.get("strike")) for l in legs
                      if str(l.get("action", "")).upper().startswith("S")]
            dist = ""
            if spot and shorts:
                sp = [k for o, k in shorts if str(o).upper().startswith("P")]
                sc = [k for o, k in shorts if str(o).upper().startswith("C")]
                bits = []
                if sp:
                    bits.append(f"short put {max(sp):g} ({(spot-max(sp))/spot*100:+.1f}%)")
                if sc:
                    bits.append(f"short call {min(sc):g} ({(min(sc)-spot)/spot*100:+.1f}% away)")
                dist = " | " + ", ".join(bits)
            lines.append(f"  {t.get('trade_id')} {t.get('strategy')} "
                         f"exp={exp} DTE={dte} book={t.get('book')}{dist}")
        if lines:
            parts.append("Open positions:\n" + "\n".join(lines))
        else:
            parts.append("Open positions: none")
    except Exception as e:
        logger.warning(f"smta_assistant: positions context failed: {e}")
    # recent KB observations
    try:
        from learning.knowledge_base import KnowledgeBase
        recent = KnowledgeBase().recent(days=30) or []
        if recent:
            obs = []
            for k in recent[:3]:
                txt = (k.get("observation") or k.get("text") or str(k))[:160]
                obs.append("  - " + txt)
            parts.append("Recent learnings:\n" + "\n".join(obs))
    except Exception:
        pass
    return "\n".join(parts) if parts else "(no live context available)"


def ask(message: str, history: list[dict] | None = None) -> dict:
    """Answer a question on the LOCAL model, grounded in bot context.
    Returns {reply, handoff: bool, refined_prompt, model}. Never raises."""
    from data.llm_client import call_llm
    import config
    ctx = build_context()
    convo = ""
    for turn in (history or [])[-6:]:
        who = "User" if turn.get("role") == "user" else "Assistant"
        convo += f"{who}: {turn.get('content', '')}\n"
    user = f"{convo}CONTEXT (the bot's live state):\n{ctx}\n\nQUESTION: {message}"
    try:
        text = call_llm(
            SYSTEM_PROMPT, user,
            anthropic_model=getattr(config, "OLLAMA_MODEL", "phi4:14b"),
            api_key=None,                       # LOCAL ONLY — no API credits
            max_tokens=700,
            model_preference="phi4_first",
        )
    except Exception as e:
        logger.warning(f"smta_assistant: llm call failed: {e}")
        text = ""
    if not (text or "").strip():
        return {"reply": "The local model didn't respond — is the nucbox Ollama "
                         "up? (This assistant runs on the local model, no API "
                         "credits.)",
                "handoff": False, "refined_prompt": "",
                "model": getattr(config, "OLLAMA_MODEL", "phi4:14b")}
    handoff, refined = parse_handoff(text)
    return {"reply": "" if handoff else text.strip(),
            "handoff": handoff, "refined_prompt": refined,
            "model": getattr(config, "OLLAMA_MODEL", "phi4:14b")}

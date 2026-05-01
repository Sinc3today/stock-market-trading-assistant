"""
alerts/ai_advisor.py — AI Trade Advisor
Uses the Claude API for pre-trade and post-trade analysis.
Maintains conversation context within a session.
Saves all conversations to logs/ai_conversations.json.

Two modes:
    PRE-TRADE:  Analyzes a setup before entry
    POST-TRADE: Reviews a closed trade against the signal
    GENERAL:    Open Q&A with optional context

Usage:
    from alerts.ai_advisor import AIAdvisor
    advisor = AIAdvisor()
    response = advisor.pre_trade_analysis(ticker, score_result, ...)
    response = advisor.ask("What does CVD divergence mean?")
"""

import os
import sys
import json
import requests
from datetime import datetime
from loguru import logger
import pytz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import config

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL   = "claude-sonnet-4-20250514"

# System prompt — tells Claude its role once per session
SYSTEM_PROMPT = """You are an experienced trading coach and mentor embedded in a personal trading assistant.

The trader uses a systematic signal engine with these indicators:
- Moving Averages (MA 20/50/200) for trend direction
- Donchian Channels for breakout detection  
- Volume + RVOL for conviction confirmation
- CVD (Cumulative Volume Delta) for buyer/seller pressure
- RSI divergence (pivot-confirmed, Option B method) for setup quality

They trade US stocks, ETFs, and options (debit spreads, credit spreads, iron condors).
Alert tiers: Standard (75-89/100) and High Conviction (90+/100).
They mix swing trading and intraday trading.

Your role:
- Analyze setups and trades using the actual signal data provided
- Help them learn from every trade through structured post-trade review
- Identify their behavioral patterns over time
- Be direct, practical, and educational — not generic
- Never give specific financial advice or price targets
- Speak like a coach: honest, encouraging, and specific
- Keep responses focused and under 300 words unless asked for more
- Remember what was discussed earlier in this conversation"""


class AIAdvisor:
    """
    AI-powered trade advisor with persistent session context
    and conversation history logging.
    """

    def __init__(self):
        self.api_key          = os.getenv("ANTHROPIC_API_KEY")
        self.conversation     = []   # Session message history
        self.session_start    = datetime.now(pytz.timezone("US/Eastern"))
        self.session_id       = self.session_start.strftime("%Y%m%d_%H%M%S")
        self._history_path    = os.path.join(config.LOG_DIR, "ai_conversations.json")

        os.makedirs(config.LOG_DIR, exist_ok=True)

        if not self.api_key:
            logger.warning("ANTHROPIC_API_KEY not set — AI Advisor disabled")

    # ─────────────────────────────────────────
    # PUBLIC METHODS
    # ─────────────────────────────────────────

    def pre_trade_analysis(
        self,
        ticker:           str,
        score_result:     dict,
        ma_result:        dict = None,
        donchian_result:  dict = None,
        volume_result:    dict = None,
        cvd_result:       dict = None,
        rsi_result:       dict = None,
        options_context:  dict = None,
        trade_history:    list = None,
        user_question:    str  = "",
    ) -> str:
        """Ask Claude for a pre-trade analysis given indicator + score context."""
        prompt = self._build_pre_trade_prompt(
            ticker, score_result, ma_result, donchian_result,
            volume_result, cvd_result, rsi_result,
            options_context, trade_history, user_question
        )
        response = self._call_claude(prompt)
        self._save_to_history(
            mode="pre_trade", ticker=ticker,
            user_message=prompt, ai_response=response,
            metadata={"score": score_result.get("final_score"),
                      "direction": score_result.get("direction")}
        )
        return response

    def post_trade_review(
        self,
        trade:         dict,
        lesson:        dict = None,
        patterns:      dict = None,
        user_question: str  = "",
    ) -> str:
        """Ask Claude for a post-trade review given the closed trade + lesson."""
        prompt = self._build_post_trade_prompt(
            trade, lesson, patterns, user_question
        )
        response = self._call_claude(prompt)
        self._save_to_history(
            mode="post_trade",
            ticker=trade.get("ticker", ""),
            user_message=prompt,
            ai_response=response,
            metadata={"outcome": trade.get("outcome"),
                      "pnl_pct": trade.get("pnl_pct")}
        )
        return response

    def ask(
        self,
        question:     str,
        context_data: dict = None,
    ) -> str:
        """Free-form Q&A with Claude — optional context dict is included in the prompt."""
        prompt   = self._build_general_prompt(question, context_data)
        response = self._call_claude(prompt)
        self._save_to_history(
            mode="general", ticker="",
            user_message=question, ai_response=response,
            metadata={}
        )
        return response

    def reset_conversation(self):
        """Clear session context — starts a fresh conversation."""
        self.conversation  = []
        self.session_id    = datetime.now(
            pytz.timezone("US/Eastern")
        ).strftime("%Y%m%d_%H%M%S")
        logger.info("AI Advisor conversation reset")

    def get_conversation(self) -> list:
        """Return current session conversation history."""
        return self.conversation.copy()

    # ─────────────────────────────────────────
    # HISTORY
    # ─────────────────────────────────────────

    def get_history(self, limit: int = 50) -> list:
        """Load saved conversation history from disk."""
        if not os.path.exists(self._history_path):
            return []
        try:
            with open(self._history_path, "r") as f:
                all_convos = json.load(f)
            return all_convos[-limit:]
        except Exception:
            return []

    def _save_to_history(
        self,
        mode:         str,
        ticker:       str,
        user_message: str,
        ai_response:  str,
        metadata:     dict,
    ):
        """Append a conversation turn to the history log."""
        entry = {
            "session_id":   self.session_id,
            "timestamp":    datetime.now(
                pytz.timezone("US/Eastern")
            ).strftime("%Y-%m-%d %I:%M %p EST"),
            "mode":         mode,
            "ticker":       ticker,
            "user_message": user_message[:500],   # Truncate prompt for storage
            "ai_response":  ai_response,
            "metadata":     metadata,
        }

        history = self.get_history(limit=1000)
        history.append(entry)
        history = history[-500:]  # Keep last 500 entries

        try:
            with open(self._history_path, "w") as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save AI conversation: {e}")

    # ─────────────────────────────────────────
    # CLAUDE API CALL WITH SESSION CONTEXT
    # ─────────────────────────────────────────

    def _call_claude(self, user_message: str) -> str:
        """
        Call Claude API maintaining conversation context.
        Each call appends to self.conversation so Claude
        remembers earlier messages in the session.
        """
        if not self.api_key:
            return (
                "⚠️ AI Advisor is not configured.\n\n"
                "Add ANTHROPIC_API_KEY to your .env file.\n"
                "Get your key at: https://console.anthropic.com"
            )

        # Add user message to conversation history
        self.conversation.append({
            "role":    "user",
            "content": user_message,
        })

        # Keep conversation to last 10 turns to manage token usage
        messages_to_send = self.conversation[-10:]

        headers = {
            "Content-Type":      "application/json",
            "x-api-key":         self.api_key,
            "anthropic-version": "2023-06-01",
        }

        payload = {
            "model":      CLAUDE_MODEL,
            "max_tokens": 1000,
            "system":     SYSTEM_PROMPT,
            "messages":   messages_to_send,
        }

        try:
            response = requests.post(
                CLAUDE_API_URL,
                headers=headers,
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()

            text = "".join(
                block.get("text", "")
                for block in data.get("content", [])
                if block.get("type") == "text"
            )

            # Add assistant response to conversation history
            self.conversation.append({
                "role":    "assistant",
                "content": text,
            })

            logger.info(f"AI Advisor response: {len(text)} chars | "
                       f"Session turns: {len(self.conversation) // 2}")
            return text

        except requests.exceptions.Timeout:
            # Remove the user message we added since call failed
            self.conversation.pop()
            return "⚠️ Request timed out. Please try again."

        except requests.exceptions.HTTPError as e:
            self.conversation.pop()
            status = e.response.status_code if e.response else "unknown"
            if status == 401:
                return "⚠️ Invalid API key. Check ANTHROPIC_API_KEY in .env"
            elif status == 429:
                return "⚠️ Rate limit reached. Please wait a moment and try again."
            else:
                logger.error(f"AI Advisor HTTP error: {e}")
                return f"⚠️ API error ({status}). Please try again."

        except Exception as e:
            self.conversation.pop()
            logger.error(f"AI Advisor error: {e}")
            return f"⚠️ Error: {e}"

    # ─────────────────────────────────────────
    # PROMPT BUILDERS
    # ─────────────────────────────────────────

    def _build_pre_trade_prompt(
        self, ticker, score_result, ma_result, donchian_result,
        volume_result, cvd_result, rsi_result,
        options_context, trade_history, user_question,
    ) -> str:
        score     = score_result.get("final_score", 0)
        direction = score_result.get("direction", "neutral")
        tier      = score_result.get("tier", "none")
        layers    = score_result.get("layer_scores", {})

        indicator_lines = []
        if ma_result:
            indicator_lines.append(
                f"Moving Averages: trend={ma_result.get('trend_direction')} | "
                f"MA20={ma_result.get('ma20')} MA50={ma_result.get('ma50')} "
                f"MA200={ma_result.get('ma200')} | "
                f"Stack bullish={ma_result.get('stack_bullish')} | "
                f"HH/HL={ma_result.get('higher_highs_lows')} | "
                f"Score: {ma_result.get('score')}/35"
            )
        if donchian_result:
            indicator_lines.append(
                f"Donchian: breakout_up={donchian_result.get('breakout_up')} | "
                f"breakout_down={donchian_result.get('breakout_down')} | "
                f"upper={donchian_result.get('upper_band')} "
                f"lower={donchian_result.get('lower_band')} | "
                f"Score: {donchian_result.get('score')}/15"
            )
        if volume_result:
            indicator_lines.append(
                f"Volume: RVOL={volume_result.get('rvol')}x | "
                f"spike={volume_result.get('volume_spike')} | "
                f"direction={volume_result.get('volume_direction')} | "
                f"Score: {volume_result.get('score')}/12"
            )
        if cvd_result:
            indicator_lines.append(
                f"CVD: slope={cvd_result.get('cvd_slope')} | "
                f"signal={cvd_result.get('cvd_signal')} | "
                f"Score: {cvd_result.get('score')}/12"
            )
        if rsi_result:
            indicator_lines.append(
                f"RSI: value={rsi_result.get('rsi_current')} | "
                f"trend={rsi_result.get('rsi_trend')} | "
                f"bull_div={rsi_result.get('bullish_divergence')} | "
                f"bear_div={rsi_result.get('bearish_divergence')} | "
                f"Score: {rsi_result.get('score')}/12"
            )

        options_lines = ""
        if options_context and options_context.get("tradeable"):
            options_lines = f"""
OPTIONS CONTEXT:
  Strategy:   {options_context.get('strategy')}
  IV Rank:    {options_context.get('iv_rank')} ({options_context.get('iv_assessment')})
  DTE:        {options_context.get('recommended_dte')} days
  Max Loss:   {options_context.get('max_loss')}
  Max Profit: {options_context.get('max_profit')}
  Legs:       {json.dumps(options_context.get('legs', []))}"""

        history_lines = ""
        if trade_history:
            recent = trade_history[-5:]
            history_lines = "\nPAST TRADES ON THIS TICKER:\n"
            for t in recent:
                history_lines += (
                    f"  {t.get('entry_date','')} | {t.get('strategy','stock')} | "
                    f"{t.get('direction')} | entry=${t.get('entry_price')} | "
                    f"outcome={t.get('outcome')} | P&L={t.get('pnl_pct')}%\n"
                )

        question_block = f"\nSPECIFIC QUESTION: {user_question}" if user_question else ""

        return f"""PRE-TRADE ANALYSIS REQUEST — {ticker}

SIGNAL DATA:
  Confidence Score: {score}/100
  Direction:        {direction.upper()}
  Alert Tier:       {tier.replace('_',' ').title()}
  Trend Layer:      {layers.get('trend',{}).get('score',0)}/35
  Setup Layer:      {layers.get('setup',{}).get('score',0)}/35
  Volume Layer:     {layers.get('volume',{}).get('score',0)}/30
  Confluence:       {score_result.get('confluence_applied', False)}

INDICATOR BREAKDOWN:
{chr(10).join(indicator_lines)}
{options_lines}
{history_lines}
{question_block}

Provide pre-trade analysis covering:
1. What the signal is telling us (2-3 sentences)
2. Strongest points of this setup
3. Risks or weaknesses to watch
4. One specific thing to monitor after entry
5. If options shown — comment on strategy fit"""

    def _build_post_trade_prompt(
        self, trade, lesson, patterns, user_question
    ) -> str:
        trade_lines = f"""
POST-TRADE REVIEW REQUEST — {trade.get('ticker')}

TRADE SUMMARY:
  Ticker:     {trade.get('ticker')}
  Strategy:   {trade.get('strategy','stock')}
  Direction:  {trade.get('direction')}
  Entry:      ${trade.get('entry_price')} × {trade.get('size')}
  Exit:       ${trade.get('exit_price')}
  P&L:        ${trade.get('pnl_dollars')} ({trade.get('pnl_pct')}%)
  Outcome:    {trade.get('outcome','').upper()}
  Alert Score at entry: {trade.get('alert_score','N/A')}/100"""

        lesson_lines = ""
        if lesson:
            lesson_lines = f"""
TRADER DEBRIEF:
  Followed system:    {lesson.get('followed_system')}
  Entry quality 1-5:  {lesson.get('entry_quality')}
  Exit quality 1-5:   {lesson.get('exit_quality')}
  Execution 1-5:      {lesson.get('execution_score')}
  Emotion during:     {lesson.get('emotion_during')}
  What went right:    {lesson.get('what_went_right')}
  What went wrong:    {lesson.get('what_went_wrong')}
  Do differently:     {lesson.get('would_do_differently')}
  Lesson summary:     {lesson.get('lesson_summary')}
  Flags:              {', '.join(lesson.get('flags',[]))}"""

        pattern_lines = ""
        if patterns and patterns.get("total_lessons", 0) >= 3:
            pattern_lines = f"""
TRADING PATTERNS ({patterns.get('total_lessons')} lessons):
  System win rate:    {patterns.get('followed_win_rate')}%
  Override win rate:  {patterns.get('override_win_rate')}%
  Top loss emotion:   {list(patterns.get('loss_emotions',{}).keys())[:1]}
  Avg execution:      {patterns.get('avg_execution_score')}/5
  Top flags:          {list(patterns.get('top_flags',{}).keys())[:3]}"""

        question_block = f"\nSPECIFIC QUESTION: {user_question}" if user_question else ""

        return f"""{trade_lines}
{lesson_lines}
{pattern_lines}
{question_block}

Post-trade review covering:
1. Did the trade play out as the signal suggested?
2. What the trader did well (be specific)
3. One concrete thing to improve next time
4. If patterns shown — does this fit or break the pattern?
5. One sentence takeaway"""

    def _build_general_prompt(self, question: str, context_data: dict) -> str:
        context_block = ""
        if context_data:
            context_block = f"\nCONTEXT:\n{json.dumps(context_data, indent=2)}\n"
        return (
            f"You are acting as a trading coach for an active options trader "
            f"focused on SPY and individual equities using technical analysis.\n"
            f"Give direct, practical answers grounded in real market mechanics.\n"
            f"{context_block}"
            f"\nQUESTION: {question}"
        )
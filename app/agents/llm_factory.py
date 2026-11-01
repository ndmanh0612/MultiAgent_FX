"""LLM factory integrating TradingAgents Google Gemini client with offline mock fallback."""

from __future__ import annotations

import logging
import os
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from tradingagents.agents.schemas import (
    PortfolioDecision,
    PortfolioRating,
    ResearchPlan,
    TraderAction,
    TraderProposal,
)
from tradingagents.llm_clients.google_client import GoogleClient

logger = logging.getLogger(__name__)


class MockForexLLM:
    """Deterministic mock LLM for fast, offline, and cost-free test execution."""

    def __init__(self, default_action: str = "Buy"):
        self.default_action = default_action

    def invoke(self, input_val: Any, config: Any = None, **kwargs) -> AIMessage:
        prompt_text = str(input_val)

        if "Technical Analyst" in prompt_text:
            content = (
                "**Technical Analysis Report**\n\n"
                "- Trend: EMA20 is above EMA50, signaling an upward bias.\n"
                "- Momentum: RSI14 is at 58.5, indicating moderate bullish momentum without being overbought.\n"
                "- Volatility: ATR14 reflects normal volatility with clear expansion potential.\n"
                "- Levels: Immediate support at recent swing low; resistance near the recent high.\n\n"
                "**Bias**: BULLISH"
            )
        elif "Macroeconomist" in prompt_text:
            content = (
                "**Macroeconomic & Policy Report**\n\n"
                "- Policy Stance: Base currency central bank maintains a steady policy rate; quote currency central bank leans dovish.\n"
                "- Rate Differential: Yield differential mildly favors the base currency.\n"
                "- Risk Sentiment: Neutral to mild risk-on environment.\n\n"
                "**Bias**: BULLISH"
            )
        elif "Bull Researcher" in prompt_text:
            content = (
                "The bullish case is supported by EMA alignment and stable macro tailwinds. "
                "The recent consolidation offers an attractive continuation entry."
            )
        elif "Bear Researcher" in prompt_text:
            content = (
                "The bearish counter-argument highlights resistance near the session high and "
                "the risk of mean reversion if momentum fades."
            )
        elif "Risk Analyst" in prompt_text:
            content = (
                "Risk evaluation suggests adequate risk-reward potential, though stop loss must be placed "
                "sufficiently below the recent consolidation to withstand normal market noise."
            )
        else:
            content = f"Analysis completed with action recommendation: {self.default_action}."

        return AIMessage(content=content)

    def with_structured_output(self, schema: type[Any], **kwargs):
        """Mock implementation of LangChain with_structured_output."""
        action = self.default_action

        def _structured_response(prompt: Any) -> Any:
            if schema == ResearchPlan:
                rating = PortfolioRating.BUY if action == "Buy" else (PortfolioRating.SELL if action == "Sell" else PortfolioRating.HOLD)
                return ResearchPlan(
                    recommendation=rating,
                    rationale=f"Mock synthesis: evidence supports {action} with technical and macro convergence.",
                    strategic_actions=f"Initiate tactical {action} position adhering to ATR-based stops.",
                )
            elif schema == TraderProposal:
                act = TraderAction.BUY if action == "Buy" else (TraderAction.SELL if action == "Sell" else TraderAction.HOLD)
                return TraderProposal(
                    action=act,
                    reasoning=f"Tactical {action} setup supported by EMA trend and healthy RSI momentum.",
                    entry_price=1.0850,
                    stop_loss=1.0810 if action == "Buy" else 1.0890,
                    position_sizing="1% risk of account equity",
                )
            elif schema == PortfolioDecision:
                rating = PortfolioRating.BUY if action == "Buy" else (PortfolioRating.SELL if action == "Sell" else PortfolioRating.HOLD)
                return PortfolioDecision(
                    rating=rating,
                    executive_summary=f"Approved {action} order for execution. Favorable risk-reward profile.",
                    investment_thesis="Clear technical alignment with macro rate support justifies the trade.",
                    price_target=1.0920 if action == "Buy" else 1.0780,
                    time_horizon="H1-H4",
                )
            else:
                return schema()

        return RunnableLambda(_structured_response)


def get_gemini_api_key() -> str:
    """Retrieve Gemini API key from environment."""
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""


def create_forex_llm(
    tier: str = "quick",
    mock_mode: bool = False,
    api_key: str | None = None,
    mock_action: str = "Buy",
) -> Any:
    """Create a configured LLM client using TradingAgents GoogleClient or MockLLM.

    Args:
        tier: "quick" (for analysts, debaters, trader) or "deep" (for managers)
        mock_mode: Force offline mock LLM
        api_key: Optional explicit API key
        mock_action: Action generated by mock ("Buy", "Sell", "Hold")
    """
    key = api_key or get_gemini_api_key()

    if mock_mode or not key:
        logger.info("Initializing MockForexLLM for %s tier (mock_mode=%s, key_present=%s)", tier, mock_mode, bool(key))
        return MockForexLLM(default_action=mock_action)

    model_name = os.getenv(
        "FOREX_QUICK_LLM" if tier == "quick" else "FOREX_DEEP_LLM",
        "gemini-3.8-flash",
    )
    thinking_level = os.getenv("FOREX_THINKING_LEVEL", "low")

    logger.info("Initializing GoogleClient for %s tier using model %s", tier, model_name)
    client = GoogleClient(
        model=model_name,
        api_key=key,
        thinking_level=thinking_level,
        temperature=0.2,
        max_output_tokens=2048,
    )
    return client.get_llm()

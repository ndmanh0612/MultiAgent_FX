"""Unit tests for TradingAgents multi-agent integration and Gemini orchestration (Phase 3)."""

from datetime import UTC, datetime
from unittest import mock

import pytest

from app.agents.forex_graph import ForexTradingAgentsGraph
from app.agents.llm_factory import MockForexLLM
from app.agents.prompts import (
    build_forex_instrument_context,
    build_technical_analyst_prompt,
)
from app.domain.models import ForexPair, MarketSnapshot, TechnicalIndicators
from tradingagents.agents.schemas import (
    PortfolioDecision,
    PortfolioRating,
    ResearchPlan,
    TraderAction,
    TraderProposal,
)


@pytest.fixture
def sample_snapshot() -> MarketSnapshot:
    """Provide a verified mock MarketSnapshot for EUR_USD."""
    return MarketSnapshot(
        symbol=ForexPair.EUR_USD,
        timeframe="H1",
        candle_timestamp=datetime(2026, 11, 2, 10, 0, tzinfo=UTC),
        open=1.08500,
        high=1.08800,
        low=1.08400,
        close=1.08720,
        volume=2500,
        indicators=TechnicalIndicators(
            ema20=1.08450,
            ema50=1.08200,
            rsi14=62.4,
            atr14=0.00350,
        ),
        bid=1.08712,
        ask=1.08728,
        spread_pips=1.6,
        is_stale=False,
    )


# ---------------------------------------------------------------------------
# LLM Factory & Prompts Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_mock_llm_structured_output():
    mock_llm = MockForexLLM(default_action="Buy")

    # ResearchPlan
    rm_bound = mock_llm.with_structured_output(ResearchPlan)
    plan: ResearchPlan = rm_bound.invoke("test prompt")
    assert isinstance(plan, ResearchPlan)
    assert plan.recommendation == PortfolioRating.BUY

    # TraderProposal
    trader_bound = mock_llm.with_structured_output(TraderProposal)
    proposal: TraderProposal = trader_bound.invoke("test prompt")
    assert isinstance(proposal, TraderProposal)
    assert proposal.action == TraderAction.BUY
    assert proposal.entry_price == 1.0850

    # PortfolioDecision
    pm_bound = mock_llm.with_structured_output(PortfolioDecision)
    decision: PortfolioDecision = pm_bound.invoke("test prompt")
    assert isinstance(decision, PortfolioDecision)
    assert decision.rating == PortfolioRating.BUY


@pytest.mark.unit
def test_prompt_grounding_integrity(sample_snapshot: MarketSnapshot):
    context = build_forex_instrument_context(sample_snapshot)

    assert "EUR_USD" in context
    assert "1.08720" in context
    assert "62.4" in context  # RSI
    assert "1.08450" in context  # EMA20
    assert "1.08200" in context  # EMA50
    assert "1.6 pips" in context  # Spread
    assert "STRICT INTEGRITY RULE" in context

    tech_prompt = build_technical_analyst_prompt(sample_snapshot)
    assert "Senior Quantitative Technical Analyst" in tech_prompt
    assert "EUR_USD" in tech_prompt


# ---------------------------------------------------------------------------
# Multi-Agent LangGraph End-to-End Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_forex_graph_end_to_end_buy(sample_snapshot: MarketSnapshot):
    graph = ForexTradingAgentsGraph(mock_mode=True, mock_action="Buy")
    result = graph.analyze(sample_snapshot)

    assert result["symbol"] == "EUR_USD"
    assert result["action"] == "BUY"
    assert result["final_rating"] == "Buy"

    # Verify each agent turn produced content
    assert "market_report" in result and len(result["market_report"]) > 0
    assert "news_report" in result and len(result["news_report"]) > 0
    assert "investment_plan" in result and len(result["investment_plan"]) > 0
    assert "trader_investment_plan" in result and len(result["trader_investment_plan"]) > 0
    assert "final_trade_decision" in result and len(result["final_trade_decision"]) > 0

    # Verify debate occurred
    debate = result.get("investment_debate_state", {})
    assert debate.get("count", 0) >= 2  # At least 1 round (Bull + Bear)
    assert "Bull Researcher" in debate.get("history", "")
    assert "Bear Researcher" in debate.get("history", "")


@pytest.mark.unit
def test_forex_graph_end_to_end_sell(sample_snapshot: MarketSnapshot):
    graph = ForexTradingAgentsGraph(mock_mode=True, mock_action="Sell")
    result = graph.analyze(sample_snapshot)

    assert result["symbol"] == "EUR_USD"
    assert result["action"] == "SELL"
    assert result["final_rating"] == "Sell"


@pytest.mark.unit
def test_forex_graph_end_to_end_hold(sample_snapshot: MarketSnapshot):
    graph = ForexTradingAgentsGraph(mock_mode=True, mock_action="Hold")
    result = graph.analyze(sample_snapshot)

    assert result["symbol"] == "EUR_USD"
    assert result["action"] == "NO_TRADE"
    assert result["final_rating"] == "Hold"


@pytest.mark.unit
def test_forex_graph_stale_data_failsafe(sample_snapshot: MarketSnapshot):
    stale_snapshot = sample_snapshot.model_copy(update={"is_stale": True, "stale_reason": "Weekend market closure"})
    graph = ForexTradingAgentsGraph(mock_mode=True)

    result = graph.analyze(stale_snapshot)

    assert result["action"] == "NO_TRADE"
    assert result["final_rating"] == "Hold"
    assert "Weekend market closure" in result["reason"]
    # LLM should not have run
    assert "(Skipped: Market closed / stale data)" in result["market_report"]


@pytest.mark.unit
def test_forex_graph_exception_failsafe(sample_snapshot: MarketSnapshot):
    graph = ForexTradingAgentsGraph(mock_mode=True)

    # Simulate an unexpected failure in LangGraph invoke
    with mock.patch.object(graph.graph, "invoke", side_effect=RuntimeError("Unexpected graph failure")):
        result = graph.analyze(sample_snapshot)
        assert result["action"] == "NO_TRADE"
        assert result["final_rating"] == "Hold"
        assert "Execution error in multi-agent pipeline" in result["reason"]

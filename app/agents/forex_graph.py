"""Forex Multi-Agent LangGraph workflow integrating TradingAgents reasoning and Gemini."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from app.agents.llm_factory import create_forex_llm
from app.agents.prompts import (
    build_bear_researcher_prompt,
    build_bull_researcher_prompt,
    build_forex_instrument_context,
    build_macro_analyst_prompt,
    build_portfolio_manager_prompt,
    build_research_manager_prompt,
    build_risk_analyst_prompt,
    build_technical_analyst_prompt,
    build_trader_prompt,
)
from app.domain.models import MarketSnapshot
from tradingagents.agents.schemas import (
    PortfolioDecision,
    PortfolioRating,
    ResearchPlan,
    TraderAction,
    TraderProposal,
    render_pm_decision,
    render_research_plan,
    render_trader_proposal,
)
from tradingagents.agents.state import AgentState, InvestDebateState, RiskDebateState
from tradingagents.graph.conditional_logic import ConditionalLogic

logger = logging.getLogger(__name__)


class ForexAgentState(AgentState):
    """Extended agent state with verified market snapshot."""

    snapshot: MarketSnapshot


class ForexTradingAgentsGraph:
    """Orchestrates the Forex multi-agent reasoning graph for H1 candles."""

    def __init__(
        self,
        quick_llm: Any = None,
        deep_llm: Any = None,
        max_debate_rounds: int = 1,
        max_risk_discuss_rounds: int = 1,
        mock_mode: bool = False,
        api_key: str | None = None,
        mock_action: str = "Buy",
    ):
        self.mock_mode = mock_mode
        self.mock_action = mock_action
        self.quick_llm = quick_llm or create_forex_llm("quick", mock_mode=mock_mode, api_key=api_key, mock_action=mock_action)
        self.deep_llm = deep_llm or create_forex_llm("deep", mock_mode=mock_mode, api_key=api_key, mock_action=mock_action)
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=max_debate_rounds,
            max_risk_discuss_rounds=max_risk_discuss_rounds,
        )
        self.workflow = self._build_graph()
        self.graph = self.workflow.compile()

    def _build_graph(self) -> StateGraph:
        workflow = StateGraph(ForexAgentState)

        # 1. Technical Analyst Node
        def technical_analyst_node(state: ForexAgentState) -> dict:
            snapshot: MarketSnapshot = state["snapshot"]
            prompt = build_technical_analyst_prompt(snapshot)
            response = self.quick_llm.invoke(prompt)
            report = response.content if isinstance(response, AIMessage) else str(response)
            return {"market_report": report}

        # 2. Macro Analyst Node
        def macro_analyst_node(state: ForexAgentState) -> dict:
            snapshot: MarketSnapshot = state["snapshot"]
            prompt = build_macro_analyst_prompt(snapshot)
            response = self.quick_llm.invoke(prompt)
            report = response.content if isinstance(response, AIMessage) else str(response)
            return {"news_report": report}

        # 3. Bull Researcher Node
        def bull_researcher_node(state: ForexAgentState) -> dict:
            snapshot: MarketSnapshot = state["snapshot"]
            invest_state = state.get("investment_debate_state", {})
            history = invest_state.get("history", "")
            opponent = invest_state.get("current_response", "(The Bear analyst has not spoken yet)")

            prompt = build_bull_researcher_prompt(
                snapshot=snapshot,
                technical_report=state.get("market_report", ""),
                macro_report=state.get("news_report", ""),
                debate_history=history,
                opponent_argument=opponent,
            )
            response = self.quick_llm.invoke(prompt)
            arg = f"Bull Researcher: {response.content if isinstance(response, AIMessage) else str(response)}"

            new_invest_state: InvestDebateState = {
                "history": f"{history}\n{arg}".strip(),
                "bull_history": f"{invest_state.get('bull_history', '')}\n{arg}".strip(),
                "bear_history": invest_state.get("bear_history", ""),
                "current_response": arg,
                "count": invest_state.get("count", 0) + 1,
            }
            return {"investment_debate_state": new_invest_state}

        # 4. Bear Researcher Node
        def bear_researcher_node(state: ForexAgentState) -> dict:
            snapshot: MarketSnapshot = state["snapshot"]
            invest_state = state.get("investment_debate_state", {})
            history = invest_state.get("history", "")
            opponent = invest_state.get("current_response", "(The Bull analyst has not spoken yet)")

            prompt = build_bear_researcher_prompt(
                snapshot=snapshot,
                technical_report=state.get("market_report", ""),
                macro_report=state.get("news_report", ""),
                debate_history=history,
                opponent_argument=opponent,
            )
            response = self.quick_llm.invoke(prompt)
            arg = f"Bear Researcher: {response.content if isinstance(response, AIMessage) else str(response)}"

            new_invest_state: InvestDebateState = {
                "history": f"{history}\n{arg}".strip(),
                "bull_history": invest_state.get("bull_history", ""),
                "bear_history": f"{invest_state.get('bear_history', '')}\n{arg}".strip(),
                "current_response": arg,
                "count": invest_state.get("count", 0) + 1,
            }
            return {"investment_debate_state": new_invest_state}

        # 5. Research Manager Node
        structured_rm_llm = self.deep_llm.with_structured_output(ResearchPlan)

        def research_manager_node(state: ForexAgentState) -> dict:
            snapshot: MarketSnapshot = state["snapshot"]
            history = state.get("investment_debate_state", {}).get("history", "")
            prompt = build_research_manager_prompt(snapshot, history)
            try:
                plan: ResearchPlan = structured_rm_llm.invoke(prompt)
                rendered = render_research_plan(plan)
            except Exception as exc:
                logger.warning("Structured ResearchPlan call failed: %s. Falling back to Hold.", exc)
                plan = ResearchPlan(
                    recommendation=PortfolioRating.HOLD,
                    rationale=f"Synthesis fallback due to parsing error ({exc}).",
                    strategic_actions="Maintain neutral stance, do not enter.",
                )
                rendered = render_research_plan(plan)
            return {"investment_plan": rendered}

        # 6. Trader Node
        structured_trader_llm = self.quick_llm.with_structured_output(TraderProposal)

        def trader_node(state: ForexAgentState) -> dict:
            snapshot: MarketSnapshot = state["snapshot"]
            plan_text = state.get("investment_plan", "")
            tech_report = state.get("market_report", "")
            prompt = build_trader_prompt(snapshot, plan_text, tech_report)
            try:
                proposal: TraderProposal = structured_trader_llm.invoke(prompt)
                rendered = render_trader_proposal(proposal)
            except Exception as exc:
                logger.warning("Structured TraderProposal call failed: %s. Defaulting to Hold.", exc)
                proposal = TraderProposal(
                    action=TraderAction.HOLD,
                    reasoning=f"Proposal fallback due to error: {exc}.",
                    entry_price=snapshot.close,
                    stop_loss=None,
                    position_sizing="None",
                )
                rendered = render_trader_proposal(proposal)
            return {"trader_investment_plan": rendered}

        # 7. Risk Analysts (Aggressive, Conservative, Neutral)
        def make_risk_node(analyst_type: str):
            def risk_node(state: ForexAgentState) -> dict:
                snapshot: MarketSnapshot = state["snapshot"]
                r_state = state.get("risk_debate_state", {})
                history = r_state.get("history", "")
                opponent = r_state.get("current_aggressive_response", "(Opening risk review)")

                prompt = build_risk_analyst_prompt(
                    analyst_type=analyst_type,
                    snapshot=snapshot,
                    trader_proposal_text=state.get("trader_investment_plan", ""),
                    risk_history=history,
                    opponent_argument=opponent,
                )
                response = self.quick_llm.invoke(prompt)
                arg = f"{analyst_type} Analyst: {response.content if isinstance(response, AIMessage) else str(response)}"

                new_r_state: RiskDebateState = {
                    "history": f"{history}\n{arg}".strip(),
                    "aggressive_history": r_state.get("aggressive_history", "") + (f"\n{arg}" if analyst_type == "Aggressive" else ""),
                    "conservative_history": r_state.get("conservative_history", "") + (f"\n{arg}" if analyst_type == "Conservative" else ""),
                    "neutral_history": r_state.get("neutral_history", "") + (f"\n{arg}" if analyst_type == "Neutral" else ""),
                    "latest_speaker": analyst_type,
                    "current_aggressive_response": arg if analyst_type == "Aggressive" else r_state.get("current_aggressive_response", ""),
                    "current_conservative_response": arg if analyst_type == "Conservative" else r_state.get("current_conservative_response", ""),
                    "current_neutral_response": arg if analyst_type == "Neutral" else r_state.get("current_neutral_response", ""),
                    "count": r_state.get("count", 0) + 1,
                }
                return {"risk_debate_state": new_r_state}

            return risk_node

        # 8. Portfolio Manager Node
        structured_pm_llm = self.deep_llm.with_structured_output(PortfolioDecision)

        def portfolio_manager_node(state: ForexAgentState) -> dict:
            snapshot: MarketSnapshot = state["snapshot"]
            r_history = state.get("risk_debate_state", {}).get("history", "")
            prompt = build_portfolio_manager_prompt(
                snapshot=snapshot,
                research_plan_text=state.get("investment_plan", ""),
                trader_proposal_text=state.get("trader_investment_plan", ""),
                risk_debate_history=r_history,
            )
            try:
                decision: PortfolioDecision = structured_pm_llm.invoke(prompt)
                rendered = render_pm_decision(decision)
                rating = decision.rating.value
            except Exception as exc:
                logger.warning("Structured PortfolioDecision call failed: %s. Defaulting to Hold.", exc)
                decision = PortfolioDecision(
                    rating=PortfolioRating.HOLD,
                    executive_summary="Hold recommendation: insufficient confidence or error during risk synthesis.",
                    investment_thesis=f"Fallback triggered: {exc}",
                )
                rendered = render_pm_decision(decision)
                rating = PortfolioRating.HOLD.value

            return {
                "final_trade_decision": rendered,
                "final_rating": rating,
            }

        # Add Nodes to LangGraph
        workflow.add_node("Technical Analyst", technical_analyst_node)
        workflow.add_node("Macro Analyst", macro_analyst_node)
        workflow.add_node("Bull Researcher", bull_researcher_node)
        workflow.add_node("Bear Researcher", bear_researcher_node)
        workflow.add_node("Research Manager", research_manager_node)
        workflow.add_node("Trader", trader_node)
        workflow.add_node("Aggressive Analyst", make_risk_node("Aggressive"))
        workflow.add_node("Conservative Analyst", make_risk_node("Conservative"))
        workflow.add_node("Neutral Analyst", make_risk_node("Neutral"))
        workflow.add_node("Portfolio Manager", portfolio_manager_node)

        # Edges
        # Analysts start in parallel
        workflow.add_edge(START, "Technical Analyst")
        workflow.add_edge(START, "Macro Analyst")
        # Fan-in to Bull Researcher
        workflow.add_edge(["Technical Analyst", "Macro Analyst"], "Bull Researcher")

        # Debate Loop
        debate_path_map = {
            "Bull Researcher": "Bull Researcher",
            "Bear Researcher": "Bear Researcher",
            "Research Manager": "Research Manager",
        }
        workflow.add_conditional_edges("Bull Researcher", self.conditional_logic.should_continue_debate, debate_path_map)
        workflow.add_conditional_edges("Bear Researcher", self.conditional_logic.should_continue_debate, debate_path_map)

        # Trader Transition
        workflow.add_edge("Research Manager", "Trader")
        workflow.add_edge("Trader", "Aggressive Analyst")

        # Risk Discussion Loop
        risk_path_map = {
            "Aggressive Analyst": "Aggressive Analyst",
            "Conservative Analyst": "Conservative Analyst",
            "Neutral Analyst": "Neutral Analyst",
            "Portfolio Manager": "Portfolio Manager",
        }
        workflow.add_conditional_edges("Aggressive Analyst", self.conditional_logic.should_continue_risk_analysis, risk_path_map)
        workflow.add_conditional_edges("Conservative Analyst", self.conditional_logic.should_continue_risk_analysis, risk_path_map)
        workflow.add_conditional_edges("Neutral Analyst", self.conditional_logic.should_continue_risk_analysis, risk_path_map)

        # Portfolio Manager Finishes
        workflow.add_edge("Portfolio Manager", END)

        return workflow

    def analyze(self, snapshot: MarketSnapshot) -> dict[str, Any]:
        """Execute the multi-agent graph on a verified H1 MarketSnapshot.

        Fails safely to NO_TRADE if market data is stale or market is closed.
        """
        pair_str = snapshot.symbol.value

        # Safety Check: Market Closed or Stale Data
        if snapshot.is_stale:
            logger.info("MarketSnapshot for %s is stale (%s); aborting analysis to NO_TRADE.", pair_str, snapshot.stale_reason)
            return {
                "symbol": pair_str,
                "action": "NO_TRADE",
                "final_rating": "Hold",
                "reason": f"Market data stale or market closed: {snapshot.stale_reason}",
                "final_trade_decision": f"HOLD - {snapshot.stale_reason}",
                "trader_investment_plan": "HOLD",
                "investment_plan": "HOLD",
                "market_report": "(Skipped: Market closed / stale data)",
                "news_report": "(Skipped: Market closed / stale data)",
                "snapshot": snapshot,
            }

        # Initialize LangGraph state
        initial_state: dict[str, Any] = {
            "snapshot": snapshot,
            "company_of_interest": pair_str,
            "asset_type": "forex",
            "instrument_context": build_forex_instrument_context(snapshot),
            "trade_date": snapshot.candle_timestamp.strftime("%Y-%m-%d"),
            "market_report": "",
            "news_report": "",
            "investment_debate_state": {
                "bull_history": "",
                "bear_history": "",
                "history": "",
                "current_response": "",
                "count": 0,
            },
            "investment_plan": "",
            "trader_investment_plan": "",
            "risk_debate_state": {
                "aggressive_history": "",
                "conservative_history": "",
                "neutral_history": "",
                "history": "",
                "latest_speaker": "",
                "current_aggressive_response": "",
                "current_conservative_response": "",
                "current_neutral_response": "",
                "count": 0,
            },
            "final_trade_decision": "",
            "final_rating": "",
            "past_context": "",
            "memory_note": "",
            "portfolio_context": "",
            "messages": [],
        }

        try:
            logger.info("Running Forex Multi-Agent analysis graph for %s at %s", pair_str, snapshot.candle_timestamp)
            final_state = self.graph.invoke(initial_state)

            # Map 5-tier rating to 3-tier action (BUY, SELL, NO_TRADE)
            rating = final_state.get("final_rating", "Hold")
            if rating in ("Buy", "Overweight"):
                action = "BUY"
            elif rating in ("Sell", "Underweight"):
                action = "SELL"
            else:
                action = "NO_TRADE"

            final_state["action"] = action
            final_state["symbol"] = pair_str
            final_state["snapshot"] = snapshot
            return final_state

        except Exception as exc:
            logger.error("Error executing Forex multi-agent graph: %s. Failing safely to NO_TRADE.", exc, exc_info=True)
            return {
                "symbol": pair_str,
                "action": "NO_TRADE",
                "final_rating": "Hold",
                "reason": f"Execution error in multi-agent pipeline: {exc}",
                "final_trade_decision": f"HOLD - Pipeline error: {exc}",
                "trader_investment_plan": "HOLD",
                "investment_plan": "HOLD",
                "market_report": "",
                "news_report": "",
                "snapshot": snapshot,
            }

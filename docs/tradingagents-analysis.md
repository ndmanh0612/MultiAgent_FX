# TradingAgents Architecture & Forex H1 Adaptation Analysis

## 1. Upstream Framework Version & Commit Pinning

- **Repository**: [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
- **Tested Version**: `0.7.1` (defined in `tradingagents/__init__.py`)
- **Pinned Commit Hash**: `c4544913a14ed3df5f1ed7b9e8483270ce20682a`
- **Runtime Environment**: Python 3.11+ (verified under Python 3.14 on Windows)

---

## 2. Upstream Project Structure

```text
TradingAgents/
├── cli/                        # Typer & questionary-based interactive terminal UI
├── tradingagents/              # Core multi-agent framework
│   ├── agents/
│   │   ├── analysts/           # Market, Fundamentals, News, Sentiment analysts
│   │   ├── managers/           # Research Manager & Portfolio Manager
│   │   ├── researchers/        # Bull Researcher & Bear Researcher
│   │   ├── risk_mgmt/          # Aggressive, Conservative, Neutral debaters
│   │   ├── trader/             # Trader agent proposing transaction execution
│   │   ├── context.py          # Instrument identity & context resolution
│   │   ├── schemas.py          # Pydantic models for structured outputs
│   │   ├── state.py            # TypedDict LangGraph states (AgentState, etc.)
│   │   ├── structured.py       # LLM provider-agnostic structured binding
│   │   └── tools.py            # Tools exposed to LangGraph analyst nodes
│   ├── dataflows/
│   │   ├── config.py           # Thread-local runtime configuration
│   │   ├── date_window.py      # Point-in-time constraints (as_of, in_window)
│   │   ├── router.py           # Tool-to-vendor dispatching
│   │   └── vendors/            # Alpha Vantage, Yahoo Finance, FRED, SEC EDGAR, Polymarket
│   ├── graph/
│   │   ├── trading_graph.py    # Main TradingAgentsGraph orchestration engine
│   │   ├── setup.py            # LangGraph StateGraph assembly and workflow compiling
│   │   ├── conditional_logic.py# Dynamic routing for debate & risk discussion rounds
│   │   └── checkpointer.py     # SQLite checkpointing for resumable graphs
│   ├── llm_clients/
│   │   ├── factory.py          # Unified client factory with tier routing
│   │   ├── google_client.py    # Google Gemini client (via langchain-google-genai)
│   │   └── model_catalog.py    # Supported models & capability metadata
│   ├── memory/
│   │   ├── log.py              # Append-only Markdown decision & outcome log
│   │   ├── reflection.py       # LLM-based reflection over settled decisions
│   │   └── settlement.py       # Automatic settlement of pending decisions
│   ├── default_config.py       # Configuration defaults & TRADINGAGENTS_* overrides
│   ├── reporting.py            # Markdown report generation
│   └── report_html.py          # Single-page HTML reporting
└── tests/                      # Pytest unit & integration test suite
```

---

## 3. TradingAgentsGraph Implementation & Orchestration

The graph in `tradingagents/graph/trading_graph.py` and `setup.py` implements an asynchronous, fan-out/fan-in multi-agent pipeline using **LangGraph**:

1. **Analyst Subgraphs**: 
   - Analysts (`market`, `news`, `fundamentals`, `social`) run concurrently from `START`.
   - Each analyst operates in an isolated subgraph (`_analyst_graph`) with private tool messages and bounded execution (`max_tool_rounds`).
   - An optional `Memory Log` step runs in parallel to retrieve past lessons and settle prior decisions.
2. **Research Debate**:
   - Analysts converge into `Bull Researcher` via a fan-in barrier.
   - A debate loop ping-pongs between `Bull Researcher` and `Bear Researcher` controlled by `ConditionalLogic.should_continue_debate` until `max_debate_rounds` is reached.
3. **Research Synthesis**:
   - `Research Manager` synthesizes the bull/bear debate history into a typed `ResearchPlan` (recommendation, rationale, strategic actions).
4. **Trader Proposal**:
   - `Trader` takes the `ResearchPlan` and technical market report to produce a concrete `TraderProposal` (action: Buy/Hold/Sell, entry price, stop loss, sizing).
5. **Risk Management Debate**:
   - `TraderProposal` is submitted to the risk debate team: `Aggressive Analyst`, `Conservative Analyst`, and `Neutral Analyst`.
   - The debate continues in sequence until `ConditionalLogic.should_continue_risk_analysis` routes to `Portfolio Manager`.
6. **Portfolio Decision**:
   - `Portfolio Manager` evaluates risk debate arguments, past reflection lessons, and outputs a typed `PortfolioDecision` (rating: Buy, Overweight, Hold, Underweight, Sell; executive summary; investment thesis).

---

## 4. Market Data Interfaces & Tool-Calling

- Tools are registered as LangChain `@tool` objects and injected into analyst subgraphs using LangGraph `ToolNode`.
- Tool calls pass through `tradingagents/dataflows/router.py`, which maps tool names (`get_stock_data`, `get_indicators`, `get_verified_market_snapshot`, `get_macro_indicators`, etc.) to specific vendor implementations.
- Point-in-time constraints are enforced by `date_window.py`: `trade_date` is injected into tool arguments via `langgraph.prebuilt.InjectedState`, preventing agents from looking past the analysis date.

---

## 5. LLM Configuration & Gemini Support

- The framework features native Google Gemini support via `tradingagents/llm_clients/google_client.py` using `langchain_google_genai.ChatGoogleGenerativeAI` with output normalization (`NormalizedChatGoogleGenerativeAI`).
- Models configured:
  - **Quick-think tier** (analysts, researchers, debaters, trader): e.g. `gemini-3.8-flash`, `gemini-3.5-flash-lite`, `gemini-2.5-flash`.
  - **Deep-think tier** (research manager, portfolio manager): e.g. `gemini-3.8-flash`, `gemini-3.1-pro-preview`.
- Parameter knobs supported: `google_thinking_level` (`"high"`, `"low"`), `max_tokens`, `temperature`, `llm_max_retries`, and `timeout` (default 600s).

---

## 6. Structured Decision Outputs

Structured outputs are powered by Pydantic schemas in `tradingagents/agents/schemas.py`:
- `ResearchPlan`: `recommendation: PortfolioRating`, `rationale: str`, `strategic_actions: str`.
- `TraderProposal`: `action: TraderAction` (Buy/Hold/Sell), `reasoning: str`, `entry_price: float | None`, `stop_loss: float | None`, `position_sizing: str | None`.
- `PortfolioDecision`: `rating: PortfolioRating` (Buy/Overweight/Hold/Underweight/Sell), `executive_summary: str`, `investment_thesis: str`, `price_target: float | None`, `time_horizon: str | None`.

Binding helper `bind_structured` uses LangChain `with_structured_output` (utilizing Gemini's native `response_schema`), with graceful fallback to free-text generation and regex parsing.

---

## 7. Memory & Checkpoint Handling

- **Memory Log** (`TradingMemoryLog` in `tradingagents/memory/log.py`):
  - Append-only markdown log tagged with `[YYYY-MM-DD | TICKER | RATING | pending/settled]`.
  - Upon completion of holding periods (e.g. 5 days), `settle_pending` evaluates actual price return vs benchmark and requests LLM reflection.
- **Checkpoints** (`tradingagents/graph/checkpointer.py`):
  - Uses `langgraph-checkpoint-sqlite` (`SqliteSaver`) storing graph states per ticker.
  - Generates a deterministic signature based on analyst selection, debate rounds, and settings.

---

## 8. Gap Analysis & Required Modifications for Forex H1

While TradingAgents provides an exceptional multi-agent debate and reasoning foundation, it was originally built for daily equities. The following key gaps must be addressed for Forex H1 signal generation:

| Upstream Component | Stock / Daily Behavior | Forex H1 Requirement | Adapter / Extension Strategy |
| :--- | :--- | :--- | :--- |
| **Timeframe & Clocks** | Daily `YYYY-MM-DD` strings (`_validate_trade_date`) | Hourly H1 UTC timestamps (`ISO-8601`) | Implement `ForexMarketData` adapter with strict UTC H1 candle validation; pass sanitized date/timestamp contexts to agents. |
| **Market Data Vendor** | Yahoo Finance / Alpha Vantage daily stock bars | OANDA Practice REST API for H1 candles with Bid/Ask spread | Implement `OandaClient` adapter fetching completed H1 bars, validating continuity and integrity without look-ahead bias. |
| **Technical Indicators** | StockStats over daily equity prices | EMA20, EMA50, RSI14, ATR14 on completed H1 candles | Compute indicators deterministically in `ForexDataService` using pandas/numpy; ground all agent reasoning in verified indicator tables. |
| **Instrument Identity** | Yahoo Finance `get_company_profile` (Equity sector, industry) | Currency pair characteristics (base/quote, pips, central banks) | Build `ForexInstrumentContext` injecting currency pairs (EUR/USD, GBP/USD, USD/JPY), pip sizes (0.0001 vs 0.01), session timings, and central bank pairings (ECB/Fed, BoE/Fed, BoJ/Fed). |
| **Macro / Fundamentals** | SEC EDGAR 10-K/10-Q, balance sheets, insider trading | Central bank interest rate differentials, economic calendar, CPI/GDP from FRED | Specialize analysts: Technical Analyst (H1 structure/indicators) and Macro Analyst (FRED macro data & central bank rates); exclude irrelevant SEC EDGAR equity tools. |
| **Signal Engine & Levels** | Optional free-form price targets; 5-tier ratings (Overweight, etc.) | Deterministic BUY / SELL / NO_TRADE with exact Entry, SL, TP, Risk/Reward >= 1.5 | Implement deterministic `SignalEngine`: validate LLM proposal against verified market data, enforce SL/TP distances, check spread, adjust for JPY pips, fallback safely to `NO_TRADE`. |
| **Persistence** | Markdown logs & per-ticker SqliteSaver checkpoints | Structured SQLite relational schema for production audit & analytics | Create SQLite schema: `market_snapshots`, `analysis_runs`, `agent_decisions`, `signals`, `telegram_deliveries`, `evaluation_results`. |
| **Delivery & Automation** | Interactive CLI | Resilient Telegram Bot notifications + APScheduler + GitHub Actions | Implement `TelegramService` with dry-run support, idempotency tracking, and create `.github/workflows/forex-signals.yml`. |

---

## 9. Integration Architecture & Strategy

To adhere to the principle of **"Prefer composition and adapters over modifying upstream source code"**:

1. **No Breaking Modifications to Upstream**: The original `tradingagents/` source remains clean and reusable as a submodule/library.
2. **Custom Modular Application Package (`app/`)**:
   - `app/domain/`: Pydantic domain models (`Candle`, `MarketSnapshot`, `Signal`, `TradeAction`, `ForexPair`).
   - `app/data/`: `OandaClient` (API adapter with offline mock support), `IndicatorService` (EMA, RSI, ATR).
   - `app/agents/`: Forex-specialized agent integration wiring TradingAgents LangGraph nodes, prompts, and Gemini client.
   - `app/engine/`: Deterministic `SignalEngine` for Entry/SL/TP calculation, validation, pip precision, and risk filtering.
   - `app/storage/`: SQLite database manager and repositories with automatic migrations.
   - `app/notifications/`: `TelegramService` with formatting, retry logic, and delivery tracking.
   - `app/cli.py`: Multi-command Typer CLI (`analyze`, `run-once`, `scheduler`, `test-telegram`).
   - `app/evaluation/`: Backtesting and evaluation module with transaction costs and EMA baseline comparison.

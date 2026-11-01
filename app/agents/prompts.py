"""Forex-specialized prompts grounded in verified H1 market data and central bank dynamics."""

from __future__ import annotations

from app.domain.models import MarketSnapshot


def format_recent_candles_table(snapshot: MarketSnapshot, max_rows: int = 10) -> str:
    """Format recent completed candles into a readable markdown table."""
    if not snapshot.recent_candles:
        return "(No recent candle history available)"

    rows = ["| Timestamp (UTC) | Open | High | Low | Close | Volume |", "| :--- | :--- | :--- | :--- | :--- | :--- |"]
    for c in snapshot.recent_candles[-max_rows:]:
        t_str = c.timestamp.strftime("%Y-%m-%d %H:%M")
        precision = snapshot.symbol.price_precision
        rows.append(
            f"| {t_str} | {c.open:.{precision}f} | {c.high:.{precision}f} | "
            f"{c.low:.{precision}f} | {c.close:.{precision}f} | {c.volume} |"
        )
    return "\n".join(rows)


def build_forex_instrument_context(snapshot: MarketSnapshot) -> str:
    """Build deterministic, verified instrument and market snapshot context for agents."""
    pair = snapshot.symbol
    cb_base, cb_quote = pair.central_banks
    precision = pair.price_precision

    ema20_str = f"{snapshot.indicators.ema20:.{precision}f}" if snapshot.indicators.ema20 is not None else "N/A"
    ema50_str = f"{snapshot.indicators.ema50:.{precision}f}" if snapshot.indicators.ema50 is not None else "N/A"
    rsi_str = f"{snapshot.indicators.rsi14:.1f}" if snapshot.indicators.rsi14 is not None else "N/A"

    if snapshot.indicators.atr14 is not None:
        atr_pips = pair.round_pips(pair.price_to_pips(snapshot.indicators.atr14))
        atr_str = f"{snapshot.indicators.atr14:.{precision}f} ({atr_pips} pips)"
    else:
        atr_str = "N/A"

    spread_str = f"{snapshot.spread_pips:.1f} pips" if snapshot.spread_pips is not None else "N/A"

    return f"""## Verified Market Context (Source of Truth)
- **Currency Pair**: `{pair.value}` (Base: {pair.base_currency}, Quote: {pair.quote_currency})
- **Timeframe**: `{snapshot.timeframe}` (1-Hour Bars, strictly completed candles)
- **Latest Closed Candle Time**: `{snapshot.candle_timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")}`
- **Latest OHLC**: Open={snapshot.open:.{precision}f}, High={snapshot.high:.{precision}f}, Low={snapshot.low:.{precision}f}, Close={snapshot.close:.{precision}f}
- **Volume / Ticks**: {snapshot.volume}
- **Bid / Ask**: {f"{snapshot.bid:.{precision}f}" if snapshot.bid else "N/A"} / {f"{snapshot.ask:.{precision}f}" if snapshot.ask else "N/A"} (Spread: {spread_str})
- **Verified Indicators**:
  - EMA(20): `{ema20_str}`
  - EMA(50): `{ema50_str}`
  - RSI(14): `{rsi_str}`
  - ATR(14): `{atr_str}`
- **Central Banks**: Base: {cb_base} | Quote: {cb_quote}
- **Pip Size**: `{pair.pip_size}` ({pair.pip_decimal_places} decimal places)

### Recent Completed H1 Candles:
{format_recent_candles_table(snapshot)}

**STRICT INTEGRITY RULE**: All claims regarding current price, indicators, trend, or support/resistance MUST be grounded in the verified data above. Do NOT invent prices or cite future candles.
"""


def build_technical_analyst_prompt(snapshot: MarketSnapshot) -> str:
    context = build_forex_instrument_context(snapshot)
    return f"""You are a Senior Quantitative Technical Analyst specializing in Forex H1 trading.

{context}

Analyze the H1 technical structure for `{snapshot.symbol.value}`:
1. **Trend & Moving Averages**: Evaluate the relationship between EMA20 and EMA50 and the current close price. Is the market in an uptrend, downtrend, or consolidation?
2. **Momentum (RSI14)**: Evaluate RSI level (Overbought > 70, Oversold < 30, Neutral 40-60). Are there signs of momentum exhaustion or continuation?
3. **Volatility & Range (ATR14)**: Evaluate market volatility and the expected hourly range in pips.
4. **Key Price Structure**: Identify dynamic support/resistance levels derived from recent swing highs and lows in the candle table.

Provide a concise, objective technical report. Conclude with a clear bias: BULLISH, BEARISH, or NEUTRAL.
"""


def build_macro_analyst_prompt(snapshot: MarketSnapshot) -> str:
    context = build_forex_instrument_context(snapshot)
    cb_base, cb_quote = snapshot.symbol.central_banks
    return f"""You are a Chief Forex Macroeconomist specializing in G10 currency policy.

{context}

Analyze the macroeconomic backdrop and central bank policy for `{snapshot.symbol.value}`:
1. **Central Bank Stance**: Compare {cb_base} vs {cb_quote}. Which central bank has a relatively more restrictive (hawkish) or accommodative (dovish) monetary stance?
2. **Yield & Rate Differential**: How does the rate differential influence institutional capital flows and carry trade direction for {snapshot.symbol.base_currency} vs {snapshot.symbol.quote_currency}?
3. **Global Risk Sentiment**: How does prevailing market risk appetite (Risk-On vs Risk-Off) impact this pair? (Note: USD and JPY often act as safe-havens).
4. **Data Integrity Note**: If scheduled intraday economic calendar releases are not provided, base your assessment strictly on established central bank policy paths and macro fundamentals. Never fabricate specific economic data figures.

Provide a concise, objective macroeconomic report. Conclude with a fundamental bias: BULLISH, BEARISH, or NEUTRAL.
"""


def build_bull_researcher_prompt(snapshot: MarketSnapshot, technical_report: str, macro_report: str, debate_history: str, opponent_argument: str) -> str:
    context = build_forex_instrument_context(snapshot)
    return f"""You are the Bull Researcher advocating for a LONG position on `{snapshot.symbol.value}` (strengthening of {snapshot.symbol.base_currency} against {snapshot.symbol.quote_currency}).

{context}

### Technical Analyst Report:
{technical_report}

### Macro Analyst Report:
{macro_report}

### Current Debate History:
{debate_history or "(Debate opening)"}

### Opponent's Last Point:
{opponent_argument}

Build a persuasive, evidence-based case for BUYING `{snapshot.symbol.value}`. Address the bear researcher's arguments directly, leveraging positive technical indicators and macro tailwinds. Be concise, sharp, and grounded in real data.
"""


def build_bear_researcher_prompt(snapshot: MarketSnapshot, technical_report: str, macro_report: str, debate_history: str, opponent_argument: str) -> str:
    context = build_forex_instrument_context(snapshot)
    return f"""You are the Bear Researcher advocating for a SHORT position on `{snapshot.symbol.value}` (weakening of {snapshot.symbol.base_currency} against {snapshot.symbol.quote_currency}).

{context}

### Technical Analyst Report:
{technical_report}

### Macro Analyst Report:
{macro_report}

### Current Debate History:
{debate_history or "(Debate opening)"}

### Opponent's Last Point:
{opponent_argument}

Build a persuasive, evidence-based case for SELLING `{snapshot.symbol.value}`. Address the bull researcher's arguments directly, leveraging technical resistance, downside momentum, or macro headwinds. Be concise, sharp, and grounded in real data.
"""


def build_research_manager_prompt(snapshot: MarketSnapshot, debate_history: str) -> str:
    context = build_forex_instrument_context(snapshot)
    return f"""You are the Research Manager moderating the Bull vs Bear debate for `{snapshot.symbol.value}`.

{context}

### Debate History:
{debate_history}

Critically weigh the evidence from both sides. Conflicting views are normal; decide which side presented the stronger, data-supported case. If neither side has a decisive edge or the market is in chop, recommend Hold.

Provide your synthesis in the structured ResearchPlan format:
- **recommendation**: Exactly one of Buy / Overweight / Hold / Underweight / Sell
- **rationale**: Concise summary of which arguments decided the outcome
- **strategic_actions**: Tactical guidance for the execution trader
"""


def build_trader_prompt(snapshot: MarketSnapshot, research_plan_text: str, technical_report: str) -> str:
    context = build_forex_instrument_context(snapshot)
    precision = snapshot.symbol.price_precision
    return f"""You are the Head FX Execution Trader. Your task is to turn the Research Manager's plan into an actionable order proposal for `{snapshot.symbol.value}`.

{context}

### Research Manager's Plan:
{research_plan_text}

### Technical Market Structure:
{technical_report}

Execution Guidelines:
1. **Action**: Exactly one of `Buy`, `Sell`, or `Hold`. (If the research plan is Hold/Underweight/Overweight with low conviction, pick `Hold`).
2. **Entry Price**: Grounded in the verified close price ({snapshot.close:.{precision}f}).
3. **Stop Loss (SL)**: MUST be set beyond recent support/resistance or at least 1.5 * ATR14 away.
4. **Take Profit (TP)**: Target level ensuring a Minimum Risk/Reward ratio of 1.5:1.
5. All price numbers must be absolute quotes (e.g. 1.08500 or 153.500), never percentages.
"""


def build_risk_analyst_prompt(
    analyst_type: str,
    snapshot: MarketSnapshot,
    trader_proposal_text: str,
    risk_history: str,
    opponent_argument: str,
) -> str:
    context = build_forex_instrument_context(snapshot)
    personas = {
        "Aggressive": "Focus on high-conviction momentum, upside capture, and capitalizing on trend breakouts.",
        "Conservative": "Focus on downside capital preservation, spread cost, false breakouts, and tight risk limits.",
        "Neutral": "Balance risk and reward objectively, evaluating probability, session liquidity, and baseline expectations.",
    }
    persona_desc = personas.get(analyst_type, "Evaluate risk.")

    return f"""You are the {analyst_type} FX Risk Analyst. {persona_desc}

{context}

### Trader's Order Proposal:
{trader_proposal_text}

### Risk Discussion History:
{risk_history or "(Beginning of risk review)"}

### Opponent's Last Point:
{opponent_argument}

Critically evaluate the proposed trade. Highlight key risk vectors (spread slippage, impending central bank volatility, ATR extremes). Output your critique concisely in a collegial debate format.
"""


def build_portfolio_manager_prompt(
    snapshot: MarketSnapshot,
    research_plan_text: str,
    trader_proposal_text: str,
    risk_debate_history: str,
) -> str:
    context = build_forex_instrument_context(snapshot)
    return f"""You are the Chief Investment Officer & Portfolio Manager. Synthesize the risk analysts' debate and issue the final decision on `{snapshot.symbol.value}`.

{context}

### Research Manager Plan:
{research_plan_text}

### Trader Execution Proposal:
{trader_proposal_text}

### Risk Analysts Debate:
{risk_debate_history}

Deliver the final decision in structured PortfolioDecision format:
- **rating**: Exactly one of Buy / Overweight / Hold / Underweight / Sell. Choose Hold if risk is uncompensated or evidence is mixed.
- **executive_summary**: High-level action summary (2-3 sentences).
- **investment_thesis**: Core quantitative and fundamental rationale.
- **price_target**: Target price in quote currency, or null.
- **time_horizon**: Recommended horizon (e.g. "H1 session", "1-4 hours").
"""

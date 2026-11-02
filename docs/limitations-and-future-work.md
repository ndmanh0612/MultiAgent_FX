# Known Limitations & Future Improvements

## 1. Known MVP Limitations

While this MVP provides a robust, end-to-end automated architecture adhering strictly to quantitative principles, the following limitations should be noted:

### 1.1 Single Timeframe Scope (H1 Only)
- Currently, the multi-agent graph analyzes the 1-Hour (`H1`) candle timeframe in isolation.
- **Risk**: A valid H1 bullish breakout might run directly into a major 4-Hour (`H4`) or Daily (`D1`) supply zone or key resistance level.

### 1.2 Macroeconomic Data Grounding
- The Macro Analyst agent currently derives currency regime sentiment from general knowledge and verified price action rather than direct live economic feeds.
- While the prompts strictly forbid hallucinating release numbers, the absence of a live economic calendar feed means sudden high-impact events (e.g., Non-Farm Payrolls, US CPI, FOMC rate decisions) must be avoided manually or via scheduled blackouts.

### 1.3 Fixed ATR Multiplier Levels
- Entry, SL, and TP are calculated using fixed volatility multiples (`1.5 * ATR14` for SL, `3.0 * ATR14` for TP).
- While mathematically consistent and yielding a clean 2:1 R:R, institutional execution often places stops slightly beyond recent swing highs/lows or liquidity pools rather than purely mathematical ATR buffers.

### 1.4 GitHub Actions Schedule Jitter
- GitHub Actions cron triggers are best-effort and can experience execution delays between 2 to 15 minutes during peak hours.
- Our code handles this gracefully via candle freshness checks (rejecting candles older than 2 hours as `stale`), but local APScheduler daemon execution remains the most timely and predictable option.

---

## 2. Future Improvements & Engineering Roadmap

### Phase A: Multi-Timeframe Confluence (MTF)
- Incorporate **D1** (macro trend filter), **H4** (market structure & key levels), and **H1** (execution trigger) into the `MarketSnapshot`.
- The Technical Analyst will evaluate alignment across all 3 timeframes before confirming directional bias.

### Phase B: Real-Time Economic Calendar Integration
- Integrate a live economic data feed (e.g., ForexFactory API or FXStreet REST endpoint).
- Implement an **Auto-Lockout Guardrail**: automatically force `NO_TRADE` 45 minutes prior to and 30 minutes following any High-Impact ("Red Folder") news release (CPI, NFP, Interest Rate decisions).

### Phase C: Dynamic Swing High/Low Structure Recognition
- Enhance the indicator suite to identify fractal swing highs and swing lows (e.g., Donchian Channels or ZigZag pivots).
- Anchor Stop Loss orders 2 pips beyond the most recent structural pivot for optimal order placement.

### Phase D: Multi-Stage Take Profit & Trailing Stops
- Enable tiered exit signals:
  - **TP1**: Close 50% position at 1.0x R:R and move Stop Loss to Break-Even (`Entry + spread`).
  - **TP2**: Close 30% position at 2.0x R:R.
  - **TP3 (Runner)**: Trail remaining 20% position using an EMA20 trailing stop.

### Phase E: Real-time Streaming & Web Dashboard
- Connect to OANDA's Streaming Pricing API (WebSocket) for real-time spread widening detection.
- Provide a lightweight FastAPI/Streamlit web dashboard displaying historical signal cards, equity curves, and multi-agent debate logs.

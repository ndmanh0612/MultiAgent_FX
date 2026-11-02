# Telegram Signal Format & Delivery Specification

This document details the Telegram notification service used by the **Forex AI Multi-Agent Signal Bot**.

---

## 1. Notification Principles

1. **Precision & Clarity**: Deliver all actionable levels (Entry, SL, TP, Risk/Reward) immediately visible at the top of the message.
2. **AI Transparency**: Provide concise bullet points summarizing the Multi-Agent consensus (Technical + Macro reasoning).
3. **Strict Risk Disclaimers**: Every signal carries an explicit disclaimer that signals are experimental quant research outputs and not financial advice.
4. **Anti-Spam Filter**: `NO_TRADE` evaluations are saved in SQLite for historical backtesting and auditing, but **never** sent to the Telegram channel to keep notifications clean and high-signal.

---

## 2. Example 1: BUY Signal (EUR/USD)

### Formatted Telegram Output

```text
🚀 <b>FOREX AI SIGNAL: BUY EUR/USD</b>

⏱ <b>Timeframe:</b> H1
🎯 <b>Action:</b> BUY
📍 <b>Entry:</b> 1.08500
🛑 <b>Stop Loss:</b> 1.08200 (30.0 pips)
🎯 <b>Take Profit:</b> 1.09100 (60.0 pips)
⚖️ <b>Risk / Reward:</b> 1 : 2.0
📅 <b>Candle Close:</b> 2026-11-02 12:00:00 UTC

🤖 <b>Multi-Agent Consensus:</b>
Technical Analyst identifies bullish market structure above EMA20 and EMA50 with RSI at 54.2 indicating upside momentum. Bull Researcher highlights strong support defense at 1.0820. Risk Manager approved 1:2.0 R:R with 1.2 pip spread within safe limits.

⚠️ <i>Experimental algorithmic research signal. Not financial advice. Past performance does not guarantee future results.</i>
```

---

## 3. Example 2: SELL Signal (USD/JPY - JPY Pip Precision)

Notice the 2-decimal pip precision formatting for Japanese Yen pairs (`153.200` vs `153.800` = 60 pips).

### Formatted Telegram Output

```text
🔻 <b>FOREX AI SIGNAL: SELL USD/JPY</b>

⏱ <b>Timeframe:</b> H1
🎯 <b>Action:</b> SELL
📍 <b>Entry:</b> 153.200
🛑 <b>Stop Loss:</b> 153.800 (60.0 pips)
🎯 <b>Take Profit:</b> 152.000 (120.0 pips)
⚖️ <b>Risk / Reward:</b> 1 : 2.0
📅 <b>Candle Close:</b> 2026-11-02 13:00:00 UTC

🤖 <b>Multi-Agent Consensus:</b>
H1 candle closed below EMA20 and EMA50 after rejection at 153.80 resistance. RSI at 41.5 showing bearish acceleration. Bear debaters cite growing intervention speculation from BoJ. Risk debater confirms favorable risk-reward ratio exceeding 1:2.0.

⚠️ <i>Experimental algorithmic research signal. Not financial advice. Past performance does not guarantee future results.</i>
```

---

## 4. Example 3: System Health Diagnostic Ping

When running `python -m app.cli test-telegram`:

```text
🤖 <b>Forex AI Signal Bot — Diagnostic Ping</b>

✅ Connectivity: OK
⏱ Server Time: 2026-11-02 14:30:00 UTC
📊 Timeframe: H1 (EUR/USD, GBP/USD, USD/JPY)
🛡 Status: Multi-agent pipeline online and monitoring
```

---

## 5. Security & Secret Protection

- In accordance with production security standards, the bot token (`TELEGRAM_BOT_TOKEN`) is automatically masked in all console logs (e.g., `7812***391`).
- Bot endpoints use HTTPS POST with bounded timeouts (10 seconds) and retry backoff.
- In `dry-run` mode (`--dry-run` or `TELEGRAM_DRY_RUN=true`), the exact message is logged to standard output without initiating an outbound network request to the Telegram API.

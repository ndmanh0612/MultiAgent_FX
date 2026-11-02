"""Telegram signal message formatting module."""

from __future__ import annotations

import html

from app.domain.models import ForexSignal, SignalAction


def format_telegram_signal_message(signal: ForexSignal) -> str:
    """Format a validated Forex trade signal into a high-visibility Telegram message (HTML mode).

    Must contain:
    - Currency pair
    - Timeframe
    - BUY or SELL
    - Reference Entry
    - Stop Loss
    - Take Profit
    - Risk/Reward
    - Analysis timestamp
    - Short AI reasoning
    - Experimental signal disclaimer
    """
    pair = signal.symbol
    precision = pair.price_precision

    pair_display = pair.value.replace("_", "/")
    timeframe = signal.timeframe

    if signal.action == SignalAction.BUY:
        header = f"🟢 <b>FOREX H1 SIGNAL: BUY {pair_display}</b>"
        direction_badge = "📈 <b>DIRECTION: BUY (LONG)</b>"
    elif signal.action == SignalAction.SELL:
        header = f"🔴 <b>FOREX H1 SIGNAL: SELL {pair_display}</b>"
        direction_badge = "📉 <b>DIRECTION: SELL (SHORT)</b>"
    else:
        header = f"⚪ <b>FOREX H1 UPDATE: NO TRADE ({pair_display})</b>"
        direction_badge = "⏸ <b>DIRECTION: NO TRADE (HOLD)</b>"

    time_str = signal.candle_timestamp.strftime("%Y-%m-%d %H:%M UTC")

    if signal.action in (SignalAction.BUY, SignalAction.SELL) and signal.entry is not None:
        entry_str = f"{signal.entry:.{precision}f}"
        sl_str = f"{signal.stop_loss:.{precision}f}" if signal.stop_loss else "N/A"
        tp_str = f"{signal.take_profit:.{precision}f}" if signal.take_profit else "N/A"
        sl_pips_str = f" ({signal.sl_pips:.1f} pips)" if signal.sl_pips else ""
        tp_pips_str = f" ({signal.tp_pips:.1f} pips)" if signal.tp_pips else ""
        rr_str = f"1 : {signal.risk_reward:.1f}" if signal.risk_reward else "N/A"
        spread_str = f"{signal.spread_pips:.1f} pips" if signal.spread_pips else "N/A"

        price_block = f"""
🎯 <b>Reference Entry</b>: <code>{entry_str}</code>
🛑 <b>Stop Loss</b>: <code>{sl_str}</code>{sl_pips_str}
🎁 <b>Take Profit</b>: <code>{tp_str}</code>{tp_pips_str}
⚖️ <b>Risk / Reward</b>: <b>{rr_str}</b>
📊 <b>Market Spread</b>: {spread_str}
"""
    else:
        price_block = "\n<i>No active trade parameters (Status: NO_TRADE / Filtered).</i>\n"

    reason_escaped = html.escape(signal.reason)

    message = f"""{header}
{direction_badge}
⏱ <b>Timeframe</b>: {timeframe}
📅 <b>Candle Close (UTC)</b>: {time_str}
{price_block}
🧠 <b>Multi-Agent Reasoning</b>:
<i>"{reason_escaped}"</i>

🤖 <b>Engine</b>: <code>{signal.model_name}</code>
──────────────────────────
⚠️ <b>DISCLAIMER</b>: <i>Experimental automated AI quantitative research signal. Not financial advice. Past performance does not guarantee future results. Never risk capital you cannot afford to lose.</i>
"""
    return message.strip()


def format_telegram_test_message() -> str:
    """Format a ping/connectivity test message for Telegram."""
    return (
        "🤖 <b>Forex AI Multi-Agent Bot — Connectivity Test</b>\n\n"
        "✅ <b>Status</b>: Connected successfully to Telegram Bot API.\n"
        "📡 <b>Mode</b>: Active & ready to stream H1 signals.\n"
        "🛡 <b>Environment</b>: Local Windows / GitHub Actions Automation.\n"
        "──────────────────────────\n"
        "<i>All systems operational.</i>"
    )

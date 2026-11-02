"""Forex signal evaluation and backtesting module with conservative intrabar resolution and baseline comparison."""

from __future__ import annotations

import logging
from datetime import datetime

from pydantic import BaseModel, Field

from app.data.indicators import calculate_indicators_dataframe
from app.domain.models import Candle, ForexSignal, SignalAction

logger = logging.getLogger(__name__)


class TradeOutcome(BaseModel):
    """Evaluation outcome for an individual trade signal."""

    signal_id: str | None = None
    symbol: str
    action: str
    outcome: str  # "WIN_TP", "LOSS_SL", "EXPIRED", "NO_TRADE"
    entry_price: float
    exit_price: float
    pnl_pips: float
    risk_reward_achieved: float
    bars_held: int
    exit_time: datetime


class BacktestMetrics(BaseModel):
    """Aggregated quantitative performance metrics for a backtest or evaluation sweep."""

    total_trades: int = Field(default=0)
    win_count: int = Field(default=0)
    loss_count: int = Field(default=0)
    win_rate_pct: float = Field(default=0.0)
    profit_factor: float = Field(default=0.0)
    expectancy_pips: float = Field(default=0.0)
    max_drawdown_pips: float = Field(default=0.0)
    total_pips: float = Field(default=0.0)
    baseline_win_rate_pct: float | None = None
    baseline_total_pips: float | None = None


def evaluate_signal_outcome(
    signal: ForexSignal,
    future_candles: list[Candle],
    max_holding_bars: int = 24,
    slippage_pips: float = 0.5,
) -> TradeOutcome:
    """Evaluate a signal forward in time without look-ahead bias.

    Conservative rule:
    If both TP and SL are breached in the same H1 candle, assume SL hit first.
    Models spread, transaction costs, and slippage.
    """
    pair = signal.symbol

    if signal.action == SignalAction.NO_TRADE or signal.entry is None or signal.stop_loss is None or signal.take_profit is None:
        return TradeOutcome(
            symbol=pair.value,
            action=signal.action.value,
            outcome="NO_TRADE",
            entry_price=0.0,
            exit_price=0.0,
            pnl_pips=0.0,
            risk_reward_achieved=0.0,
            bars_held=0,
            exit_time=signal.candle_timestamp,
        )

    entry = signal.entry
    sl = signal.stop_loss
    tp = signal.take_profit
    spread = signal.spread_pips or 1.5

    # Filter future candles to only those strictly after the signal candle timestamp
    eval_candles = [c for c in future_candles if c.timestamp > signal.candle_timestamp][:max_holding_bars]

    if not eval_candles:
        return TradeOutcome(
            symbol=pair.value,
            action=signal.action.value,
            outcome="EXPIRED",
            entry_price=entry,
            exit_price=entry,
            pnl_pips=0.0,
            risk_reward_achieved=0.0,
            bars_held=0,
            exit_time=signal.candle_timestamp,
        )

    for bar_idx, candle in enumerate(eval_candles, start=1):
        if signal.action == SignalAction.BUY:
            tp_hit = candle.high >= tp
            sl_hit = candle.low <= sl

            # Conservative intrabar resolution: if both hit in same candle, SL triggered first
            if sl_hit and tp_hit or sl_hit:
                effective_exit = sl - (slippage_pips * pair.pip_size)
                pnl_pips = -pair.price_to_pips(entry - effective_exit) - spread
                return TradeOutcome(
                    symbol=pair.value,
                    action="BUY",
                    outcome="LOSS_SL",
                    entry_price=entry,
                    exit_price=pair.round_price(effective_exit),
                    pnl_pips=pair.round_pips(pnl_pips),
                    risk_reward_achieved=-1.0,
                    bars_held=bar_idx,
                    exit_time=candle.timestamp,
                )
            elif tp_hit:
                effective_exit = tp - (slippage_pips * pair.pip_size)
                pnl_pips = pair.price_to_pips(effective_exit - entry) - spread
                rr_achieved = signal.risk_reward or 1.5
                return TradeOutcome(
                    symbol=pair.value,
                    action="BUY",
                    outcome="WIN_TP",
                    entry_price=entry,
                    exit_price=pair.round_price(effective_exit),
                    pnl_pips=pair.round_pips(pnl_pips),
                    risk_reward_achieved=rr_achieved,
                    bars_held=bar_idx,
                    exit_time=candle.timestamp,
                )

        elif signal.action == SignalAction.SELL:
            tp_hit = candle.low <= tp
            sl_hit = candle.high >= sl

            # Conservative intrabar resolution: if both hit, assume SL hit first
            if sl_hit and tp_hit or sl_hit:
                effective_exit = sl + (slippage_pips * pair.pip_size)
                pnl_pips = -pair.price_to_pips(effective_exit - entry) - spread
                return TradeOutcome(
                    symbol=pair.value,
                    action="SELL",
                    outcome="LOSS_SL",
                    entry_price=entry,
                    exit_price=pair.round_price(effective_exit),
                    pnl_pips=pair.round_pips(pnl_pips),
                    risk_reward_achieved=-1.0,
                    bars_held=bar_idx,
                    exit_time=candle.timestamp,
                )
            elif tp_hit:
                effective_exit = tp + (slippage_pips * pair.pip_size)
                pnl_pips = pair.price_to_pips(entry - effective_exit) - spread
                rr_achieved = signal.risk_reward or 1.5
                return TradeOutcome(
                    symbol=pair.value,
                    action="SELL",
                    outcome="WIN_TP",
                    entry_price=entry,
                    exit_price=pair.round_price(effective_exit),
                    pnl_pips=pair.round_pips(pnl_pips),
                    risk_reward_achieved=rr_achieved,
                    bars_held=bar_idx,
                    exit_time=candle.timestamp,
                )

    # If neither TP nor SL hit before max_holding_bars, mark as expired and close at final bar close
    last_bar = eval_candles[-1]
    if signal.action == SignalAction.BUY:
        pnl_pips = pair.price_to_pips(last_bar.close - entry) - spread
    else:
        pnl_pips = pair.price_to_pips(entry - last_bar.close) - spread

    return TradeOutcome(
        symbol=pair.value,
        action=signal.action.value,
        outcome="EXPIRED",
        entry_price=entry,
        exit_price=last_bar.close,
        pnl_pips=pair.round_pips(pnl_pips),
        risk_reward_achieved=round(pnl_pips / (signal.sl_pips or 30.0), 2),
        bars_held=len(eval_candles),
        exit_time=last_bar.timestamp,
    )


def compute_metrics(outcomes: list[TradeOutcome]) -> BacktestMetrics:
    """Calculate quantitative summary metrics from a collection of trade outcomes."""
    valid_trades = [o for o in outcomes if o.outcome in ("WIN_TP", "LOSS_SL", "EXPIRED")]
    if not valid_trades:
        return BacktestMetrics()

    total = len(valid_trades)
    wins = [o for o in valid_trades if o.pnl_pips > 0]
    losses = [o for o in valid_trades if o.pnl_pips <= 0]

    win_count = len(wins)
    loss_count = len(losses)
    win_rate = round((win_count / total) * 100.0, 1)

    gross_profit = sum(o.pnl_pips for o in wins)
    gross_loss = abs(sum(o.pnl_pips for o in losses))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

    total_pips = round(sum(o.pnl_pips for o in valid_trades), 1)
    expectancy = round(total_pips / total, 2)

    # Max Drawdown calculation over cumulative pips
    peak = 0.0
    cum = 0.0
    max_dd = 0.0
    for t in valid_trades:
        cum += t.pnl_pips
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    return BacktestMetrics(
        total_trades=total,
        win_count=win_count,
        loss_count=loss_count,
        win_rate_pct=win_rate,
        profit_factor=profit_factor,
        expectancy_pips=expectancy,
        max_drawdown_pips=round(max_dd, 1),
        total_pips=total_pips,
    )


def evaluate_ema_baseline(candles: list[Candle]) -> BacktestMetrics:
    """Evaluate a standard EMA20/EMA50 crossover baseline strategy over the same candles."""
    df = calculate_indicators_dataframe(candles)
    if len(df) < 55:
        return BacktestMetrics()

    outcomes: list[TradeOutcome] = []
    # Identify crossovers
    for i in range(51, len(df) - 10):
        prev_ema20 = df["ema20"].iloc[i - 1]
        prev_ema50 = df["ema50"].iloc[i - 1]
        curr_ema20 = df["ema20"].iloc[i]
        curr_ema50 = df["ema50"].iloc[i]

        bullish_cross = prev_ema20 <= prev_ema50 and curr_ema20 > curr_ema50
        bearish_cross = prev_ema20 >= prev_ema50 and curr_ema20 < curr_ema50

        if bullish_cross or bearish_cross:
            entry = df["close"].iloc[i]
            exit_bar = df["close"].iloc[min(i + 5, len(df) - 1)]  # Hold 5 bars
            pips = (exit_bar - entry) * 10000.0 if bullish_cross else (entry - exit_bar) * 10000.0
            outcomes.append(
                TradeOutcome(
                    symbol="BASELINE",
                    action="BUY" if bullish_cross else "SELL",
                    outcome="WIN_TP" if pips > 0 else "LOSS_SL",
                    entry_price=entry,
                    exit_price=exit_bar,
                    pnl_pips=round(pips - 1.5, 1),  # Subtract spread
                    risk_reward_achieved=1.0 if pips > 0 else -1.0,
                    bars_held=5,
                    exit_time=df["timestamp"].iloc[min(i + 5, len(df) - 1)],
                )
            )

    return compute_metrics(outcomes)

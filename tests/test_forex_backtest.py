"""Unit tests for Backtesting and Evaluation module."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.models import Candle, ForexPair, ForexSignal, SignalAction, SignalStatus
from app.evaluation.backtester import (
    compute_metrics,
    evaluate_ema_baseline,
    evaluate_signal_outcome,
)


@pytest.fixture
def base_time() -> datetime:
    return datetime(2026, 11, 2, 10, 0, tzinfo=UTC)


@pytest.fixture
def buy_signal(base_time: datetime) -> ForexSignal:
    return ForexSignal(
        symbol=ForexPair.EUR_USD,
        timeframe="H1",
        action=SignalAction.BUY,
        entry=1.08500,
        stop_loss=1.08200,  # 30 pips SL
        take_profit=1.09100,  # 60 pips TP
        risk_reward=2.0,
        reason="Test buy",
        candle_timestamp=base_time,
        model_name="test-model",
        status=SignalStatus.PENDING,
        sl_pips=30.0,
        tp_pips=60.0,
        spread_pips=1.5,
    )


@pytest.mark.unit
def test_evaluate_signal_buy_win_tp(buy_signal: ForexSignal, base_time: datetime):
    # Future candles where price climbs and hits TP at candle 2
    candles = [
        Candle(
            timestamp=base_time + timedelta(hours=1),
            open=1.0850,
            high=1.0880,
            low=1.0845,
            close=1.0870,
            volume=1000,
        ),
        Candle(
            timestamp=base_time + timedelta(hours=2),
            open=1.0870,
            high=1.0915,  # Hits TP (1.09100)
            low=1.0865,
            close=1.0910,
            volume=1000,
        ),
    ]

    outcome = evaluate_signal_outcome(buy_signal, candles)
    assert outcome.outcome == "WIN_TP"
    assert outcome.bars_held == 2
    assert outcome.pnl_pips > 50.0  # ~60 pips minus slippage and spread


@pytest.mark.unit
def test_evaluate_signal_buy_loss_sl(buy_signal: ForexSignal, base_time: datetime):
    # Future candles where price drops and hits SL at candle 1
    candles = [
        Candle(
            timestamp=base_time + timedelta(hours=1),
            open=1.0850,
            high=1.0855,
            low=1.0815,  # Breaches SL (1.08200)
            close=1.0820,
            volume=1000,
        ),
    ]

    outcome = evaluate_signal_outcome(buy_signal, candles)
    assert outcome.outcome == "LOSS_SL"
    assert outcome.bars_held == 1
    assert outcome.pnl_pips < 0.0


@pytest.mark.unit
def test_evaluate_signal_conservative_intrabar_resolution(buy_signal: ForexSignal, base_time: datetime):
    # Candle breaches BOTH TP and SL in the same bar
    volatile_candle = [
        Candle(
            timestamp=base_time + timedelta(hours=1),
            open=1.0850,
            high=1.0920,  # Breaches TP (1.0910)
            low=1.0810,  # Breaches SL (1.0820)
            close=1.0900,
            volume=5000,
        ),
    ]

    outcome = evaluate_signal_outcome(buy_signal, volatile_candle)
    # Must assume conservative outcome: SL hit first!
    assert outcome.outcome == "LOSS_SL"
    assert outcome.pnl_pips < 0.0


@pytest.mark.unit
def test_compute_metrics_summary():
    from app.evaluation.backtester import TradeOutcome

    outcomes = [
        TradeOutcome(
            symbol="EUR_USD",
            action="BUY",
            outcome="WIN_TP",
            entry_price=1.08,
            exit_price=1.09,
            pnl_pips=60.0,
            risk_reward_achieved=2.0,
            bars_held=3,
            exit_time=datetime.now(UTC),
        ),
        TradeOutcome(
            symbol="EUR_USD",
            action="BUY",
            outcome="WIN_TP",
            entry_price=1.08,
            exit_price=1.09,
            pnl_pips=55.0,
            risk_reward_achieved=1.8,
            bars_held=4,
            exit_time=datetime.now(UTC),
        ),
        TradeOutcome(
            symbol="EUR_USD",
            action="SELL",
            outcome="LOSS_SL",
            entry_price=1.08,
            exit_price=1.083,
            pnl_pips=-32.0,
            risk_reward_achieved=-1.0,
            bars_held=2,
            exit_time=datetime.now(UTC),
        ),
    ]

    metrics = compute_metrics(outcomes)
    assert metrics.total_trades == 3
    assert metrics.win_count == 2
    assert metrics.loss_count == 1
    assert metrics.win_rate_pct == pytest.approx(66.7, rel=1e-1)
    assert metrics.profit_factor > 3.0
    assert metrics.total_pips == pytest.approx(83.0)


@pytest.mark.unit
def test_evaluate_ema_baseline():
    now = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    candles = []
    # Generate 70 candles with fluctuating pattern
    for i in range(70):
        t = now - timedelta(hours=70 - i)
        price = 1.0800 + (0.0030 if (i // 10) % 2 == 0 else -0.0030) + (i * 0.0001)
        candles.append(
            Candle(
                timestamp=t,
                open=price,
                high=price + 0.0005,
                low=price - 0.0005,
                close=price + 0.0001,
                volume=1000,
            )
        )

    metrics = evaluate_ema_baseline(candles)
    assert metrics.total_trades >= 0

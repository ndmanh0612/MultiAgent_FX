"""Unit tests for Deterministic Signal Engine (Phase 4)."""

from datetime import UTC, datetime

import pytest

from app.domain.models import (
    ForexPair,
    ForexSignal,
    MarketSnapshot,
    SignalAction,
    SignalStatus,
    TechnicalIndicators,
)
from app.engine.signal_engine import SignalEngine


@pytest.fixture
def eur_usd_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        symbol=ForexPair.EUR_USD,
        timeframe="H1",
        candle_timestamp=datetime(2026, 11, 2, 14, 0, tzinfo=UTC),
        open=1.08400,
        high=1.08650,
        low=1.08350,
        close=1.08500,
        volume=2100,
        indicators=TechnicalIndicators(
            ema20=1.08380,
            ema50=1.08200,
            rsi14=58.2,
            atr14=0.00200,  # 20 pips ATR
        ),
        bid=1.08492,
        ask=1.08508,
        spread_pips=1.6,
        is_stale=False,
    )


@pytest.fixture
def usd_jpy_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        symbol=ForexPair.USD_JPY,
        timeframe="H1",
        candle_timestamp=datetime(2026, 11, 2, 14, 0, tzinfo=UTC),
        open=153.200,
        high=153.800,
        low=153.100,
        close=153.500,
        volume=3200,
        indicators=TechnicalIndicators(
            ema20=153.150,
            ema50=152.800,
            rsi14=61.5,
            atr14=0.300,  # 30 pips ATR
        ),
        bid=153.485,
        ask=153.505,
        spread_pips=2.0,
        is_stale=False,
    )


# ---------------------------------------------------------------------------
# Signal Generation Tests (BUY / SELL / JPY)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_signal_engine_buy_generation(eur_usd_snapshot: MarketSnapshot):
    engine = SignalEngine()
    analysis = {
        "action": "BUY",
        "final_trade_decision": "**Executive Summary**: Buy order approved based on strong EMA breakout and RSI momentum.",
    }

    signal = engine.generate_signal(analysis, eur_usd_snapshot)

    assert signal.action == SignalAction.BUY
    assert signal.status == SignalStatus.PENDING
    assert signal.symbol == ForexPair.EUR_USD
    assert signal.entry == pytest.approx(1.08500)
    assert signal.stop_loss is not None
    assert signal.take_profit is not None

    # Strict ordering: Stop Loss < Entry < Take Profit
    assert signal.stop_loss < signal.entry < signal.take_profit

    # R:R must be >= 1.5
    assert signal.risk_reward is not None
    assert signal.risk_reward >= 1.5

    # Check output dict schema against requirements
    out = signal.to_output_dict()
    assert out["symbol"] == "EUR_USD"
    assert out["timeframe"] == "H1"
    assert out["action"] == "BUY"
    assert out["entry"] == 1.08500
    assert out["stop_loss"] == signal.stop_loss
    assert out["take_profit"] == signal.take_profit
    assert out["risk_reward"] == signal.risk_reward
    assert out["candle_timestamp"] == "2026-11-02T14:00:00Z"
    assert out["status"] == "PENDING"
    assert len(out["reason"]) > 0


@pytest.mark.unit
def test_signal_engine_sell_generation(eur_usd_snapshot: MarketSnapshot):
    engine = SignalEngine()
    analysis = {
        "action": "SELL",
        "final_trade_decision": "**Executive Summary**: Sell order approved due to resistance rejection.",
    }

    signal = engine.generate_signal(analysis, eur_usd_snapshot)

    assert signal.action == SignalAction.SELL
    assert signal.status == SignalStatus.PENDING
    assert signal.entry == pytest.approx(1.08500)

    # Strict ordering for SELL: Take Profit < Entry < Stop Loss
    assert signal.take_profit < signal.entry < signal.stop_loss
    assert signal.risk_reward is not None
    assert signal.risk_reward >= 1.5


@pytest.mark.unit
def test_signal_engine_usd_jpy_precision(usd_jpy_snapshot: MarketSnapshot):
    engine = SignalEngine()
    analysis = {
        "action": "BUY",
        "final_trade_decision": "**Executive Summary**: Long USD/JPY on interest rate carry.",
    }

    signal = engine.generate_signal(analysis, usd_jpy_snapshot)

    assert signal.symbol == ForexPair.USD_JPY
    assert signal.action == SignalAction.BUY
    assert signal.entry == pytest.approx(153.500)

    # Check JPY pip distance: ATR14=0.30 -> raw SL = 1.5 * 30 pips = 45 pips = 0.450
    assert signal.sl_pips == pytest.approx(45.0)
    assert signal.stop_loss == pytest.approx(153.050)
    # TP distance: 2 * 45 = 90 pips = 0.900 -> TP = 154.400
    assert signal.take_profit == pytest.approx(154.400)
    assert signal.risk_reward == pytest.approx(2.0, rel=1e-2)


# ---------------------------------------------------------------------------
# Rejection & Safety Guards Tests (NO_TRADE)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_signal_engine_hold_to_no_trade(eur_usd_snapshot: MarketSnapshot):
    engine = SignalEngine()
    analysis = {
        "action": "NO_TRADE",
        "reason": "Market in range consolidation; risk reward unattractive.",
    }

    signal = engine.generate_signal(analysis, eur_usd_snapshot)

    assert signal.action == SignalAction.NO_TRADE
    assert signal.status == SignalStatus.REJECTED
    assert signal.entry is None
    assert signal.stop_loss is None
    assert signal.take_profit is None
    assert "Multi-agent consensus was NO_TRADE" in signal.reason


@pytest.mark.unit
def test_signal_engine_stale_data_rejection(eur_usd_snapshot: MarketSnapshot):
    stale_snapshot = eur_usd_snapshot.model_copy(update={"is_stale": True, "stale_reason": "Weekend closure"})
    engine = SignalEngine()
    analysis = {"action": "BUY"}

    signal = engine.generate_signal(analysis, stale_snapshot)

    assert signal.action == SignalAction.NO_TRADE
    assert signal.status == SignalStatus.REJECTED
    assert "Market data stale or closed" in signal.reason


@pytest.mark.unit
def test_signal_engine_spread_cost_rejection(eur_usd_snapshot: MarketSnapshot):
    # Set excessive spread (3.5 pips on EUR_USD exceeds max 3.0 pips)
    wide_spread_snapshot = eur_usd_snapshot.model_copy(update={"spread_pips": 3.8})
    engine = SignalEngine()
    analysis = {"action": "BUY"}

    signal = engine.generate_signal(analysis, wide_spread_snapshot)

    assert signal.action == SignalAction.NO_TRADE
    assert "Spread 3.8 pips exceeds maximum threshold" in signal.reason


@pytest.mark.unit
def test_signal_engine_deduplication(eur_usd_snapshot: MarketSnapshot):
    engine = SignalEngine()
    analysis = {"action": "BUY"}

    # First run succeeds
    first_signal = engine.generate_signal(analysis, eur_usd_snapshot)
    assert first_signal.action == SignalAction.BUY

    # Immediate second run with exact same candle timestamp is rejected as duplicate
    duplicate_signal = engine.generate_signal(analysis, eur_usd_snapshot)
    assert duplicate_signal.action == SignalAction.NO_TRADE
    assert "Duplicate signal rejected" in duplicate_signal.reason


# ---------------------------------------------------------------------------
# Pydantic Model Integrity Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_forex_signal_pydantic_validation_guards():
    now = datetime.now(UTC)

    # Invalid BUY ordering: SL > Entry
    with pytest.raises(ValueError, match="BUY order ordering violation"):
        ForexSignal(
            symbol=ForexPair.EUR_USD,
            action=SignalAction.BUY,
            entry=1.08500,
            stop_loss=1.08600,  # Invalid: above entry!
            take_profit=1.09000,
            risk_reward=2.0,
            reason="Test",
            candle_timestamp=now,
            model_name="test-model",
        )

    # Invalid SELL ordering: TP > Entry
    with pytest.raises(ValueError, match="SELL order ordering violation"):
        ForexSignal(
            symbol=ForexPair.EUR_USD,
            action=SignalAction.SELL,
            entry=1.08500,
            stop_loss=1.08900,
            take_profit=1.08600,  # Invalid: above entry!
            risk_reward=2.0,
            reason="Test",
            candle_timestamp=now,
            model_name="test-model",
        )

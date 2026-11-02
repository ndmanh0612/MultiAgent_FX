"""Unit tests for Telegram notification service and message formatting (Phase 5)."""

from datetime import UTC, datetime
from unittest import mock

import pytest
import requests

from app.domain.models import ForexPair, ForexSignal, SignalAction, SignalStatus
from app.notifications.formatter import (
    format_telegram_signal_message,
    format_telegram_test_message,
)
from app.notifications.telegram_service import (
    DeliveryResult,
    TelegramConfig,
    TelegramService,
)


@pytest.fixture
def sample_buy_signal() -> ForexSignal:
    return ForexSignal(
        symbol=ForexPair.EUR_USD,
        timeframe="H1",
        action=SignalAction.BUY,
        entry=1.08500,
        stop_loss=1.08200,
        take_profit=1.09100,
        risk_reward=2.0,
        reason="H1 Bullish structure supported by EMA20 > EMA50 and positive carry expectations.",
        candle_timestamp=datetime(2026, 11, 2, 12, 0, tzinfo=UTC),
        model_name="gemini-3.8-flash",
        status=SignalStatus.PENDING,
        sl_pips=30.0,
        tp_pips=60.0,
        spread_pips=1.5,
    )


# ---------------------------------------------------------------------------
# Formatting Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_telegram_message_formatting_elements(sample_buy_signal: ForexSignal):
    msg = format_telegram_signal_message(sample_buy_signal)

    # 1. Currency pair
    assert "EUR/USD" in msg
    # 2. Timeframe
    assert "H1" in msg
    # 3. Action
    assert "BUY" in msg
    # 4. Entry
    assert "1.08500" in msg
    # 5. Stop Loss
    assert "1.08200" in msg
    assert "30.0 pips" in msg
    # 6. Take Profit
    assert "1.09100" in msg
    assert "60.0 pips" in msg
    # 7. Risk/Reward
    assert "1 : 2.0" in msg
    # 8. Analysis timestamp UTC
    assert "2026-11-02 12:00 UTC" in msg
    # 9. Short AI reasoning
    assert "H1 Bullish structure" in msg
    # 10. Experimental disclaimer
    assert "DISCLAIMER" in msg


@pytest.mark.unit
def test_telegram_test_message_formatting():
    msg = format_telegram_test_message()
    assert "Connectivity Test" in msg
    assert "Status" in msg


# ---------------------------------------------------------------------------
# Telegram Service Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_telegram_service_dry_run(sample_buy_signal: ForexSignal):
    config = TelegramConfig(bot_token="test_token", chat_id="12345", dry_run=True)
    service = TelegramService(config)

    result = service.send_signal(sample_buy_signal)

    assert isinstance(result, DeliveryResult)
    assert result.success is True
    assert result.is_dry_run is True
    assert result.message_id == 999999


@pytest.mark.unit
def test_telegram_service_live_mock_success(sample_buy_signal: ForexSignal):
    config = TelegramConfig(bot_token="valid_token", chat_id="12345", dry_run=False)
    session = mock.MagicMock(spec=requests.Session)

    resp = mock.MagicMock(status_code=200)
    resp.json.return_value = {"ok": True, "result": {"message_id": 78910}}
    session.post.return_value = resp

    service = TelegramService(config, session=session)
    result = service.send_signal(sample_buy_signal)

    assert result.success is True
    assert result.message_id == 78910
    session.post.assert_called_once()


@pytest.mark.unit
def test_telegram_service_duplicate_prevention(sample_buy_signal: ForexSignal):
    config = TelegramConfig(dry_run=True)
    service = TelegramService(config)

    # First send succeeds
    res1 = service.send_signal(sample_buy_signal)
    assert res1.success is True

    # Second send is blocked as duplicate
    res2 = service.send_signal(sample_buy_signal)
    assert res2.success is False
    assert "Duplicate notification prevented" in res2.error


@pytest.mark.unit
def test_telegram_service_no_trade_skipped():
    config = TelegramConfig(dry_run=True)
    service = TelegramService(config)

    no_trade_signal = ForexSignal(
        symbol=ForexPair.EUR_USD,
        timeframe="H1",
        action=SignalAction.NO_TRADE,
        reason="Consolidation range",
        candle_timestamp=datetime(2026, 11, 2, 12, 0, tzinfo=UTC),
        model_name="test-model",
        status=SignalStatus.REJECTED,
    )

    result = service.send_signal(no_trade_signal)
    assert result.success is True
    assert "NO_TRADE signals are not broadcast" in result.error


@pytest.mark.unit
def test_telegram_service_error_handling(sample_buy_signal: ForexSignal):
    config = TelegramConfig(bot_token="invalid_token", chat_id="12345", dry_run=False)
    session = mock.MagicMock(spec=requests.Session)

    # 401 Unauthorized
    resp = mock.MagicMock(status_code=401)
    resp.json.return_value = {"ok": False, "description": "Unauthorized"}
    session.post.return_value = resp

    service = TelegramService(config, session=session)
    result = service.send_signal(sample_buy_signal)

    assert result.success is False
    assert "Unauthorized" in result.error


@pytest.mark.unit
def test_telegram_service_test_message():
    config = TelegramConfig(dry_run=True)
    service = TelegramService(config)

    result = service.send_test_message()
    assert result.success is True
    assert result.is_dry_run is True


@pytest.mark.unit
def test_telegram_config_security():
    config = TelegramConfig(bot_token="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ", chat_id="999", dry_run=True)
    repr_str = repr(config)

    # Token should be masked
    assert "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ" not in repr_str
    assert "..." in repr_str

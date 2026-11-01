"""Unit and integration tests for Forex Data Pipeline (Phase 2)."""

from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest
import requests

from app.data.indicators import (
    calculate_indicators_dataframe,
    calculate_latest_indicators,
)
from app.data.mocks import generate_mock_h1_candles
from app.data.oanda_client import (
    DataIntegrityError,
    OandaAuthError,
    OandaClient,
    OandaConfig,
    OandaTimeoutError,
    is_forex_market_open,
)
from app.domain.models import Candle, ForexPair, MarketSnapshot

# ---------------------------------------------------------------------------
# ForexPair & Domain Models Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_forex_pair_properties():
    eur_usd = ForexPair.EUR_USD
    gbp_usd = ForexPair.GBP_USD
    usd_jpy = ForexPair.USD_JPY

    # Pip size tests
    assert eur_usd.pip_size == 0.0001
    assert gbp_usd.pip_size == 0.0001
    assert usd_jpy.pip_size == 0.01

    # Precision tests
    assert eur_usd.pip_decimal_places == 4
    assert usd_jpy.pip_decimal_places == 2
    assert eur_usd.price_precision == 5
    assert usd_jpy.price_precision == 3

    # Pip conversion
    assert eur_usd.price_to_pips(0.0020) == pytest.approx(20.0)
    assert usd_jpy.price_to_pips(0.50) == pytest.approx(50.0)

    # Parsing variants
    assert ForexPair.from_string("EUR/USD") == ForexPair.EUR_USD
    assert ForexPair.from_string("eurusd") == ForexPair.EUR_USD
    assert ForexPair.from_string("USD_JPY") == ForexPair.USD_JPY
    assert ForexPair.from_string("usdjpy") == ForexPair.USD_JPY

    with pytest.raises(ValueError):
        ForexPair.from_string("INVALID_PAIR")


@pytest.mark.unit
def test_candle_model_validation():
    now = datetime.now(UTC)

    # Valid candle
    c = Candle(
        timestamp=now,
        open=1.0850,
        high=1.0890,
        low=1.0830,
        close=1.0870,
        volume=1200,
        complete=True,
    )
    assert c.timestamp.tzinfo == UTC
    assert c.high >= c.open and c.high >= c.close
    assert c.low <= c.open and c.low <= c.close

    # High < Low violation
    with pytest.raises(ValueError, match="High .* cannot be lower than Low"):
        Candle(timestamp=now, open=1.0850, high=1.0820, low=1.0830, close=1.0840, volume=100)

    # High < Open violation
    with pytest.raises(ValueError, match="High .* must be >= open"):
        Candle(timestamp=now, open=1.0890, high=1.0880, low=1.0820, close=1.0850, volume=100)

    # Low > Close violation
    with pytest.raises(ValueError, match="Low .* must be <= open"):
        Candle(timestamp=now, open=1.0850, high=1.0890, low=1.0860, close=1.0870, volume=100)


# ---------------------------------------------------------------------------
# Indicator Calculations Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_indicator_calculations():
    now = datetime.now(UTC)
    candles = []

    # Generate 60 candles with steady upward trend
    for i in range(60):
        t = now - timedelta(hours=60 - i)
        base = 1.0800 + i * 0.0005
        candles.append(
            Candle(
                timestamp=t,
                open=base,
                high=base + 0.0004,
                low=base - 0.0002,
                close=base + 0.0003,
                volume=1000,
                complete=True,
            )
        )

    df = calculate_indicators_dataframe(candles)
    assert len(df) == 60
    assert "ema20" in df.columns
    assert "ema50" in df.columns
    assert "rsi14" in df.columns
    assert "atr14" in df.columns

    # Verify latest values
    ind = calculate_latest_indicators(candles)
    assert ind.ema20 is not None
    assert ind.ema50 is not None
    assert ind.rsi14 is not None
    assert ind.atr14 is not None

    # Trend verification: EMA20 should be above EMA50 in strong uptrend
    assert ind.ema20 > ind.ema50
    # RSI should be high (> 70) due to consecutive gains
    assert ind.rsi14 > 70.0
    # ATR should reflect the range (~0.0006 per candle)
    assert 0.0004 <= ind.atr14 <= 0.0008


@pytest.mark.unit
def test_indicators_insufficient_history():
    now = datetime.now(UTC)
    # Only 10 candles
    candles = [
        Candle(
            timestamp=now - timedelta(hours=10 - i),
            open=1.0850,
            high=1.0860,
            low=1.0840,
            close=1.0855,
            volume=500,
            complete=True,
        )
        for i in range(10)
    ]

    ind = calculate_latest_indicators(candles)
    assert ind.ema20 is None
    assert ind.ema50 is None
    assert ind.rsi14 is None
    assert ind.atr14 is None


# ---------------------------------------------------------------------------
# OANDA Client Tests (Mock Mode & Data Integrity)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_oanda_client_fetches_completed_candles_mock():
    config = OandaConfig(mock_mode=True)
    client = OandaClient(config)

    # Fetch 50 candles for EUR_USD
    candles = client.fetch_h1_candles("EUR_USD", count=50, completed_only=True)
    assert len(candles) == 50

    # Ensure all are marked complete
    for c in candles:
        assert c.complete is True

    # Ensure timestamps are strictly increasing
    for i in range(1, len(candles)):
        assert candles[i].timestamp > candles[i - 1].timestamp
        assert candles[i].timestamp.tzinfo == UTC


@pytest.mark.unit
def test_oanda_client_filters_incomplete_candle():
    config = OandaConfig(mock_mode=True)
    client = OandaClient(config)

    # In mock generator, include_incomplete=True adds an incomplete candle as the last one
    with mock.patch("app.data.oanda_client.get_mock_oanda_candles_response") as mock_get:
        # 10 candles total, last one complete=False
        raw_list = generate_mock_h1_candles("EUR_USD", count=10, include_incomplete=True)
        mock_get.return_value = {"candles": raw_list}

        candles = client.fetch_h1_candles("EUR_USD", count=20, completed_only=True)
        assert len(candles) == 9  # Incomplete one was omitted
        assert all(c.complete is True for c in candles)


@pytest.mark.unit
def test_oanda_client_detects_timestamp_violation():
    config = OandaConfig(mock_mode=True)
    client = OandaClient(config)

    # Inject out of order timestamps
    now = datetime.now(UTC)
    bad_candles = [
        {
            "complete": True,
            "volume": 100,
            "time": now.strftime("%Y-%m-%dT%H:00:00.000000000Z"),
            "mid": {"o": "1.08", "h": "1.09", "l": "1.07", "c": "1.085"},
        },
        {
            "complete": True,
            "volume": 100,
            "time": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:00:00.000000000Z"),  # Prior to first!
            "mid": {"o": "1.08", "h": "1.09", "l": "1.07", "c": "1.085"},
        },
    ]

    with (
        mock.patch("app.data.oanda_client.get_mock_oanda_candles_response", return_value={"candles": bad_candles}),
        pytest.raises(DataIntegrityError, match="Candle ordering violation"),
    ):
        client.fetch_h1_candles("EUR_USD", count=2)


@pytest.mark.unit
def test_market_closure_detection():
    # Saturday noon UTC
    saturday_noon = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    assert is_forex_market_open(saturday_noon) is False

    # Wednesday noon UTC
    wednesday_noon = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    assert is_forex_market_open(wednesday_noon) is True

    # Friday 23:00 UTC (closed)
    friday_night = datetime(2026, 11, 6, 23, 0, tzinfo=UTC)
    assert is_forex_market_open(friday_night) is False

    # Sunday 22:00 UTC (open)
    sunday_night = datetime(2026, 11, 8, 22, 0, tzinfo=UTC)
    assert is_forex_market_open(sunday_night) is True


@pytest.mark.unit
def test_market_snapshot_mock_generation():
    config = OandaConfig(mock_mode=True)
    client = OandaClient(config)

    # Test EUR_USD snapshot
    snapshot = client.get_market_snapshot("EUR_USD", count=60)
    assert isinstance(snapshot, MarketSnapshot)
    assert snapshot.symbol == ForexPair.EUR_USD
    assert snapshot.timeframe == "H1"
    assert snapshot.indicators.ema20 is not None
    assert snapshot.indicators.ema50 is not None
    assert snapshot.indicators.rsi14 is not None
    assert snapshot.indicators.atr14 is not None
    assert snapshot.bid is not None
    assert snapshot.ask is not None
    assert snapshot.spread_pips is not None
    assert snapshot.spread_pips > 0

    # Test USD_JPY snapshot (JPY pip size scaling)
    snapshot_jpy = client.get_market_snapshot("USD_JPY", count=60)
    assert snapshot_jpy.symbol == ForexPair.USD_JPY
    assert snapshot_jpy.close > 100.0
    # Spread should be reasonable (e.g. 1.0 to 3.0 pips)
    assert 0.5 <= snapshot_jpy.spread_pips <= 5.0


@pytest.mark.unit
def test_oanda_client_http_retries_and_errors():
    config = OandaConfig(api_key="test_key", account_id="test_acc", mock_mode=False)
    session = mock.MagicMock(spec=requests.Session)
    client = OandaClient(config, session=session)

    # 401 Unauthorized
    resp_401 = mock.MagicMock(status_code=401, text="Unauthorized access")
    session.request.return_value = resp_401
    with pytest.raises(OandaAuthError):
        client.fetch_h1_candles("EUR_USD")

    # Timeout exhaust
    session.request.side_effect = requests.Timeout("Connection timed out")
    with pytest.raises(OandaTimeoutError):
        client.fetch_h1_candles("EUR_USD")

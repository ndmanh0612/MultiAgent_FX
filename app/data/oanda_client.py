"""OANDA REST API Client (Practice environment prioritized).

Handles H1 candle retrieval, candle completeness validation, UTC normalization,
indicator computation, bid/ask spread tracking, and network resilience.
Supports offline mock mode for reproducible unit testing.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import requests
from pydantic import BaseModel, Field

from app.data.indicators import calculate_latest_indicators
from app.data.mocks import get_mock_oanda_candles_response, get_mock_oanda_pricing_response
from app.domain.models import Candle, ForexPair, MarketSnapshot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class OandaError(Exception):
    """Base exception for OANDA API operations."""


class OandaAuthError(OandaError):
    """Raised when authentication fails (401)."""


class OandaRateLimitError(OandaError):
    """Raised when rate limit is exceeded (429)."""


class OandaTimeoutError(OandaError):
    """Raised on connection or read timeouts."""


class DataIntegrityError(OandaError):
    """Raised when candle data is malformed, missing, or out of sequence."""


class MarketClosedError(OandaError):
    """Raised when an operation cannot proceed because the market is closed."""


# ---------------------------------------------------------------------------
# Market Hour Helpers
# ---------------------------------------------------------------------------


def is_forex_market_open(dt: datetime | None = None) -> bool:
    """Check if the global Forex market is currently open.

    Forex trades 24/5:
    - Closes Friday around 21:00-22:00 UTC.
    - Closed all day Saturday.
    - Reopens Sunday around 21:00-22:00 UTC.
    """
    if dt is None:
        dt = datetime.now(UTC)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)

    weekday = dt.weekday()  # Monday is 0, Sunday is 6
    hour = dt.hour

    if weekday == 5:  # Saturday
        return False
    if weekday == 4 and hour >= 22:  # Friday late
        return False
    return not (weekday == 6 and hour < 21)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class OandaConfig(BaseModel):
    """OANDA API configuration."""

    api_key: str = Field(default="", description="OANDA v20 API Token")
    account_id: str = Field(default="", description="OANDA Account ID")
    environment: str = Field(default="practice", description="'practice' or 'live'")
    base_url: str = Field(default="", description="Base URL, auto-populated if empty")
    timeout_seconds: float = Field(default=15.0, description="HTTP timeout in seconds")
    max_retries: int = Field(default=3, description="Maximum retries for transient errors")
    mock_mode: bool = Field(default=False, description="Use offline mock responses")

    def model_post_init(self, __context: Any) -> None:
        if not self.base_url:
            if self.environment.lower() == "live":
                self.base_url = "https://api-fxtrade.oanda.com/v3"
            else:
                self.base_url = "https://api-fxpractice.oanda.com/v3"

    @classmethod
    def from_env(cls) -> OandaConfig:
        """Load configuration from environment variables."""
        api_key = os.getenv("OANDA_API_KEY", "")
        account_id = os.getenv("OANDA_ACCOUNT_ID", "")
        env = os.getenv("OANDA_ENVIRONMENT", "practice").lower()
        mock_mode_val = os.getenv("OANDA_MOCK_MODE", "").lower() in ("true", "1", "yes")

        # Automatically enable mock mode if API key is not supplied
        if not api_key:
            mock_mode_val = True

        return cls(
            api_key=api_key,
            account_id=account_id,
            environment=env,
            mock_mode=mock_mode_val,
        )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class OandaClient:
    """OANDA API adapter specialized for Forex H1 candles and pricing."""

    def __init__(self, config: OandaConfig | None = None, session: requests.Session | None = None):
        self.config = config or OandaConfig.from_env()
        self.session = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept-Datetime-Format": "RFC3339",
        }

    def _request(self, method: str, endpoint: str, params: dict | None = None) -> dict:
        """Execute HTTP request with retries and exponential backoff."""
        if self.config.mock_mode:
            raise RuntimeError("Live HTTP request called in mock mode")

        url = f"{self.config.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        headers = self._headers()

        last_error = None
        for attempt in range(self.config.max_retries):
            try:
                resp = self.session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    timeout=self.config.timeout_seconds,
                )
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 401:
                    raise OandaAuthError(f"Authentication failed: {resp.text}")
                elif resp.status_code == 429:
                    sleep_time = 2.0 ** attempt
                    logger.warning("OANDA 429 Rate limited. Backing off for %.1f seconds...", sleep_time)
                    time.sleep(sleep_time)
                    continue
                else:
                    raise OandaError(f"OANDA API error {resp.status_code}: {resp.text}")

            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                sleep_time = 1.5 ** attempt
                logger.warning("OANDA connection/timeout issue (%s). Retrying in %.1fs...", exc, sleep_time)
                time.sleep(sleep_time)

        raise OandaTimeoutError(f"Failed to communicate with OANDA after {self.config.max_retries} attempts: {last_error}")

    def fetch_h1_candles(
        self,
        symbol: ForexPair | str,
        count: int = 100,
        completed_only: bool = True,
        as_of_time: datetime | None = None,
    ) -> list[Candle]:
        """Fetch H1 candles for a currency pair, filtering for completed bars only.

        Args:
            symbol: Target ForexPair or string (e.g. 'EUR_USD')
            count: Number of completed candles to return
            completed_only: Discard currently forming (incomplete) candle
            as_of_time: Optional point-in-time boundary for historical runs
        """
        pair = ForexPair.from_string(str(symbol))

        if self.config.mock_mode:
            payload = get_mock_oanda_candles_response(
                symbol=pair.value,
                count=count + (1 if completed_only else 0),
                include_incomplete=completed_only,
                end_time=as_of_time,
            )
        else:
            params = {
                "granularity": "H1",
                "price": "M",  # Midpoint prices
                "count": min(count + 2, 500),  # Fetch extra to accommodate incomplete bar
            }
            if as_of_time:
                params["to"] = as_of_time.strftime("%Y-%m-%dT%H:%M:%SZ")

            endpoint = f"instruments/{pair.value}/candles"
            payload = self._request("GET", endpoint, params=params)

        raw_candles = payload.get("candles", [])
        if not raw_candles:
            raise DataIntegrityError(f"No candles returned for instrument {pair.value}")

        parsed_candles: list[Candle] = []
        for item in raw_candles:
            is_complete = bool(item.get("complete", False))
            if completed_only and not is_complete:
                # OANDA returns the currently ticking bar with complete=false. Skip it!
                continue

            mid = item.get("mid", {})
            try:
                candle = Candle(
                    timestamp=item["time"],
                    open=float(mid["o"]),
                    high=float(mid["h"]),
                    low=float(mid["l"]),
                    close=float(mid["c"]),
                    volume=int(item.get("volume", 0)),
                    complete=is_complete,
                )
                parsed_candles.append(candle)
            except (KeyError, ValueError, TypeError) as exc:
                raise DataIntegrityError(f"Corrupted candle payload received: {item}") from exc

        if not parsed_candles:
            raise DataIntegrityError(f"No completed candles available for {pair.value}")

        # Validate strictly ascending chronological ordering
        for i in range(1, len(parsed_candles)):
            prev_t = parsed_candles[i - 1].timestamp
            curr_t = parsed_candles[i].timestamp
            if curr_t <= prev_t:
                raise DataIntegrityError(
                    f"Candle ordering violation at index {i}: {curr_t} <= {prev_t}"
                )

        # Slice to requested count
        return parsed_candles[-count:]

    def fetch_pricing(self, symbol: ForexPair | str) -> dict[str, float]:
        """Fetch current bid, ask, and spread for the instrument."""
        pair = ForexPair.from_string(str(symbol))

        if self.config.mock_mode:
            payload = get_mock_oanda_pricing_response(pair.value)
        else:
            if not self.config.account_id:
                raise OandaError("account_id is required to fetch pricing")
            endpoint = f"accounts/{self.config.account_id}/pricing"
            params = {"instruments": pair.value}
            payload = self._request("GET", endpoint, params=params)

        prices = payload.get("prices", [])
        if not prices:
            raise DataIntegrityError(f"No pricing received for {pair.value}")

        quote = prices[0]
        try:
            bid = float(quote["closeoutBid"])
            ask = float(quote["closeoutAsk"])
            spread_pips = pair.round_pips(pair.price_to_pips(ask - bid))
            return {
                "bid": bid,
                "ask": ask,
                "spread_pips": spread_pips,
            }
        except (KeyError, ValueError, TypeError) as exc:
            raise DataIntegrityError(f"Malformed pricing payload: {quote}") from exc

    def get_market_snapshot(
        self,
        symbol: ForexPair | str,
        count: int = 100,
        as_of_time: datetime | None = None,
        max_staleness_hours: int = 4,
    ) -> MarketSnapshot:
        """Fetch completed candles, compute verified indicators, check staleness, and return snapshot."""
        pair = ForexPair.from_string(str(symbol))
        candles = self.fetch_h1_candles(pair, count=count, completed_only=True, as_of_time=as_of_time)

        if not candles:
            raise DataIntegrityError(f"No completed candles available for snapshot {pair.value}")

        latest_candle = candles[-1]
        now_utc = datetime.now(UTC) if as_of_time is None else as_of_time
        now_utc = now_utc.astimezone(UTC) if now_utc.tzinfo else now_utc.replace(tzinfo=UTC)

        # Check staleness & market closure
        is_stale = False
        stale_reason = None

        if not is_forex_market_open(now_utc):
            is_stale = True
            stale_reason = "Forex market is currently closed (weekend)"
        else:
            # Check elapsed hours since the last completed candle
            elapsed = (now_utc - latest_candle.timestamp).total_seconds() / 3600.0
            # An H1 candle is timestamped at its open, so a completed candle can be up to 2 hours old normally
            if elapsed > max_staleness_hours:
                is_stale = True
                stale_reason = f"Data is stale: latest candle timestamp is {latest_candle.timestamp.isoformat()}, elapsed {elapsed:.1f} hours"

        # Calculate indicators from verified candles
        indicators = calculate_latest_indicators(candles)

        # Fetch pricing / spread
        try:
            pricing = self.fetch_pricing(pair)
            bid = pricing.get("bid")
            ask = pricing.get("ask")
            spread_pips = pricing.get("spread_pips")
        except Exception as exc:
            logger.warning("Could not fetch real-time spread for %s: %s", pair.value, exc)
            bid = None
            ask = None
            spread_pips = None

        return MarketSnapshot(
            symbol=pair,
            timeframe="H1",
            candle_timestamp=latest_candle.timestamp,
            open=latest_candle.open,
            high=latest_candle.high,
            low=latest_candle.low,
            close=latest_candle.close,
            volume=latest_candle.volume,
            indicators=indicators,
            bid=bid,
            ask=ask,
            spread_pips=spread_pips,
            is_stale=is_stale,
            stale_reason=stale_reason,
            recent_candles=candles,
        )

"""Domain models for Forex instruments, market data, indicators, and signals."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ForexPair(StrEnum):
    """Supported Forex currency pairs."""

    EUR_USD = "EUR_USD"
    GBP_USD = "GBP_USD"
    USD_JPY = "USD_JPY"

    @classmethod
    def from_string(cls, value: str) -> ForexPair:
        """Parse string into ForexPair, accepting variants like EUR/USD or EURUSD."""
        cleaned = value.strip().upper().replace("/", "_").replace("-", "_")
        if cleaned in cls._value2member_map_:
            return cls(cleaned)
        # Try pairing without underscore: EURUSD -> EUR_USD
        if len(cleaned) == 6:
            candidate = f"{cleaned[:3]}_{cleaned[3:]}"
            if candidate in cls._value2member_map_:
                return cls(candidate)
        raise ValueError(f"Unsupported Forex pair: {value!r}. Supported: {[p.value for p in cls]}")

    @property
    def pip_size(self) -> float:
        """Pip size: 0.01 for JPY pairs, 0.0001 for all others."""
        return 0.01 if "JPY" in self.value else 0.0001

    @property
    def pip_decimal_places(self) -> int:
        """Number of decimal places for a pip."""
        return 2 if "JPY" in self.value else 4

    @property
    def price_precision(self) -> int:
        """Typical quoting precision (pipettes): 3 for JPY, 5 for others."""
        return 3 if "JPY" in self.value else 5

    @property
    def base_currency(self) -> str:
        """Base currency (first 3 letters)."""
        return self.value.split("_")[0]

    @property
    def quote_currency(self) -> str:
        """Quote currency (last 3 letters)."""
        return self.value.split("_")[1]

    @property
    def central_banks(self) -> tuple[str, str]:
        """Relevant central banks for base and quote currencies."""
        bank_map = {
            "EUR": "European Central Bank (ECB)",
            "USD": "Federal Reserve (Fed)",
            "GBP": "Bank of England (BoE)",
            "JPY": "Bank of Japan (BoJ)",
        }
        return (
            bank_map.get(self.base_currency, "Unknown CB"),
            bank_map.get(self.quote_currency, "Unknown CB"),
        )

    def round_price(self, price: float) -> float:
        """Round price to the pair's standard precision."""
        return round(price, self.price_precision)

    def round_pips(self, pips: float) -> float:
        """Round pips to 1 decimal place."""
        return round(pips, 1)

    def price_to_pips(self, price_diff: float) -> float:
        """Convert a price difference to pips."""
        return abs(price_diff) / self.pip_size


class Candle(BaseModel):
    """Normalized OHLCV candle model."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime = Field(description="Candle open timestamp in UTC")
    open: float = Field(gt=0, description="Open price")
    high: float = Field(gt=0, description="Highest price during period")
    low: float = Field(gt=0, description="Lowest price during period")
    close: float = Field(gt=0, description="Closing price")
    volume: int = Field(ge=0, description="Tick count or volume")
    complete: bool = Field(default=True, description="Whether this candle is closed/completed")

    @field_validator("timestamp", mode="before")
    @classmethod
    def _ensure_utc(cls, v: Any) -> datetime:
        if isinstance(v, str):
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        elif isinstance(v, datetime):
            dt = v
        else:
            raise ValueError(f"Invalid timestamp value: {v!r}")

        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)

    @model_validator(mode="after")
    def _validate_price_bounds(self) -> Candle:
        if self.high < self.low:
            raise ValueError(f"High ({self.high}) cannot be lower than Low ({self.low})")
        if self.high < max(self.open, self.close):
            raise ValueError(f"High ({self.high}) must be >= open ({self.open}) and close ({self.close})")
        if self.low > min(self.open, self.close):
            raise ValueError(f"Low ({self.low}) must be <= open ({self.open}) and close ({self.close})")
        return self


class TechnicalIndicators(BaseModel):
    """Calculated technical indicators for a market snapshot."""

    model_config = ConfigDict(frozen=True)

    ema20: float | None = Field(default=None, description="20-period Exponential Moving Average")
    ema50: float | None = Field(default=None, description="50-period Exponential Moving Average")
    rsi14: float | None = Field(default=None, description="14-period Relative Strength Index (0-100)")
    atr14: float | None = Field(default=None, description="14-period Average True Range")

    @field_validator("rsi14")
    @classmethod
    def _validate_rsi_bounds(cls, v: float | None) -> float | None:
        if v is not None and not (0 <= v <= 100):
            raise ValueError(f"RSI must be between 0 and 100, got {v}")
        return v

    @field_validator("atr14")
    @classmethod
    def _validate_atr_positive(cls, v: float | None) -> float | None:
        if v is not None and v < 0:
            raise ValueError(f"ATR must be non-negative, got {v}")
        return v


class MarketSnapshot(BaseModel):
    """Complete market snapshot for a Forex instrument at a specific H1 candle close."""

    symbol: ForexPair
    timeframe: str = Field(default="H1", description="Bar timeframe, strictly H1")
    candle_timestamp: datetime = Field(description="Timestamp of the latest completed candle in UTC")
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int = Field(ge=0)
    indicators: TechnicalIndicators
    bid: float | None = Field(default=None, gt=0, description="Current market bid price")
    ask: float | None = Field(default=None, gt=0, description="Current market ask price")
    spread_pips: float | None = Field(default=None, ge=0, description="Spread in pips")
    is_stale: bool = Field(default=False, description="Flag indicating if market data is stale or market is closed")
    stale_reason: str | None = Field(default=None, description="Reason if data was deemed stale")
    recent_candles: list[Candle] = Field(default_factory=list, description="Trailing verified completed candles")

    @field_validator("candle_timestamp", mode="before")
    @classmethod
    def _ensure_utc(cls, v: Any) -> datetime:
        if isinstance(v, str):
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        elif isinstance(v, datetime):
            dt = v
        else:
            raise ValueError(f"Invalid timestamp value: {v!r}")

        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)


class SignalAction(StrEnum):
    """Trading signal action."""

    BUY = "BUY"
    SELL = "SELL"
    NO_TRADE = "NO_TRADE"


class SignalStatus(StrEnum):
    """Trading signal status."""

    PENDING = "PENDING"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    FILLED = "FILLED"


class ForexSignal(BaseModel):
    """Validated, deterministic Forex trade signal produced by the Signal Engine."""

    symbol: ForexPair
    timeframe: str = Field(default="H1")
    action: SignalAction
    entry: float | None = Field(default=None, description="Verified Entry price level")
    stop_loss: float | None = Field(default=None, description="Verified Stop Loss price level")
    take_profit: float | None = Field(default=None, description="Verified Take Profit price level")
    risk_reward: float | None = Field(default=None, description="Risk/Reward ratio")
    reason: str = Field(description="Explanatory reasoning for signal or rejection")
    candle_timestamp: datetime = Field(description="Timestamp of the completed H1 candle in UTC")
    model_name: str = Field(description="Name/identifier of the model that evaluated the setup")
    status: SignalStatus = Field(default=SignalStatus.PENDING)
    sl_pips: float | None = Field(default=None, description="Stop Loss distance in pips")
    tp_pips: float | None = Field(default=None, description="Take Profit distance in pips")
    spread_pips: float | None = Field(default=None, description="Current market spread in pips at evaluation")

    @field_validator("candle_timestamp", mode="before")
    @classmethod
    def _ensure_utc(cls, v: Any) -> datetime:
        if isinstance(v, str):
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        elif isinstance(v, datetime):
            dt = v
        else:
            raise ValueError(f"Invalid timestamp value: {v!r}")

        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)

    @model_validator(mode="after")
    def _validate_signal_integrity(self) -> ForexSignal:
        if self.action in (SignalAction.BUY, SignalAction.SELL):
            if self.entry is None or self.stop_loss is None or self.take_profit is None or self.risk_reward is None:
                raise ValueError("Active signals (BUY/SELL) require entry, stop_loss, take_profit, and risk_reward.")

            if self.entry <= 0 or self.stop_loss <= 0 or self.take_profit <= 0:
                raise ValueError("Price levels must be strictly positive.")

            if self.action == SignalAction.BUY and not (self.stop_loss < self.entry < self.take_profit):
                raise ValueError(
                    f"BUY order ordering violation: Stop Loss ({self.stop_loss}) < "
                    f"Entry ({self.entry}) < Take Profit ({self.take_profit}) is required."
                )
            elif self.action == SignalAction.SELL and not (self.take_profit < self.entry < self.stop_loss):
                raise ValueError(
                    f"SELL order ordering violation: Take Profit ({self.take_profit}) < "
                    f"Entry ({self.entry}) < Stop Loss ({self.stop_loss}) is required."
                )
        return self

    def to_output_dict(self) -> dict[str, Any]:
        """Produce the exact dictionary representation requested by project specification."""
        return {
            "symbol": self.symbol.value,
            "timeframe": self.timeframe,
            "action": self.action.value,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "risk_reward": self.risk_reward,
            "reason": self.reason,
            "candle_timestamp": self.candle_timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "model_name": self.model_name,
            "status": self.status.value,
        }

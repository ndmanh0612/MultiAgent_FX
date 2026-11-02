"""Telegram notification service with bounded retries, deduplication, and dry-run support."""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import requests
from pydantic import BaseModel, Field

from app.domain.models import ForexSignal, SignalAction
from app.notifications.formatter import (
    format_telegram_signal_message,
    format_telegram_test_message,
)

logger = logging.getLogger(__name__)


class TelegramConfig(BaseModel):
    """Configuration for Telegram Bot API integration."""

    bot_token: str = Field(default="", description="Telegram Bot Token from @BotFather")
    chat_id: str = Field(default="", description="Destination Telegram Channel or User Chat ID")
    dry_run: bool = Field(default=False, description="Dry-run mode: logs messages without making HTTP calls")
    timeout_seconds: float = Field(default=15.0, description="HTTP request timeout in seconds")
    max_retries: int = Field(default=3, description="Maximum retries for network failures")
    base_url: str = Field(default="https://api.telegram.org", description="Telegram Bot API base URL")

    def __repr__(self) -> str:
        masked_token = f"{self.bot_token[:6]}...{self.bot_token[-4:]}" if len(self.bot_token) > 10 else "***"
        return f"TelegramConfig(chat_id={self.chat_id!r}, dry_run={self.dry_run}, bot_token={masked_token})"

    @classmethod
    def from_env(cls) -> TelegramConfig:
        """Instantiate configuration from environment variables."""
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        dry_run_env = os.getenv("TELEGRAM_DRY_RUN", "").strip().lower() in ("true", "1", "yes")

        # Automatically enable dry-run if bot token or chat ID is missing
        dry_run = dry_run_env or not token or not chat_id

        return cls(
            bot_token=token,
            chat_id=chat_id,
            dry_run=dry_run,
        )


class DeliveryResult(BaseModel):
    """Result of a Telegram message delivery attempt."""

    success: bool
    signal_key: str | None = None
    message_id: int | None = None
    chat_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None
    is_dry_run: bool = False


class TelegramService:
    """Delivers formatted signals to Telegram with confirmation, deduplication, and retry logic."""

    def __init__(self, config: TelegramConfig | None = None, session: requests.Session | None = None):
        self.config = config or TelegramConfig.from_env()
        self.session = session or requests.Session()
        self._sent_keys: set[str] = set()

    def _get_signal_key(self, signal: ForexSignal) -> str:
        """Deterministic uniqueness key for a signal."""
        return f"{signal.symbol.value}:{signal.candle_timestamp.isoformat()}:{signal.action.value}"

    def _send_raw_message(self, text: str) -> dict[str, Any]:
        """Send message via Telegram Bot API with bounded exponential backoff retries."""
        if self.config.dry_run:
            logger.info("[Telegram DRY-RUN] Message would be sent to chat_id=%s:\n%s", self.config.chat_id, text)
            return {"ok": True, "result": {"message_id": 999999, "chat": {"id": self.config.chat_id}}}

        if not self.config.bot_token or not self.config.chat_id:
            raise ValueError("Cannot send Telegram message: bot_token or chat_id is missing.")

        url = f"{self.config.base_url.rstrip('/')}/bot{self.config.bot_token}/sendMessage"
        payload = {
            "chat_id": self.config.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        last_error = None
        for attempt in range(self.config.max_retries):
            try:
                resp = self.session.post(url, json=payload, timeout=self.config.timeout_seconds)
                data = resp.json()

                if resp.status_code == 200 and data.get("ok") is True:
                    return data
                elif resp.status_code in (400, 401, 403, 404):
                    # Client/Auth errors should not be retried
                    err_desc = data.get("description", resp.text)
                    raise RuntimeError(f"Telegram API client error ({resp.status_code}): {err_desc}")
                else:
                    # 429 or 5xx server errors
                    err_desc = data.get("description", resp.text)
                    logger.warning("Telegram transient error (%s): %s. Attempt %d/%d", resp.status_code, err_desc, attempt + 1, self.config.max_retries)

            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                logger.warning("Telegram network failure: %s. Attempt %d/%d", exc, attempt + 1, self.config.max_retries)

            sleep_time = 1.5 ** attempt
            time.sleep(sleep_time)

        raise RuntimeError(f"Telegram message delivery failed after {self.config.max_retries} attempts: {last_error}")

    def send_signal(self, signal: ForexSignal, allow_duplicate: bool = False) -> DeliveryResult:
        """Send a validated BUY/SELL signal to Telegram."""
        key = self._get_signal_key(signal)

        # 1. Do not broadcast NO_TRADE signals to public channels
        if signal.action == SignalAction.NO_TRADE:
            logger.info("Skipping Telegram delivery for NO_TRADE signal on %s (%s)", signal.symbol.value, signal.reason)
            return DeliveryResult(
                success=True,
                signal_key=key,
                error="Skipped: NO_TRADE signals are not broadcast.",
                is_dry_run=self.config.dry_run,
            )

        # 2. Duplicate Detection
        if not allow_duplicate and key in self._sent_keys:
            logger.warning("Duplicate Telegram delivery prevented for %s", key)
            return DeliveryResult(
                success=False,
                signal_key=key,
                error="Duplicate notification prevented.",
                is_dry_run=self.config.dry_run,
            )

        # 3. Format Message
        message_text = format_telegram_signal_message(signal)

        # 4. Deliver Message
        try:
            res = self._send_raw_message(message_text)
            message_id = res.get("result", {}).get("message_id")
            self._sent_keys.add(key)
            logger.info("Telegram signal delivered successfully for %s (Message ID: %s)", key, message_id)
            return DeliveryResult(
                success=True,
                signal_key=key,
                message_id=message_id,
                chat_id=self.config.chat_id,
                is_dry_run=self.config.dry_run,
            )
        except Exception as exc:
            logger.error("Failed to deliver Telegram signal for %s: %s", key, exc, exc_info=True)
            return DeliveryResult(
                success=False,
                signal_key=key,
                error=str(exc),
                is_dry_run=self.config.dry_run,
            )

    def send_test_message(self, custom_text: str | None = None) -> DeliveryResult:
        """Send a diagnostic test message to verify Telegram bot credentials and connectivity."""
        text = custom_text or format_telegram_test_message()
        try:
            res = self._send_raw_message(text)
            message_id = res.get("result", {}).get("message_id")
            logger.info("Telegram test message sent successfully (Message ID: %s)", message_id)
            return DeliveryResult(
                success=True,
                message_id=message_id,
                chat_id=self.config.chat_id,
                is_dry_run=self.config.dry_run,
            )
        except Exception as exc:
            logger.error("Telegram test message failed: %s", exc)
            return DeliveryResult(
                success=False,
                error=str(exc),
                is_dry_run=self.config.dry_run,
            )

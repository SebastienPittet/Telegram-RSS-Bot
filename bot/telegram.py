"""Telegram Bot API adapter.

``build_payload`` and ``redact`` are pure; ``send_message`` is the network
boundary, with timeouts, bounded retries, exponential backoff, Telegram
rate-limit handling and secret redaction in logs.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

import requests

LOGGER = logging.getLogger(__name__)

API_TIMEOUT_SECONDS = 10
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2
DEFAULT_RETRY_AFTER_SECONDS = 5.0
TELEGRAM_API_BASE = "https://api.telegram.org"


def api_url(bot_token: str) -> str:
    """Build the ``sendMessage`` endpoint URL for a bot token."""
    return f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"


def build_payload(chat_id: str, text: str) -> dict[str, Any]:
    """Build the form payload; text stays out of the URL (pure function)."""
    return {"chat_id": chat_id, "text": text}


def redact(text: str, secret: str) -> str:
    """Replace every occurrence of ``secret`` with ``***`` (pure function)."""
    if not secret:
        return text
    return text.replace(secret, "***")


def _retry_after(response: requests.Response) -> float:
    try:
        body = json.loads(response.text)
        return float(body["parameters"]["retry_after"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return DEFAULT_RETRY_AFTER_SECONDS


def send_message(
    session: requests.Session,
    bot_token: str,
    chat_id: str,
    text: str,
    *,
    timeout: float = API_TIMEOUT_SECONDS,
    max_retries: int = MAX_RETRIES,
    sleep: Callable[[float], object] = time.sleep,
) -> bool:
    """Send one message to the Telegram channel. Returns True on success.

    The message travels in the POST body (never in the URL), transient
    failures are retried with exponential backoff, HTTP 429 honours
    Telegram's ``retry_after``, and client errors fail fast.
    """
    url = api_url(bot_token)
    payload = build_payload(chat_id, text)
    for attempt in range(1, max_retries + 1):
        try:
            response = session.post(url, data=payload, timeout=timeout)
        except requests.RequestException as exc:
            LOGGER.warning(
                "Attempt %d/%d failed: %s", attempt, max_retries, redact(str(exc), bot_token)
            )
        else:
            if response.status_code == 429:
                wait = _retry_after(response)
                LOGGER.info("Rate limited by Telegram; waiting %.1fs.", wait)
                sleep(wait)
                continue
            if response.ok:
                LOGGER.info("Posted to channel %s: %s", chat_id, text.splitlines()[0])
                return True
            if 400 <= response.status_code < 500:
                LOGGER.error(
                    "Telegram rejected the request (%d): %s",
                    response.status_code,
                    redact(response.text, bot_token),
                )
                return False
            LOGGER.warning(
                "Telegram API returned %d (attempt %d/%d).",
                response.status_code,
                attempt,
                max_retries,
            )
        if attempt < max_retries:
            sleep(BACKOFF_BASE_SECONDS**attempt)
    LOGGER.error("Giving up after %d attempts.", max_retries)
    return False

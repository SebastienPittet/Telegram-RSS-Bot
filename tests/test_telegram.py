"""Tests for the Telegram adapter: request shape, retries, redaction."""

from __future__ import annotations

import pytest
import requests

from bot.telegram import api_url, build_payload, redact, send_message
from tests.conftest import FakeResponse, FakeSession

TOKEN = "123:SECRET-tOKEN-xyz"


# --- pure helpers ------------------------------------------------------------


def test_api_url_format() -> None:
    assert api_url("42:ABC") == "https://api.telegram.org/bot42:ABC/sendMessage"


def test_build_payload_shape() -> None:
    assert build_payload("@chan", "hello") == {"chat_id": "@chan", "text": "hello"}


def test_redact_masks_secret() -> None:
    line = f"Connection error to api.telegram.org/bot{TOKEN}/sendMessage"
    assert TOKEN not in redact(line, TOKEN)


def test_redact_noop_on_empty_secret() -> None:
    assert redact("keep me", "") == "keep me"


# --- send_message (network boundary) ------------------------------------------


def test_send_message_posts_text_in_body() -> None:
    session = FakeSession()
    session.post_responses.append(FakeResponse(200, '{"ok":true}'))
    assert send_message(session, TOKEN, "@chan", "Hello <world> & co", sleep=lambda _s: None)
    (call,) = session.post_calls
    assert call["url"].endswith("/sendMessage")
    assert call["data"] == {"chat_id": "@chan", "text": "Hello <world> & co"}
    assert "<world>" not in call["url"] and "&" not in call["url"].split("?", 1)[-1]


def test_send_message_sends_payload_with_timeout() -> None:
    session = FakeSession()
    session.post_responses.append(FakeResponse(200))
    send_message(session, TOKEN, "@chan", "msg", timeout=7, sleep=lambda _s: None)
    assert session.post_calls[0]["timeout"] == 7


def test_send_message_retries_5xx_then_succeeds() -> None:
    session = FakeSession()
    session.post_responses += [FakeResponse(502, "bad gateway"), FakeResponse(200)]
    sleeps: list[float] = []
    assert send_message(session, TOKEN, "@chan", "msg", sleep=sleeps.append)
    assert sleeps == [2]  # exponential backoff: 2**1


def test_send_message_gives_up_after_max_retries() -> None:
    session = FakeSession()
    session.post_responses += [FakeResponse(500)] * 3
    assert not send_message(session, TOKEN, "@chan", "msg", sleep=lambda _s: None)
    assert len(session.post_calls) == 3


def test_send_message_does_not_retry_client_errors() -> None:
    session = FakeSession()
    session.post_responses.append(FakeResponse(400, '{"description":"bad chat id"}'))
    assert not send_message(session, TOKEN, "@chan", "msg", sleep=lambda _s: None)
    assert len(session.post_calls) == 1


def test_send_message_honours_retry_after_on_429() -> None:
    session = FakeSession()
    session.post_responses += [
        FakeResponse(429, '{"ok":false,"parameters":{"retry_after":7}}'),
        FakeResponse(200),
    ]
    sleeps: list[float] = []
    assert send_message(session, TOKEN, "@chan", "msg", sleep=sleeps.append)
    assert 7 in sleeps


def test_send_message_retries_network_exceptions() -> None:
    session = FakeSession()
    session.post_responses.append(requests.ConnectionError(f"failed for {TOKEN}"))
    session.post_responses.append(FakeResponse(200))
    assert send_message(session, TOKEN, "@chan", "msg", sleep=lambda _s: None)
    assert len(session.post_calls) == 2


def test_send_message_returns_false_when_all_attempts_raise() -> None:
    session = FakeSession()
    session.post_responses += [requests.Timeout("timed out")] * 3
    assert not send_message(session, TOKEN, "@chan", "msg", sleep=lambda _s: None)


def test_error_logs_never_leak_the_token(caplog: pytest.LogCaptureFixture) -> None:
    session = FakeSession()
    session.post_responses += [FakeResponse(401, f"unauthorized, token {TOKEN} is invalid")]
    with caplog.at_level("ERROR"):
        send_message(session, TOKEN, "@chan", "msg", sleep=lambda _s: None)
    assert TOKEN not in caplog.text

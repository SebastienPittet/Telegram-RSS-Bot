"""Shared test fixtures and helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest
import requests

NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=UTC)


@pytest.fixture()
def now() -> datetime:
    """Fixed 'current' time used across tests."""
    return NOW


def make_rss(items: list[dict], *, title: str = "Test Feed") -> bytes:
    """Build a minimal RSS 2.0 payload.

    Each item: {title, link, guid, age_seconds (int) | pubDate (rfc822 str)}.
    """
    blocks = []
    for item in items:
        if "pubDate" in item:
            pub = item["pubDate"]
        else:
            pub = format_datetime(NOW - timedelta(seconds=item["age_seconds"]), usegmt=True)
        blocks.append(
            f"<item><title>{item['title']}</title><link>{item['link']}</link>"
            f"<guid>{item['guid']}</guid><pubDate>{pub}</pubDate></item>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        f"<title>{title}</title><link>https://example.com</link><description>test</description>"
        f"{''.join(blocks)}</channel></rss>"
    )
    return xml.encode("utf-8")


class FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(
        self,
        status_code: int = 200,
        content: bytes | str = b"",
        text: str = "",
        url: str = "https://example.com/feed",
    ) -> None:
        self.status_code = status_code
        self.content = content.encode() if isinstance(content, str) else content
        self.text = text or (content if isinstance(content, str) else content.decode("utf-8", errors="replace"))
        self.url = url

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def raise_for_status(self) -> None:
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code} error for {self.url}")


class FakeSession:
    """Queue-backed fake HTTP session (no network, no extra dependency)."""

    def __init__(self) -> None:
        self.get_responses: list[object] = []
        self.post_responses: list[object] = []
        self.get_calls: list[str] = []
        self.post_calls: list[dict] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.get_calls.append(url)
        result = self.get_responses.pop(0) if self.get_responses else FakeResponse(404, b"gone")
        if isinstance(result, Exception):
            raise result
        return result  # type: ignore[return-value]

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.post_calls.append({"url": url, **kwargs})
        result = self.post_responses.pop(0) if self.post_responses else FakeResponse(500, "boom")
        if isinstance(result, Exception):
            raise result
        return result  # type: ignore[return-value]

    def close(self) -> None:  # parity with requests.Session
        pass


@pytest.fixture()
def session() -> FakeSession:
    return FakeSession()

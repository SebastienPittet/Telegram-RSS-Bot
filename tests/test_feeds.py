"""Tests for bot.feeds — parsing and pure selection logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bot.feeds import (
    FeedEntry,
    build_message,
    is_recent,
    parse_feed,
    prune_seen,
    select_new_entries,
)
from tests.conftest import NOW, make_rss


def entry(age_seconds: float, ident: str = "id") -> FeedEntry:
    return FeedEntry(
        title=f"Title {ident}",
        link=f"https://example.com/{ident}",
        published=NOW - timedelta(seconds=age_seconds),
        entry_id=ident,
    )


# --- parse_feed ------------------------------------------------------------


def test_parse_feed_extracts_entries() -> None:
    payload = make_rss(
        [
            {"title": "First", "link": "https://example.com/1", "guid": "g1", "age_seconds": 100},
            {"title": "Second", "link": "https://example.com/2", "guid": "g2", "age_seconds": 200},
        ]
    )
    entries = parse_feed(payload)
    assert len(entries) == 2
    first = entries[0]
    assert first.title == "First"
    assert first.link == "https://example.com/1"
    assert first.entry_id == "g1"
    assert first.published is not None and first.published.tzinfo is UTC


def test_parse_feed_empty_channel_returns_empty_tuple() -> None:
    assert parse_feed(make_rss([])) == ()


def test_parse_feed_garbage_returns_empty_tuple() -> None:
    assert parse_feed(b"<html><body>not a feed</body></html>") == ()


def test_parse_feed_falls_back_to_link_as_id() -> None:
    payload = make_rss([{"title": "T", "link": "https://example.com/x", "guid": "", "age_seconds": 1}])
    (only,) = parse_feed(payload)
    assert only.entry_id == "https://example.com/x"


def test_parse_feed_atom_format() -> None:
    payload = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<feed xmlns="http://www.w3.org/2005/Atom">'
        b"<entry><title>Atom post</title>"
        b"<link href=\"https://example.com/atom-post\"/>"
        b"<id>tag:example.com,2026:atom-post</id>"
        b"<updated>2026-10-26T11:55:00Z</updated></entry></feed>"
    )
    (only,) = parse_feed(payload)
    assert only.title == "Atom post"
    assert only.link == "https://example.com/atom-post"
    assert only.published == datetime(2026, 10, 26, 11, 55, tzinfo=UTC)


# --- is_recent ---------------------------------------------------------------


def test_is_recent_accepts_fresh_entry(now: datetime) -> None:
    assert is_recent(entry(100), now, max_age_seconds=1200)


def test_is_recent_rejects_old_entry(now: datetime) -> None:
    assert not is_recent(entry(1201), now, max_age_seconds=1200)


def test_is_recent_boundary_inclusive(now: datetime) -> None:
    assert is_recent(entry(1200), now, max_age_seconds=1200)


def test_is_recent_rejects_undated_entry(now: datetime) -> None:
    undated = FeedEntry("T", "https://e.com", None, "x")
    assert not is_recent(undated, now, max_age_seconds=12_000)


def test_is_recent_rejects_future_entry(now: datetime) -> None:
    assert not is_recent(entry(-60), now, max_age_seconds=1200)


# --- select_new_entries -------------------------------------------------------


def test_select_new_entries_filters_old_and_seen(now: datetime) -> None:
    entries = [entry(100, "fresh"), entry(5000, "old"), entry(200, "seen")]
    chosen = select_new_entries(entries, now, 1200, {"seen"})
    assert [e.entry_id for e in chosen] == ["fresh"]


def test_select_new_entries_preserves_input_order(now: datetime) -> None:
    entries = [entry(100, "a"), entry(50, "b"), entry(120, "c")]
    chosen = select_new_entries(entries, now, 1200, set())
    assert [e.entry_id for e in chosen] == ["a", "b", "c"]


# --- prune_seen -----------------------------------------------------------------


def test_prune_seen_drops_stale_ids() -> None:
    seen = {"a": 1000.0, "b": 0.0, "c": -1000.0}
    assert prune_seen(seen, now_timestamp=1000.0, max_age_seconds=1200) == {"a": 1000.0, "b": 0.0}


def test_prune_seen_returns_new_dict() -> None:
    seen = {"a": 5.0}
    result = prune_seen(seen, now_timestamp=5.0, max_age_seconds=10)
    result["b"] = 6.0
    assert seen == {"a": 5.0}


# --- build_message ---------------------------------------------------------------


def test_build_message_title_then_link() -> None:
    message = build_message(entry(0, "x"))
    assert message == "Title x\nhttps://example.com/x"


def test_build_message_never_contains_raw_feed_object() -> None:
    message = build_message(entry(0, "x"))
    assert "FeedEntry" not in message


@pytest.mark.parametrize("age,max_age,expected", [(0, 10, True), (11, 10, False), (10, 10, True)])
def test_is_recent_parametrized(age: int, max_age: int, expected: bool, now: datetime) -> None:
    assert is_recent(entry(age), now, max_age) is expected

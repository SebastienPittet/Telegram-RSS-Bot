"""RSS feed fetching (IO) and pure transformations over parsed entries."""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from time import struct_time

import feedparser
import requests

LOGGER = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 15
USER_AGENT = "TelegramRSSBot/2.0 (+https://github.com/SebastienPittet/Telegram-RSS-Bot)"


@dataclass(frozen=True)
class FeedEntry:
    """A normalised, immutable RSS/Atom entry."""

    title: str
    link: str
    published: datetime | None
    entry_id: str


def fetch_feed(
    url: str,
    session: requests.Session | None = None,
    timeout: float = FETCH_TIMEOUT_SECONDS,
) -> bytes:
    """Download the raw feed payload. Raises ``requests`` errors on failure."""
    http = session or requests.Session()
    response = http.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response.content


def _to_datetime(value: object) -> datetime | None:
    """Convert a feedparser ``struct_time`` (UTC) to an aware ``datetime``."""
    if not isinstance(value, struct_time):
        return None
    return datetime(*value[:6], tzinfo=UTC)


def parse_feed(payload: bytes | str) -> tuple[FeedEntry, ...]:
    """Parse raw feed content into ``FeedEntry`` records (pure function)."""
    feed = feedparser.parse(payload)
    if feed.bozo and not feed.entries:
        LOGGER.warning("Feed is not parseable: %s", getattr(feed, "bozo_exception", "unknown"))
    entries: list[FeedEntry] = []
    for raw in feed.entries:
        published = _to_datetime(raw.get("published_parsed") or raw.get("updated_parsed"))
        entry_id = str(raw.get("id") or raw.get("link") or raw.get("title") or "")
        entries.append(
            FeedEntry(
                title=str(raw.get("title", "")).strip(),
                link=str(raw.get("link", "")),
                published=published,
                entry_id=entry_id,
            )
        )
    return tuple(entries)


def age_in_seconds(entry: FeedEntry, now: datetime) -> float | None:
    """Seconds between ``now`` and the entry publication time; ``None`` if unknown."""
    if entry.published is None:
        return None
    return (now - entry.published).total_seconds()


def is_recent(entry: FeedEntry, now: datetime, max_age_seconds: float) -> bool:
    """True when the entry was published less than ``max_age_seconds`` ago."""
    age = age_in_seconds(entry, now)
    return age is not None and 0 <= age <= max_age_seconds


def select_new_entries(
    entries: Sequence[FeedEntry],
    now: datetime,
    max_age_seconds: float,
    seen_ids: Collection[str],
) -> list[FeedEntry]:
    """Recent entries that have not been published yet, in input order (pure)."""
    return [
        entry
        for entry in entries
        if is_recent(entry, now, max_age_seconds) and entry.entry_id not in seen_ids
    ]


def prune_seen(
    seen: Mapping[str, float],
    now_timestamp: float,
    max_age_seconds: float,
) -> dict[str, float]:
    """Return a copy of ``seen`` without ids older than ``max_age_seconds`` (pure)."""
    cutoff = now_timestamp - max_age_seconds
    return {entry_id: ts for entry_id, ts in seen.items() if ts >= cutoff}


def build_message(entry: FeedEntry) -> str:
    """Format a Telegram message for one entry (pure function)."""
    return f"{entry.title}\n{entry.link}"

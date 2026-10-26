"""Telegram RSS bot — application wiring and scheduler entry point.

This module is import-safe: nothing happens at import time. ``run_cycle``
orchestrates one feed cycle and returns the number of messages published,
which makes it directly testable. Run with ``python -m bot``.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import requests
import schedule

from bot.config import Config, ConfigError, load_config
from bot.feeds import (
    FeedEntry,
    build_message,
    fetch_feed,
    parse_feed,
    prune_seen,
    select_new_entries,
)
from bot.telegram import send_message

LOGGER = logging.getLogger(__name__)

MAX_FETCH_WORKERS = 8
TICK_SECONDS = 1
HEARTBEAT_ENV = "HEARTBEAT_FILE"


def utc_now() -> datetime:
    """Current time as an aware UTC datetime (single clock for the app)."""
    return datetime.now(UTC)


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging once, writing to stdout (12-factor)."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def collect_entries(
    urls: Sequence[str],
    session: requests.Session,
    *,
    max_workers: int = MAX_FETCH_WORKERS,
) -> list[FeedEntry]:
    """Fetch and parse every feed concurrently; failing feeds are skipped."""
    entries: list[FeedEntry] = []
    if not urls:
        return entries
    with ThreadPoolExecutor(max_workers=min(max_workers, len(urls))) as pool:
        futures = {pool.submit(fetch_feed, url, session): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                payload = future.result()
            except Exception as exc:  # noqa: BLE001 - one bad feed must not kill the cycle
                LOGGER.warning("Could not fetch %s: %s", url, exc)
                continue
            entries.extend(parse_feed(payload))
    return entries


def run_cycle(
    config: Config,
    session: requests.Session,
    seen: dict[str, float],
    *,
    now: datetime | None = None,
) -> int:
    """Run one feed cycle: fetch, filter, publish. Returns messages sent.

    ``seen`` maps entry id -> first-seen timestamp and is kept across cycles
    so entries are never published twice (per process). Entries are only
    marked as seen once Telegram accepted them, so failures retry next cycle.
    """
    current = now if now is not None else utc_now()
    pruned = prune_seen(seen, current.timestamp(), 2 * config.entry_max_time_old)
    if len(pruned) != len(seen):
        seen.clear()
        seen.update(pruned)

    entries = collect_entries(config.rss_urls, session)
    LOGGER.info("Parsed %d entr(ies) from %d feed(s).", len(entries), len(config.rss_urls))

    sent = 0
    for entry in select_new_entries(entries, current, config.entry_max_time_old, seen.keys()):
        if send_message(session, config.bot_token, config.channel_id, build_message(entry)):
            seen[entry.entry_id] = current.timestamp()
            sent += 1
    return sent


def write_heartbeat(path: str) -> None:
    """Touch the heartbeat file used by the Kubernetes liveness probe."""
    if not path:
        return
    try:
        Path(path).write_text(str(utc_now().timestamp()), encoding="utf-8")
    except OSError as exc:
        LOGGER.warning("Could not write heartbeat file %s: %s", path, exc)


def _make_job(
    config: Config,
    session: requests.Session,
    seen: dict[str, float],
    heartbeat_file: str,
) -> Callable[[], None]:
    """Build the scheduled job: one cycle, isolated from crashes."""

    def job() -> None:
        try:
            sent = run_cycle(config, session, seen)
            LOGGER.info("Cycle finished: %d new message(s) published.", sent)
            write_heartbeat(heartbeat_file)
        except Exception:
            LOGGER.exception("Feed cycle failed; retrying on next tick.")

    return job


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telegram RSS bot")
    parser.add_argument(
        "--config",
        default="configuration.toml",
        help="Path to a TOML configuration file (default: %(default)s).",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO"),
        help="Logging level: DEBUG, INFO, WARNING, ERROR (default: %(default)s).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Load configuration and run the scheduler until SIGTERM/SIGINT."""
    args = _parse_args(argv)
    setup_logging(args.log_level)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        LOGGER.critical("Configuration error: %s", exc)
        return 2

    stop_event = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        LOGGER.info("Received %s, shutting down.", signal.Signals(signum).name)
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, request_stop)

    session = requests.Session()
    seen: dict[str, float] = {}
    job = _make_job(config, session, seen, os.getenv(HEARTBEAT_ENV, ""))

    schedule.every(config.time_interval_min).minutes.do(job)
    LOGGER.info("Scheduler set for a run every %d minute(s).", config.time_interval_min)

    job()  # run immediately so liveness passes soon after (re)start
    while not stop_event.wait(TICK_SECONDS):
        schedule.run_pending()
    LOGGER.info("Bot stopped cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Command line interface: the ``--once`` switch and its single-cycle contract."""

from __future__ import annotations

from pathlib import Path

from bot.config import Config
from bot.telegram_rss_bot import _parse_args, run_once
from tests.conftest import FakeResponse, FakeSession, make_rss

CONFIG = Config(
    time_interval_min=1,
    entry_max_time_old=3600,
    bot_token="token",
    channel_id="@chan",
    rss_urls=("https://example.com/feed",),
)

ONE_ITEM = make_rss([{"title": "New", "link": "https://example.com/n", "guid": "n", "age_seconds": 60}])


def test_once_flag_defaults_to_off() -> None:
    assert _parse_args([]).once is False


def test_once_flag_is_recognised() -> None:
    assert _parse_args(["--once"]).once is True


def test_run_once_publishes_exactly_one_cycle() -> None:
    session = FakeSession()
    session.get_responses.append(FakeResponse(200, ONE_ITEM))
    session.post_responses.append(FakeResponse(200))

    assert run_once(CONFIG, session) == 0
    assert len(session.get_calls) == 1
    assert len(session.post_calls) == 1


def test_run_once_writes_the_heartbeat(tmp_path: Path) -> None:
    session = FakeSession()
    session.get_responses.append(FakeResponse(200, ONE_ITEM))
    session.post_responses.append(FakeResponse(200))
    heartbeat = tmp_path / "heartbeat"

    assert run_once(CONFIG, session, str(heartbeat)) == 0
    assert heartbeat.exists()


def test_run_once_reports_failure_instead_of_raising() -> None:
    """A crashed cycle must surface as a non-zero exit code, not a traceback."""
    session = FakeSession()
    session.get_responses.append(FakeResponse(200, ONE_ITEM))
    session.post_responses.append(RuntimeError("boom"))

    assert run_once(CONFIG, session) == 1

"""Tests for the application wiring: collect_entries / run_cycle / heartbeat."""

from __future__ import annotations

from pathlib import Path

import pytest

from bot.config import Config
from bot.telegram_rss_bot import collect_entries, run_cycle, write_heartbeat
from tests.conftest import NOW, FakeResponse, FakeSession, make_rss

CONFIG = Config(
    time_interval_min=20,
    entry_max_time_old=1200,
    bot_token="42:TOKEN",
    channel_id="@chan",
    rss_urls=("https://feed.example/a", "https://feed.example/b"),
)


def make_feed(age_seconds: int, ident: str) -> bytes:
    return make_rss(
        [{"title": f"News {ident}", "link": f"https://ex.com/{ident}", "guid": ident, "age_seconds": age_seconds}]
    )


# --- collect_entries -----------------------------------------------------


def test_collect_entries_fetches_all_urls() -> None:
    session = FakeSession()
    session.get_responses += [FakeResponse(200, make_feed(10, "a")), FakeResponse(200, make_feed(20, "b"))]
    entries = collect_entries(CONFIG.rss_urls, session)  # type: ignore[arg-type]
    assert sorted(session.get_calls) == ["https://feed.example/a", "https://feed.example/b"]
    assert {e.entry_id for e in entries} == {"a", "b"}


def test_collect_entries_survives_one_failing_feed() -> None:
    import requests

    session = FakeSession()
    session.get_responses += [requests.ConnectionError("dns failure"), FakeResponse(200, make_feed(10, "b"))]
    entries = collect_entries(CONFIG.rss_urls, session)  # type: ignore[arg-type]
    assert [e.entry_id for e in entries] == ["b"]


def test_collect_entries_survives_unparseable_feed() -> None:
    session = FakeSession()
    session.get_responses += [FakeResponse(200, b"<html>nope</html>"), FakeResponse(200, make_feed(10, "b"))]
    entries = collect_entries(CONFIG.rss_urls, session)  # type: ignore[arg-type]
    assert [e.entry_id for e in entries] == ["b"]


def test_collect_entries_empty_url_list() -> None:
    assert collect_entries([], FakeSession()) == []  # type: ignore[arg-type]


# --- run_cycle --------------------------------------------------------------


def test_run_cycle_publishes_fresh_entries() -> None:
    session = FakeSession()
    session.get_responses += [FakeResponse(200, make_feed(60, "a")), FakeResponse(200, make_feed(120, "b"))]
    session.post_responses += [FakeResponse(200), FakeResponse(200)]
    seen: dict[str, float] = {}
    sent = run_cycle(CONFIG, session, seen, now=NOW)  # type: ignore[arg-type]
    assert sent == 2
    assert set(seen) == {"a", "b"}
    (first,) = session.post_calls[:1]
    assert first["data"]["chat_id"] == "@chan"


def test_run_cycle_skips_old_entries() -> None:
    session = FakeSession()
    session.get_responses += [FakeResponse(200, make_feed(9999, "old")), FakeResponse(200, make_rss([]))]
    sent = run_cycle(CONFIG, session, {}, now=NOW)  # type: ignore[arg-type]
    assert sent == 0 and session.post_calls == []


def test_run_cycle_does_not_republish_seen_entries() -> None:
    session = FakeSession()
    session.get_responses += [FakeResponse(200, make_feed(60, "a")), FakeResponse(200, make_rss([]))]
    session.post_responses.append(FakeResponse(200))
    seen = {"a": NOW.timestamp() - 300}
    assert run_cycle(CONFIG, session, seen, now=NOW) == 0  # type: ignore[arg-type]


def test_run_cycle_keeps_failed_sends_unseen_for_retry() -> None:
    session = FakeSession()
    session.get_responses += [FakeResponse(200, make_feed(60, "a")), FakeResponse(200, make_rss([]))]
    session.post_responses.append(FakeResponse(400, "rejected"))
    seen: dict[str, float] = {}
    assert run_cycle(CONFIG, session, seen, now=NOW) == 0  # type: ignore[arg-type]
    assert "a" not in seen  # will be retried next cycle


def test_run_cycle_prunes_stale_seen_ids() -> None:
    session = FakeSession()
    session.get_responses += [FakeResponse(200, make_rss([])), FakeResponse(200, make_rss([]))]
    ancient = NOW.timestamp() - 10 * CONFIG.entry_max_time_old
    seen = {"ancient": ancient}
    run_cycle(CONFIG, session, seen, now=NOW)  # type: ignore[arg-type]
    assert seen == {}


# --- heartbeat ---------------------------------------------------------------


def test_write_heartbeat_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "heartbeat"
    write_heartbeat(str(target))
    assert target.exists() and float(target.read_text(encoding="utf-8")) > 0


def test_write_heartbeat_noop_when_unset() -> None:
    write_heartbeat("")  # must not raise


def test_write_heartbeat_survives_os_error(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        write_heartbeat(str(tmp_path))  # a directory -> IsADirectoryError
    assert "heartbeat" in caplog.text.lower()


@pytest.mark.parametrize("bad_url_status", [404, 500])
def test_collect_entries_skips_http_errors(bad_url_status: int) -> None:
    session = FakeSession()
    session.get_responses += [FakeResponse(bad_url_status, b"nope"), FakeResponse(200, make_feed(10, "ok"))]
    entries = collect_entries(CONFIG.rss_urls, session)  # type: ignore[arg-type]
    assert [e.entry_id for e in entries] == ["ok"]

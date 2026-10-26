"""End-to-end integration test over real local HTTP sockets (no internet needed).

A throwaway HTTP server plays both the RSS host and the Telegram API, so
``run_cycle`` exercises requests, feedparser, retries and POST bodies for real.
"""

from __future__ import annotations

import http.server
import json
import threading
from datetime import timedelta
from email.utils import format_datetime
from urllib.parse import unquote_plus

import pytest
import requests

from bot.config import Config
from bot.telegram_rss_bot import run_cycle
from tests.conftest import NOW

pytestmark = pytest.mark.integration

TOKEN = "42:INTEGRATION"
POSTED: list[dict] = []


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - required name
        if self.path == "/feed.xml":
            fresh = format_datetime(NOW - timedelta(seconds=60), usegmt=True)
            body = (
                '<?xml version="1.0"?><rss version="2.0"><channel><title>Live</title>'
                "<link>http://localhost</link><description>d</description>"
                f"<item><title>Fresh news</title><link>http://localhost/post/1</link>"
                f"<guid>live-1</guid><pubDate>{fresh}</pubDate></item>"
                "</channel></rss>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802 - required name
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        form = {k: unquote_plus(v) for k, _, v in (kv.partition("=") for kv in raw.split("&"))}
        POSTED.append({"path": self.path, "form": form})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True}).encode())

    def log_message(self, *args: object) -> None:  # silence
        pass


@pytest.fixture()
def live_server() -> object:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_end_to_end_cycle_publishes_over_http(
    live_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    POSTED.clear()
    monkeypatch.setattr(
        "bot.telegram.TELEGRAM_API_BASE", live_server, raising=False
    )  # keep API host local; url rebuilt inside send_message
    config = Config(
        time_interval_min=20,
        entry_max_time_old=1200,
        bot_token=TOKEN,
        channel_id="@chan",
        rss_urls=(f"{live_server}/feed.xml", "http://127.0.0.1:1/unreachable"),
    )
    with requests.Session() as session:
        sent = run_cycle(config, session, {}, now=NOW)
    assert sent == 1
    (post,) = POSTED
    assert post["path"] == f"/bot{TOKEN}/sendMessage"
    assert post["form"]["chat_id"] == "@chan"
    assert "Fresh news" in post["form"]["text"]

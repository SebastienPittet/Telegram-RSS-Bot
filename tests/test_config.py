"""Tests for bot.config — pure parsers + IO wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bot.config import (
    Config,
    ConfigError,
    load_config,
    parse_env_config,
    parse_toml_config,
    split_urls,
    to_positive_int,
    validate_url,
)

VALID_ENV = {
    "TIME_INTERVAL_MIN": "20",
    "ENTRY_MAX_TIME_OLD": "1200",
    "BOT_TOKEN": "123456:ABC-DEF",
    "CHANNEL_ID": "@SwissITNews",
    "RSS_URLS": "https://a.example/feed https://b.example/feed",
}

VALID_TOML: dict[str, Any] = {
    "TIME_INTERVAL_MIN": 20,
    "ENTRY_MAX_TIME_OLD": 1200,
    "BOT_TOKEN": "123456:ABC-DEF",
    "CHANNEL_ID": "@SwissITNews",
    "RSS_URLS": ["https://a.example/feed", "https://b.example/feed"],
}


# --- split_urls ---------------------------------------------------------


def test_split_urls_splits_on_whitespace() -> None:
    assert split_urls("https://a/f https://b/f") == ("https://a/f", "https://b/f")


def test_split_urls_splits_on_commas_and_newlines() -> None:
    raw = "https://a/f,\nhttps://b/f,\thttps://c/f"
    assert split_urls(raw) == ("https://a/f", "https://b/f", "https://c/f")


def test_split_urls_strips_quotes_and_empty_tokens() -> None:
    assert split_urls('"https://a/f" \'\'   ') == ("https://a/f",)


# --- validation helpers --------------------------------------------------


@pytest.mark.parametrize("url", ["https://a/f", "http://a/f"])
def test_validate_url_accepts_http_and_https(url: str) -> None:
    assert validate_url(url) == url


@pytest.mark.parametrize(
    "url",
    ["ftp://a/f", "file:///etc/passwd", "https://", "/relative/path", "not a url"],
)
def test_validate_url_rejects_everything_else(url: str) -> None:
    with pytest.raises(ConfigError):
        validate_url(url)


@pytest.mark.parametrize(("value", "expected"), [("20", 20), (" 5 ", 5), (7, 7)])
def test_to_positive_int_accepts_positive_values(value: Any, expected: int) -> None:
    assert to_positive_int(value, "N") == expected


@pytest.mark.parametrize("value", ["0", "-3", "abc", "", None])
def test_to_positive_int_rejects_invalid_values(value: Any) -> None:
    with pytest.raises(ConfigError):
        to_positive_int(value, "N")


# --- parse_env_config ----------------------------------------------------


def test_parse_env_config_happy_path() -> None:
    config = parse_env_config(VALID_ENV)
    assert config == Config(
        time_interval_min=20,
        entry_max_time_old=1200,
        bot_token="123456:ABC-DEF",
        channel_id="@SwissITNews",
        rss_urls=("https://a.example/feed", "https://b.example/feed"),
    )


def test_parse_env_config_multiline_env_value() -> None:
    env = dict(VALID_ENV, RSS_URLS='"https://a/f\nhttps://b/f"')
    assert parse_env_config(env).rss_urls == ("https://a/f", "https://b/f")


@pytest.mark.parametrize("missing", list(VALID_ENV))
def test_parse_env_config_reports_every_missing_key(missing: str) -> None:
    env = {k: v for k, v in VALID_ENV.items() if k != missing}
    with pytest.raises(ConfigError, match=missing):
        parse_env_config(env)


def test_parse_env_config_rejects_non_numeric_interval() -> None:
    env = dict(VALID_ENV, TIME_INTERVAL_MIN="ten")
    with pytest.raises(ConfigError, match="TIME_INTERVAL_MIN"):
        parse_env_config(env)


def test_parse_env_config_rejects_invalid_url_scheme() -> None:
    env = dict(VALID_ENV, RSS_URLS="file:///etc/passwd")
    with pytest.raises(ConfigError, match="Invalid RSS URL"):
        parse_env_config(env)


# --- parse_toml_config ----------------------------------------------------


def test_parse_toml_config_happy_path() -> None:
    config = parse_toml_config(VALID_TOML)
    assert config.time_interval_min == 20
    assert config.rss_urls == ("https://a.example/feed", "https://b.example/feed")


def test_parse_toml_config_reports_missing_key() -> None:
    data = {k: v for k, v in VALID_TOML.items() if k != "BOT_TOKEN"}
    with pytest.raises(ConfigError, match="BOT_TOKEN"):
        parse_toml_config(data)


def test_parse_toml_config_accepts_string_rss_urls() -> None:
    data = dict(VALID_TOML, RSS_URLS="https://a/f https://b/f")
    assert len(parse_toml_config(data).rss_urls) == 2


# --- load_config (IO wrapper) ---------------------------------------------


def test_load_config_prefers_toml_file(tmp_path: Path) -> None:
    path = tmp_path / "configuration.toml"
    path.write_text(
        'TIME_INTERVAL_MIN = 5\nENTRY_MAX_TIME_OLD = 60\n'
        'BOT_TOKEN = "toml-token"\nCHANNEL_ID = "@chan"\n'
        'RSS_URLS = ["https://toml.example/feed"]\n',
        encoding="utf-8",
    )
    config = load_config(path, env={})  # env must not be consulted
    assert config.bot_token == "toml-token"


def test_load_config_falls_back_to_env_when_file_missing(tmp_path: Path) -> None:
    config = load_config(tmp_path / "does-not-exist.toml", env=VALID_ENV)
    assert config.time_interval_min == 20


def test_load_config_falls_back_to_env_when_file_invalid(tmp_path: Path) -> None:
    path = tmp_path / "configuration.toml"
    path.write_text("this is not [valid toml", encoding="utf-8")
    config = load_config(path, env=VALID_ENV)
    assert config.channel_id == "@SwissITNews"


def test_load_config_raises_when_both_sources_invalid(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Missing required environment"):
        load_config(tmp_path / "missing.toml", env={"TIME_INTERVAL_MIN": "20"})


def test_config_is_immutable() -> None:
    import dataclasses

    config = parse_env_config(VALID_ENV)
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.time_interval_min = 99  # type: ignore[misc]

"""Configuration loading and validation.

Parsing functions are pure (they take data, return ``Config`` or raise
``ConfigError``). ``load_config`` is the single thin IO wrapper around them.
"""

from __future__ import annotations

import logging
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

LOGGER = logging.getLogger(__name__)

DEFAULT_CONFIG_FILE = "configuration.toml"
REQUIRED_ENV_KEYS = (
    "TIME_INTERVAL_MIN",
    "ENTRY_MAX_TIME_OLD",
    "BOT_TOKEN",
    "CHANNEL_ID",
    "RSS_URLS",
)
ALLOWED_SCHEMES = ("http", "https")


class ConfigError(ValueError):
    """Raised when the configuration is incomplete or invalid."""


@dataclass(frozen=True)
class Config:
    """Immutable, validated application configuration."""

    time_interval_min: int
    entry_max_time_old: int
    bot_token: str
    channel_id: str
    rss_urls: tuple[str, ...]


def split_urls(raw: str) -> tuple[str, ...]:
    """Split a raw ``RSS_URLS`` value on whitespace and/or commas.

    Surrounding quotes are stripped; empty tokens are dropped.
    """
    tokens = raw.replace(",", " ").split()
    urls = (token.strip().strip("\"'").strip() for token in tokens)
    return tuple(url for url in urls if url)


def validate_url(url: str) -> str:
    """Return the URL unchanged when it is a well-formed http(s) URL."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
        raise ConfigError(f"Invalid RSS URL: {url!r} (must be an absolute http/https URL)")
    return url


def to_positive_int(value: Any, name: str) -> int:
    """Coerce ``value`` to a strictly positive integer or raise ``ConfigError``."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer, got {value!r}") from exc
    if number <= 0:
        raise ConfigError(f"{name} must be greater than 0, got {number}")
    return number


def _require_text(value: Any, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ConfigError(f"{name} must be a non-empty string")
    return text


def _require_urls(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_urls: list[str] = list(split_urls(value))
    else:
        raw_urls = [str(item) for item in value]
    urls = tuple(validate_url(url) for url in raw_urls)
    if not urls:
        raise ConfigError("RSS_URLS must contain at least one URL")
    return urls


def parse_env_config(env: Mapping[str, str]) -> Config:
    """Build a ``Config`` from an environment mapping (pure function)."""
    missing = [key for key in REQUIRED_ENV_KEYS if not env.get(key)]
    if missing:
        raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")
    return Config(
        time_interval_min=to_positive_int(env["TIME_INTERVAL_MIN"], "TIME_INTERVAL_MIN"),
        entry_max_time_old=to_positive_int(env["ENTRY_MAX_TIME_OLD"], "ENTRY_MAX_TIME_OLD"),
        bot_token=_require_text(env["BOT_TOKEN"], "BOT_TOKEN"),
        channel_id=_require_text(env["CHANNEL_ID"], "CHANNEL_ID"),
        rss_urls=_require_urls(env["RSS_URLS"]),
    )


def parse_toml_config(data: Mapping[str, Any]) -> Config:
    """Build a ``Config`` from already-decoded TOML data (pure function)."""
    try:
        return Config(
            time_interval_min=to_positive_int(data["TIME_INTERVAL_MIN"], "TIME_INTERVAL_MIN"),
            entry_max_time_old=to_positive_int(data["ENTRY_MAX_TIME_OLD"], "ENTRY_MAX_TIME_OLD"),
            bot_token=_require_text(data["BOT_TOKEN"], "BOT_TOKEN"),
            channel_id=_require_text(data["CHANNEL_ID"], "CHANNEL_ID"),
            rss_urls=_require_urls(data["RSS_URLS"]),
        )
    except KeyError as exc:
        raise ConfigError(f"Missing configuration key: {exc.args[0]!r}") from exc


def load_config(
    config_file: str | Path = DEFAULT_CONFIG_FILE,
    env: Mapping[str, str] | None = None,
) -> Config:
    """Load configuration: TOML file first, environment variables as fallback.

    ``env`` may be injected for testing; when omitted, ``os.environ`` is used
    after ``.env`` file loading (development convenience only).
    """
    path = Path(config_file)
    try:
        with path.open("rb") as handle:
            config = parse_toml_config(tomllib.load(handle))
        LOGGER.info("Configuration loaded from %s.", path)
        return config
    except FileNotFoundError:
        LOGGER.info("No configuration file at %s; using environment variables.", path)
    except (tomllib.TOMLDecodeError, ConfigError) as exc:
        LOGGER.error("Invalid configuration file %s (%s); using environment variables.", path, exc)

    if env is not None:
        return parse_env_config(env)

    from dotenv import load_dotenv

    load_dotenv()
    return parse_env_config(os.environ)

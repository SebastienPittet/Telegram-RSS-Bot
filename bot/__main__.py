"""Allow ``python -m bot`` as the canonical entry point."""

from bot.telegram_rss_bot import main

if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_id: int | None
    db_path: str
    check_interval_seconds: int
    http_timeout_seconds: int
    http_retries: int
    webapp_url: str
    webapp_host: str
    webapp_port: int
    news_rss_urls: tuple[str, ...]


settings = Settings(
    bot_token=os.getenv("BOT_TOKEN", ""),
    admin_id=_get_int("ADMIN_ID", 0) or None,
    db_path=os.getenv("DB_PATH", "roblox_notifications.sqlite3"),
    check_interval_seconds=_get_int("CHECK_INTERVAL_SECONDS", 60),
    http_timeout_seconds=_get_int("HTTP_TIMEOUT_SECONDS", 5),
    http_retries=_get_int("HTTP_RETRIES", 1),
    webapp_url=os.getenv("WEBAPP_URL", "").rstrip("/"),
    webapp_host=os.getenv("WEBAPP_HOST", "0.0.0.0"),
    webapp_port=_get_int("WEBAPP_PORT", 8080),
    news_rss_urls=tuple(
        url.strip()
        for url in os.getenv("NEWS_RSS_URLS", "https://blog.roblox.com/feed/").split(",")
        if url.strip()
    ),
)

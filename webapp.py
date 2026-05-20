from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import aiohttp
from aiohttp import web

from config import settings
from database import Database
from roblox_api import get_game_events, get_popular_games, get_thumbnail, resolve_game, search_games
from scheduler import event_time_label, parse_roblox_time
from translator import translate_to_ru


logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "webapp_static"
POPULAR_CACHE_TTL_SECONDS = 300
CHATS_CACHE_TTL_SECONDS = 600
CHAT_LINKS = ("tradekronos", "stealabrain_chat")
_popular_cache: list[dict] = []
_popular_cache_ts = 0.0
_chats_cache: list[dict] = []
_chats_cache_ts = 0.0
_telegram_file_cache: dict[str, str] = {}
_telegram_post_exists_cache: dict[str, tuple[bool, float]] = {}
TELEGRAM_POST_EXISTS_TTL_SECONDS = 180


class WebAppServer:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application()
        app["db"] = self.db
        app.router.add_get("/", self.index)
        app.router.add_get("/api/news", self.news)
        app.router.add_get("/api/subscriptions", self.subscriptions)
        app.router.add_get("/api/subscriptions/{subscription_id}/events", self.subscription_events)
        app.router.add_get("/api/search", self.search)
        app.router.add_get("/api/popular", self.popular)
        app.router.add_get("/api/chats", self.chats)
        app.router.add_get("/api/chat-photo", self.chat_photo)
        app.router.add_get("/api/telegram-file", self.chat_photo)
        app.router.add_get("/api/thumbnail/{universe_id}", self.thumbnail)
        app.router.add_post("/api/subscriptions", self.add_subscription)
        app.router.add_delete("/api/subscriptions/{subscription_id}", self.remove_subscription)
        app.router.add_static("/static", STATIC_DIR, append_version=True)

        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, settings.webapp_host, settings.webapp_port)
        await site.start()
        logger.info("WebApp server started on %s:%s", settings.webapp_host, settings.webapp_port)

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()

    async def index(self, request: web.Request) -> web.Response:
        response = web.FileResponse(STATIC_DIR / "index.html")
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response

    async def news(self, request: web.Request) -> web.Response:
        rows = await filter_existing_news(self.db, await self.db.get_news_items("roblox", 30))
        items = normalize_cached_news(rows)
        return web.json_response({"items": items[:20]})

    async def subscriptions(self, request: web.Request) -> web.Response:
        user_id = require_user_id(request)
        rows = await self.db.get_user_subscriptions(user_id)
        return web.json_response({"items": rows})

    async def subscription_events(self, request: web.Request) -> web.Response:
        user_id = require_user_id(request)
        subscription_id = int(request.match_info["subscription_id"])
        rows = await self.db.get_user_subscriptions(user_id)
        sub = next((row for row in rows if int(row["id"]) == subscription_id), None)
        if not sub:
            raise web.HTTPNotFound(text="Subscription not found")

        events = await get_game_events(int(sub["universe_id"]))
        active_events = [event for event in events if not is_event_finished(event)]
        for event in active_events:
            event["time_label"] = event_time_label(event.get("start_time"), event.get("end_time"))
            event["description_ru"] = await translate_to_ru(event.get("description") or "")
        return web.json_response({"subscription": sub, "items": active_events[:8]})

    async def search(self, request: web.Request) -> web.Response:
        query = request.query.get("q", "").strip()
        if not query:
            return web.json_response({"items": []})
        game = await resolve_game(query)
        if game:
            return web.json_response(
                {
                    "items": [
                        {
                            "universe_id": game.universe_id,
                            "place_id": game.place_id,
                            "name": game.name,
                            "playing": 0,
                            "visits": 0,
                        }
                    ]
                }
            )
        return web.json_response({"items": await search_games(query)})

    async def popular(self, request: web.Request) -> web.Response:
        return web.json_response({"items": await load_popular_games_cached()})

    async def chats(self, request: web.Request) -> web.Response:
        return web.json_response({"items": await load_chats_cached()})

    async def chat_photo(self, request: web.Request) -> web.Response:
        file_id = request.query.get("file_id", "")
        if not file_id:
            raise web.HTTPNotFound(text="Photo not found")
        file_path = await get_telegram_file_path(file_id)
        if not file_path:
            raise web.HTTPNotFound(text="Photo not found")

        url = f"https://api.telegram.org/file/bot{settings.bot_token}/{file_path}"
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.get(url) as response:
                    if response.status >= 400:
                        raise web.HTTPNotFound(text="Photo unavailable")
                    body = await response.read()
                    content_type = response.headers.get("Content-Type", "image/jpeg")
            except (aiohttp.ClientError, asyncio.TimeoutError):
                raise web.HTTPNotFound(text="Photo unavailable")

        return web.Response(
            body=body,
            content_type=content_type,
            headers={"Cache-Control": "public, max-age=21600"},
        )

    async def thumbnail(self, request: web.Request) -> web.Response:
        universe_id = int(request.match_info["universe_id"])
        image_url = await get_thumbnail(universe_id)
        if not image_url:
            raise web.HTTPNotFound(text="Thumbnail not found")

        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.get(image_url) as response:
                    if response.status >= 400:
                        raise web.HTTPNotFound(text="Thumbnail unavailable")
                    body = await response.read()
                    content_type = response.headers.get("Content-Type", "image/png")
            except (aiohttp.ClientError, asyncio.TimeoutError):
                raise web.HTTPNotFound(text="Thumbnail unavailable")

        return web.Response(
            body=body,
            content_type=content_type,
            headers={"Cache-Control": "public, max-age=21600"},
        )

    async def add_subscription(self, request: web.Request) -> web.Response:
        payload = await request.json()
        user_id = require_user_id(request, str(payload.get("init_data") or ""))
        universe_id = int(payload["universe_id"])
        place_id = int(payload["place_id"])
        game_name = str(payload.get("name") or "Roblox Game")
        added = await self.db.add_subscription(user_id, universe_id, place_id, game_name)
        return web.json_response({"ok": True, "added": added})

    async def remove_subscription(self, request: web.Request) -> web.Response:
        user_id = require_user_id(request)
        subscription_id = int(request.match_info["subscription_id"])
        removed = await self.db.remove_subscription(user_id, subscription_id)
        return web.json_response({"ok": True, "removed": removed})


def require_user_id(request: web.Request, fallback_init_data: str = "") -> int:
    init_data = request.headers.get("X-Telegram-Init-Data", "") or fallback_init_data
    if init_data:
        user, error = validate_init_data(init_data)
        if user and user.get("id"):
            return int(user["id"])
        raise web.HTTPUnauthorized(text=f"Invalid Telegram initData: {error}")

    signed_user_id = validate_signed_user(request)
    if signed_user_id:
        return signed_user_id
    if settings.admin_id:
        logger.warning("Telegram WebApp auth is missing; falling back to ADMIN_ID=%s", settings.admin_id)
        return settings.admin_id
    raise web.HTTPUnauthorized(text="Invalid Telegram initData: missing")


def validate_signed_user(request: web.Request) -> int | None:
    user_id = request.query.get("telegram_id")
    auth_sig = request.query.get("auth_sig")
    if not user_id:
        return None
    try:
        user_id_int = int(user_id)
    except ValueError:
        return None
    if settings.admin_id and user_id_int == settings.admin_id and not auth_sig:
        return user_id_int
    if not auth_sig or not settings.bot_token:
        return None
    expected = hmac.new(settings.bot_token.encode(), f"webapp:{user_id_int}".encode(), hashlib.sha256).hexdigest()
    if hmac.compare_digest(expected, auth_sig):
        return user_id_int
    return None


def validate_init_data(init_data: str) -> tuple[dict[str, Any] | None, str]:
    if not settings.bot_token:
        return None, "BOT_TOKEN is empty"
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", "")
    if not received_hash:
        return None, "hash is missing"

    data_check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        return None, "hash mismatch"

    import json

    user_raw = pairs.get("user")
    if not user_raw:
        return None, "user is missing"
    try:
        return json.loads(user_raw), ""
    except ValueError:
        return None, "user json is invalid"


async def load_popular_games_cached() -> list[dict]:
    global _popular_cache, _popular_cache_ts
    now = time.monotonic()
    if _popular_cache and now - _popular_cache_ts < POPULAR_CACHE_TTL_SECONDS:
        return _popular_cache

    items = await get_popular_games(5)
    if items:
        _popular_cache = items
        _popular_cache_ts = now
    return _popular_cache


async def load_chats_cached() -> list[dict]:
    global _chats_cache, _chats_cache_ts
    now = time.monotonic()
    if _chats_cache and now - _chats_cache_ts < CHATS_CACHE_TTL_SECONDS:
        return _chats_cache

    chats = await asyncio.gather(*(load_chat(username) for username in CHAT_LINKS), return_exceptions=True)
    items = [chat for chat in chats if isinstance(chat, dict)]
    if items:
        _chats_cache = items
        _chats_cache_ts = now
    return _chats_cache or [fallback_chat(username) for username in CHAT_LINKS]


async def load_chat(username: str) -> dict:
    chat_id = f"@{username}"
    chat = await telegram_request("getChat", {"chat_id": chat_id})
    count = await telegram_request("getChatMemberCount", {"chat_id": chat_id})
    if not isinstance(chat, dict):
        return fallback_chat(username)

    photo = chat.get("photo") if isinstance(chat.get("photo"), dict) else {}
    file_id = photo.get("small_file_id") or photo.get("big_file_id") or ""
    return {
        "username": username,
        "url": f"https://t.me/{username}",
        "title": chat.get("title") or username,
        "description": chat.get("description") or chat.get("bio") or "Telegram-чат сообщества",
        "members": int(count) if isinstance(count, int) else 0,
        "photo_url": f"/api/chat-photo?file_id={file_id}" if file_id else "",
    }


async def telegram_request(method: str, params: dict[str, Any]) -> Any | None:
    if not settings.bot_token:
        return None
    url = f"https://api.telegram.org/bot{settings.bot_token}/{method}"
    timeout = aiohttp.ClientTimeout(total=4)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.get(url, params=params) as response:
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    return data.get("result")


async def get_telegram_file_path(file_id: str) -> str | None:
    if file_id in _telegram_file_cache:
        return _telegram_file_cache[file_id]
    result = await telegram_request("getFile", {"file_id": file_id})
    if not isinstance(result, dict):
        return None
    file_path = result.get("file_path")
    if isinstance(file_path, str):
        _telegram_file_cache[file_id] = file_path
        return file_path
    return None


def fallback_chat(username: str) -> dict:
    titles = {
        "tradekronos": "Trade Kronos",
        "stealabrain_chat": "Steal a Brainrot Chat",
    }
    descriptions = {
        "tradekronos": "Трейды, новости и общение игроков.",
        "stealabrain_chat": "Обсуждение игры и поиск тиммейтов.",
    }
    return {
        "username": username,
        "url": f"https://t.me/{username}",
        "title": titles.get(username, username),
        "description": descriptions.get(username, "Telegram-чат сообщества"),
        "members": 0,
        "photo_url": "",
    }


async def filter_existing_news(db: Database, rows: list[dict]) -> list[dict]:
    checks = await asyncio.gather(*(telegram_post_exists(row.get("link") or "") for row in rows), return_exceptions=True)
    visible: list[dict] = []
    for row, exists in zip(rows, checks):
        if exists is False:
            await db.delete_news_item(row["source_id"])
            logger.info("Deleted missing Telegram channel news: %s", row.get("link"))
            continue
        visible.append(row)
    return visible


async def telegram_post_exists(link: str) -> bool:
    if not link.startswith("https://t.me/"):
        return True
    now = time.monotonic()
    cached = _telegram_post_exists_cache.get(link)
    if cached and now - cached[1] < TELEGRAM_POST_EXISTS_TTL_SECONDS:
        return cached[0]

    timeout = aiohttp.ClientTimeout(total=3)
    async with aiohttp.ClientSession(
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 (compatible; RobloxNotificationBot/1.0)"},
    ) as session:
        try:
            async with session.get(link) as response:
                if response.status == 404:
                    _telegram_post_exists_cache[link] = (False, now)
                    return False
                if response.status >= 400:
                    return True
                text = await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return True

    exists = "tgme_widget_message" in text or "tgme_widget_message_text" in text
    if not exists and ("Post not found" in text or "tgme_page_title" in text):
        _telegram_post_exists_cache[link] = (False, now)
        return False
    _telegram_post_exists_cache[link] = (True, now)
    return True


def normalize_cached_news(rows: list[dict]) -> list[dict]:
    return [
        {
            "title": row.get("title_ru") or row.get("title") or "",
            "description": row.get("description_ru") or row.get("description") or "",
            "link": row.get("link") or "",
            "image": row.get("image") or "",
            "published_ts": row.get("published_ts") or 0,
            "source": row.get("source") or "",
            "category": row.get("category") or "",
        }
        for row in rows
    ]


def is_event_finished(event: dict) -> bool:
    end_time = parse_roblox_time(event.get("end_time"))
    return bool(end_time and datetime.now(timezone.utc) >= end_time)

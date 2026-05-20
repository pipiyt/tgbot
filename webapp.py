from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl
from xml.etree import ElementTree

import aiohttp
from aiohttp import web

from config import settings
from database import Database
from roblox_api import get_game_events, get_thumbnail, resolve_game, search_games
from scheduler import event_time_label, parse_roblox_time
from translator import translate_to_ru


logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "webapp_static"


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
        items = await load_news()
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

    debug_user_id = request.query.get("telegram_id")
    if debug_user_id and settings.admin_id and int(debug_user_id) == settings.admin_id:
        return int(debug_user_id)
    raise web.HTTPUnauthorized(text="Invalid Telegram initData: missing")


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


async def load_news() -> list[dict]:
    timeout = aiohttp.ClientTimeout(total=4)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [fetch_rss(session, url) for url in settings.news_rss_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    items: list[dict] = []
    for result in results:
        if isinstance(result, list):
            items.extend(result)
    items.sort(key=lambda item: item.get("published_ts", 0), reverse=True)
    return items


async def fetch_rss(session: aiohttp.ClientSession, url: str) -> list[dict]:
    try:
        async with session.get(url) as response:
            if response.status >= 400:
                logger.warning("News RSS returned HTTP %s: %s", response.status, url)
                return []
            text = await response.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning("News RSS failed: %s - %r", url, exc)
        return []
    return parse_rss(text, url)


def parse_rss(text: str, source_url: str) -> list[dict]:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return []

    items = []
    for item in root.findall(".//item")[:20]:
        title = get_child_text(item, "title")
        link = get_child_text(item, "link")
        description = strip_html(get_child_text(item, "description"))
        published = get_child_text(item, "pubDate")
        published_ts = parse_news_time(published)
        image = find_rss_image(item)
        if title:
            items.append(
                {
                    "title": title,
                    "description": description,
                    "link": link,
                    "published": published,
                    "published_ts": published_ts,
                    "image": image,
                    "source": source_url,
                }
            )
    return items


def get_child_text(item: ElementTree.Element, tag: str) -> str:
    child = item.find(tag)
    return child.text.strip() if child is not None and child.text else ""


def find_rss_image(item: ElementTree.Element) -> str:
    for child in item.iter():
        url = child.attrib.get("url") or child.attrib.get("href")
        if url and any(part in child.tag.lower() for part in ("thumbnail", "content", "enclosure")):
            return url
    return ""


def strip_html(value: str) -> str:
    import re

    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


def parse_news_time(value: str) -> float:
    if not value:
        return 0
    from email.utils import parsedate_to_datetime

    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return 0


def is_event_finished(event: dict) -> bool:
    end_time = parse_roblox_time(event.get("end_time"))
    return bool(end_time and datetime.now(timezone.utc) >= end_time)

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp

from config import settings


logger = logging.getLogger(__name__)

# Change Roblox URLs here if Roblox changes an endpoint.
PLACE_DETAILS_URL = "https://games.roblox.com/v1/games/multiget-place-details"
UNIVERSE_DETAILS_URL = "https://games.roblox.com/v1/games"
THUMBNAIL_URL = "https://thumbnails.roblox.com/v1/games/icons"

# Experience Events web endpoints are not guaranteed to be stable/public.
# Replace this template with a working endpoint if Roblox changes it.
EXPERIENCE_EVENTS_URL = "https://apis.roblox.com/experience-events-api/v1/universes/{universe_id}/events"

ROBLOX_GAME_RE = re.compile(r"roblox\.com/games/(?P<place_id>\d+)", re.IGNORECASE)


@dataclass(slots=True)
class RobloxGame:
    place_id: int
    universe_id: int
    name: str


class RobloxApi:
    def __init__(self) -> None:
        timeout = aiohttp.ClientTimeout(total=settings.http_timeout_seconds)
        self.session = aiohttp.ClientSession(timeout=timeout)

    async def close(self) -> None:
        await self.session.close()

    async def _request_json(self, url: str, **kwargs: Any) -> Any | None:
        last_error: Exception | None = None
        for attempt in range(1, settings.http_retries + 1):
            try:
                async with self.session.get(url, **kwargs) as response:
                    if response.status == 404:
                        logger.warning("Roblox endpoint returned 404: %s", url)
                        return None
                    response.raise_for_status()
                    return await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc
                logger.warning(
                    "Roblox request failed (%s/%s): %s - %s",
                    attempt,
                    settings.http_retries,
                    url,
                    exc,
                )
                await asyncio.sleep(min(attempt, 3))
        logger.error("Roblox request exhausted retries: %s - %s", url, last_error)
        return None

    async def resolve_game(self, input_text: str) -> RobloxGame | None:
        value = input_text.strip()
        place_id = self._extract_place_id(value)

        if place_id is not None:
            by_place = await self._get_place_details(place_id)
            if by_place:
                return by_place

        if value.isdigit():
            universe_id = int(value)
            info = await self.get_game_info(universe_id)
            if info:
                root_place_id = int(info.get("rootPlaceId") or info.get("place_id") or 0)
                name = str(info.get("name") or "Roblox Game")
                if root_place_id:
                    return RobloxGame(place_id=root_place_id, universe_id=universe_id, name=name)

        return None

    def _extract_place_id(self, value: str) -> int | None:
        match = ROBLOX_GAME_RE.search(value)
        if match:
            return int(match.group("place_id"))
        if value.isdigit():
            return int(value)
        return None

    async def _get_place_details(self, place_id: int) -> RobloxGame | None:
        data = await self._request_json(
            PLACE_DETAILS_URL,
            params={"placeIds": str(place_id)},
        )
        if not isinstance(data, list) or not data:
            return None
        item = data[0]
        universe_id = item.get("universeId")
        name = item.get("name") or item.get("sourceName") or "Roblox Game"
        if not universe_id:
            return None
        return RobloxGame(place_id=place_id, universe_id=int(universe_id), name=str(name))

    async def get_game_info(self, universe_id: int) -> dict | None:
        data = await self._request_json(UNIVERSE_DETAILS_URL, params={"universeIds": str(universe_id)})
        if not isinstance(data, dict):
            return None
        items = data.get("data") or []
        if not items:
            return None
        item = items[0]
        return {
            "universe_id": int(item.get("id") or universe_id),
            "place_id": int(item.get("rootPlaceId") or 0),
            "name": item.get("name") or "Roblox Game",
            "playing": int(item.get("playing") or 0),
            "visits": int(item.get("visits") or 0),
            "description": item.get("description") or "",
            "creator": item.get("creator") or {},
        }

    async def get_thumbnail(self, universe_id: int) -> str | None:
        data = await self._request_json(
            THUMBNAIL_URL,
            params={
                "universeIds": str(universe_id),
                "size": "512x512",
                "format": "Png",
                "isCircular": "false",
            },
        )
        if not isinstance(data, dict):
            return None
        items = data.get("data") or []
        if not items:
            return None
        return items[0].get("imageUrl")

    async def get_game_events(self, universe_id: int) -> list[dict]:
        url = EXPERIENCE_EVENTS_URL.format(universe_id=universe_id)
        data = await self._request_json(url)
        if data is None:
            logger.warning("Events endpoint is unavailable for universe_id=%s", universe_id)
            return []

        raw_events = self._extract_events_list(data)
        events: list[dict] = []
        for item in raw_events:
            normalized = self._normalize_event(universe_id, item)
            if normalized:
                events.append(normalized)
        return events

    def _extract_events_list(self, data: Any) -> list[dict]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("data", "events", "experienceEvents"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    def _normalize_event(self, universe_id: int, item: dict) -> dict | None:
        event_id = item.get("id") or item.get("eventId") or item.get("event_id")
        title = item.get("title") or item.get("name") or item.get("eventTitle")
        if not event_id or not title:
            return None

        start_time = (
            item.get("startTime")
            or item.get("startsAt")
            or item.get("start_time")
            or item.get("eventStartTime")
        )
        end_time = item.get("endTime") or item.get("endsAt") or item.get("end_time")
        image_url = item.get("imageUrl") or item.get("thumbnailUrl") or item.get("iconUrl")

        return {
            "event_id": str(event_id),
            "universe_id": universe_id,
            "title": str(title),
            "description": item.get("description") or "",
            "start_time": self._normalize_time(start_time),
            "end_time": self._normalize_time(end_time),
            "image_url": image_url,
        }

    def _normalize_time(self, value: Any) -> str | None:
        if not value:
            return None
        if isinstance(value, (int, float)):
            return datetime.utcfromtimestamp(value).replace(microsecond=0).isoformat() + "Z"
        if isinstance(value, str):
            return value
        return None


_api: RobloxApi | None = None


async def get_api() -> RobloxApi:
    global _api
    if _api is None:
        _api = RobloxApi()
    return _api


async def close_api() -> None:
    global _api
    if _api is not None:
        await _api.close()
        _api = None


async def resolve_game(input_text: str) -> RobloxGame | None:
    return await (await get_api()).resolve_game(input_text)


async def get_game_info(universe_id: int) -> dict | None:
    return await (await get_api()).get_game_info(universe_id)


async def get_game_events(universe_id: int) -> list[dict]:
    return await (await get_api()).get_game_events(universe_id)


async def get_thumbnail(universe_id: int) -> str | None:
    return await (await get_api()).get_thumbnail(universe_id)

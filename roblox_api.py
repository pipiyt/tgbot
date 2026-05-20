from __future__ import annotations

import asyncio
import logging
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp

from config import settings


logger = logging.getLogger(__name__)

# Change Roblox URLs here if Roblox changes an endpoint.
PLACE_DETAILS_URL = "https://games.roblox.com/v1/games/multiget-place-details"
PLACE_UNIVERSE_URL = "https://apis.roblox.com/universes/v1/places/{place_id}/universe"
UNIVERSE_DETAILS_URL = "https://games.roblox.com/v1/games"
THUMBNAIL_URL = "https://thumbnails.roblox.com/v1/games/icons"
OMNI_SEARCH_URL = "https://apis.roblox.com/search-api/omni-search"

# Experience Events web endpoints are not guaranteed to be stable/public.
# Replace this template with a working endpoint if Roblox changes it.
EXPERIENCE_EVENTS_URL = "https://apis.roblox.com/experience-events-api/v1/universes/{universe_id}/events"

ROBLOX_GAME_RE = re.compile(r"roblox\.com/games/(?P<place_id>\d+)", re.IGNORECASE)
ROBLOX_GAME_SLUG_RE = re.compile(r"roblox\.com/games/\d+/(?P<slug>[^/?#]+)", re.IGNORECASE)


@dataclass(slots=True)
class RobloxGame:
    place_id: int
    universe_id: int
    name: str


class RobloxApi:
    def __init__(self) -> None:
        timeout = aiohttp.ClientTimeout(total=settings.http_timeout_seconds)
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        self.session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers={
                "Accept": "application/json, text/plain, */*",
                "User-Agent": "RobloxNotificationBot/1.0",
            },
        )

    async def close(self) -> None:
        await self.session.close()

    async def _request_json(self, url: str, retries: int | None = None, **kwargs: Any) -> Any | None:
        last_error: Exception | None = None
        max_retries = retries if retries is not None else settings.http_retries
        for attempt in range(1, max_retries + 1):
            try:
                async with self.session.get(url, **kwargs) as response:
                    if response.status == 404:
                        logger.warning("Roblox endpoint returned 404: %s", url)
                        return None
                    if response.status >= 400:
                        body = await response.text()
                        logger.warning(
                            "Roblox endpoint returned %s: %s - %s",
                            response.status,
                            url,
                            body[:300],
                        )
                        return None
                    response.raise_for_status()
                    return await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc
                logger.warning(
                    "Roblox request failed (%s/%s): %s - %s: %r",
                    attempt,
                    max_retries,
                    url,
                    type(exc).__name__,
                    exc,
                )
                if attempt < max_retries:
                    await asyncio.sleep(min(attempt, 3))
        logger.error("Roblox request exhausted retries: %s - %s", url, last_error)
        return await asyncio.to_thread(self._request_json_urllib, url, kwargs.get("params"))

    def _request_json_urllib(self, url: str, params: dict | None = None) -> Any | None:
        if params:
            query = urllib.parse.urlencode(params)
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{query}"

        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json, text/plain, */*",
                "User-Agent": "RobloxNotificationBot/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=settings.http_timeout_seconds) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                raw = response.read().decode(charset, errors="replace")
                if not raw:
                    return None
                return json.loads(raw)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            logger.warning("Roblox urllib fallback failed: %s - %s: %r", url, type(exc).__name__, exc)
            return None

    async def debug_request(self, title: str, url: str, params: dict | None = None) -> str:
        try:
            async with self.session.get(url, params=params) as response:
                body = await response.text()
                if response.status >= 400:
                    return f"{title}: HTTP {response.status} {body[:120]}"
                try:
                    data = json.loads(body)
                except ValueError:
                    return f"{title}: non-json {response.status} {body[:120]}"
                return self._format_debug_result(title, data)
        except Exception as exc:
            fallback = await asyncio.to_thread(self._debug_request_urllib, title, url, params)
            if fallback:
                return fallback
            return f"{title}: {type(exc).__name__} {exc!r}"

    def _debug_request_urllib(self, title: str, url: str, params: dict | None = None) -> str | None:
        if params:
            query = urllib.parse.urlencode(params)
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{query}"

        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json, text/plain, */*",
                "User-Agent": "RobloxNotificationBot/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=settings.http_timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
                try:
                    data = json.loads(raw)
                except ValueError:
                    return f"{title}: urllib non-json {response.status} {raw[:120]}"
                return f"{self._format_debug_result(title, data)} via urllib"
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return f"{title}: urllib HTTP {exc.code} {body[:120]}"
        except Exception as exc:
            return f"{title}: urllib {type(exc).__name__} {exc!r}"

    def _format_debug_result(self, title: str, data: Any) -> str:
        if isinstance(data, list):
            return f"{title}: OK list[{len(data)}]"
        if isinstance(data, dict):
            return f"{title}: OK dict keys={', '.join(list(data.keys())[:5])}"
        return f"{title}: OK {type(data).__name__}"

    async def resolve_game(self, input_text: str) -> RobloxGame | None:
        value = input_text.strip()
        place_id = self._extract_place_id(value)

        if place_id is not None:
            by_universe_lookup = await self._get_game_from_place_universe(place_id)
            if by_universe_lookup:
                slug_name = self._extract_slug_name(value)
                if slug_name:
                    by_universe_lookup.name = slug_name
                return by_universe_lookup
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

    async def search_games(self, query: str, limit: int = 8) -> list[dict]:
        normalized_query = self._normalize_search_query(query)
        if not normalized_query:
            return []

        results = await self._search_games_omni(normalized_query, limit)
        if results:
            return results[:limit]

        return []

    def _normalize_search_query(self, query: str) -> str:
        slug_match = ROBLOX_GAME_SLUG_RE.search(query)
        if slug_match:
            query = slug_match.group("slug").replace("-", " ")
        query = query.replace("_", " ")
        return query.strip()

    def _extract_place_id(self, value: str) -> int | None:
        match = ROBLOX_GAME_RE.search(value)
        if match:
            return int(match.group("place_id"))
        if value.isdigit():
            return int(value)
        return None

    def _extract_slug_name(self, value: str) -> str | None:
        match = ROBLOX_GAME_SLUG_RE.search(value)
        if not match:
            return None
        words = [word for word in match.group("slug").replace("_", "-").split("-") if word]
        if not words:
            return None
        return " ".join(words)

    async def _get_place_details(self, place_id: int) -> RobloxGame | None:
        data = await self._request_json(
            PLACE_DETAILS_URL,
            retries=1,
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

    async def _get_game_from_place_universe(self, place_id: int) -> RobloxGame | None:
        data = await self._request_json(PLACE_UNIVERSE_URL.format(place_id=place_id))
        if not isinstance(data, dict):
            return None

        universe_id = data.get("universeId") or data.get("universe_id")
        if not universe_id:
            return None

        return RobloxGame(place_id=place_id, universe_id=int(universe_id), name="Roblox Game")

    async def _search_games_omni(self, query: str, limit: int) -> list[dict]:
        data = await self._request_json(
            OMNI_SEARCH_URL,
            retries=1,
            params={
                "searchQuery": query,
                "pageType": "all",
                "sessionId": "roblox-notification-bot",
            },
        )
        if not isinstance(data, dict):
            return []

        raw_items: list[dict] = []
        for key in ("searchResults", "contents", "data"):
            value = data.get(key)
            if isinstance(value, list):
                raw_items.extend(item for item in value if isinstance(item, dict))

        results: list[dict] = []
        for item in raw_items:
            candidates = item.get("contents") if isinstance(item.get("contents"), list) else [item]
            for candidate in candidates:
                if isinstance(candidate, dict):
                    normalized = self._normalize_search_item(candidate)
                    if normalized:
                        results.append(normalized)
                if len(results) >= limit:
                    return results
        return results

    def _normalize_search_item(self, item: dict) -> dict | None:
        for nested_key in ("content", "item", "metadata", "experience"):
            nested = item.get(nested_key)
            if isinstance(nested, dict):
                normalized = self._normalize_search_item({**item, **nested})
                if normalized:
                    return normalized

        root_place = item.get("rootPlace")
        if isinstance(root_place, dict):
            item = {**item, "rootPlaceId": root_place.get("id")}

        universe_id = (
            item.get("universeId")
            or item.get("universe_id")
            or item.get("universeID")
            or item.get("UniverseId")
            or item.get("universe")
        )
        place_id = (
            item.get("placeId")
            or item.get("rootPlaceId")
            or item.get("place_id")
            or item.get("PlaceID")
            or item.get("PlaceId")
            or item.get("rootPlace")
        )
        if not universe_id and item.get("id") and place_id:
            universe_id = item.get("id")
        name = item.get("name") or item.get("Name") or item.get("title") or item.get("displayName")
        if not universe_id or not name:
            return None

        try:
            universe_id_int = int(universe_id)
        except (TypeError, ValueError):
            return None

        place_id_int = 0
        if place_id:
            try:
                place_id_int = int(place_id)
            except (TypeError, ValueError):
                place_id_int = 0

        playing = item.get("playing") or item.get("playerCount") or item.get("onlineCount") or 0
        visits = item.get("visits") or item.get("totalVisits") or 0

        if not place_id_int:
            # Search APIs sometimes return only universeId. Fill rootPlaceId from game details.
            return {
                "universe_id": universe_id_int,
                "place_id": 0,
                "name": str(name),
                "playing": int(playing or 0),
                "visits": int(visits or 0),
            }

        return {
            "universe_id": universe_id_int,
            "place_id": place_id_int,
            "name": str(name),
            "playing": int(playing or 0),
            "visits": int(visits or 0),
        }

    async def get_game_info(self, universe_id: int) -> dict | None:
        data = await self._request_json(
            UNIVERSE_DETAILS_URL,
            retries=1,
            params={"universeIds": str(universe_id)},
        )
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


async def search_games(query: str, limit: int = 8) -> list[dict]:
    return await (await get_api()).search_games(query, limit)

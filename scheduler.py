from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from aiogram import Bot

from database import Database
from roblox_api import get_game_events


logger = logging.getLogger(__name__)
MOSCOW_TZ = timezone(timedelta(hours=3))


def parse_roblox_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        cleaned = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        logger.warning("Could not parse event time: %s", value)
        return None


def format_time(value: str | None) -> str:
    parsed = parse_roblox_time(value)
    if not parsed:
        return "не указано"
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def format_time(value: str | None) -> str:
    parsed = parse_roblox_time(value)
    if not parsed:
        return "не указано"
    return parsed.astimezone(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M МСК")


def event_time_label(start_value: str | None, end_value: str | None = None) -> str:
    start = parse_roblox_time(start_value)
    end = parse_roblox_time(end_value)
    now = datetime.now(timezone.utc)
    if start and start <= now and (end is None or now < end):
        return "Уже идет"
    if end and now >= end:
        return "Завершено"
    return format_time(start_value)


def build_event_text(game_name: str, place_id: int, event: dict, prefix: str = "🔔 Roblox Event") -> str:
    return (
        f"{prefix}\n"
        f"🎮 Игра: {game_name}\n"
        f"🎁 Событие: {event['title']}\n"
        f"🕒 Старт: {format_time(event.get('start_time'))}\n"
        f"▶️ Играть: https://www.roblox.com/games/{place_id}"
    )


class EventScheduler:
    def __init__(self, bot: Bot, db: Database, interval_seconds: int) -> None:
        self.bot = bot
        self.db = db
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="roblox-event-scheduler")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.check_once()
            except Exception:
                logger.exception("Scheduler iteration failed")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def check_once(self) -> None:
        subscriptions = await self.db.get_all_subscriptions()
        grouped: dict[int, list[dict]] = defaultdict(list)
        for sub in subscriptions:
            grouped[int(sub["universe_id"])].append(sub)

        for universe_id, subscribers in grouped.items():
            playing = 0
            events = await get_game_events(universe_id)
            if not events:
                continue
            for event in events:
                is_new = await self.db.upsert_event(event)
                if is_new:
                    await self._notify_subscribers(subscribers, event, playing, "🔔 Roblox Event")
                    await self.db.mark_event_notified(event["event_id"], "notified_new")
                await self._send_time_notifications(subscribers, event, playing)

    async def _send_time_notifications(self, subscribers: list[dict], event: dict, playing: int) -> None:
        start = parse_roblox_time(event.get("start_time"))
        if not start:
            return

        now = datetime.now(timezone.utc)
        seconds_to_start = (start - now).total_seconds()
        saved_events = await self.db.get_upcoming_events(limit=500)
        saved = next((item for item in saved_events if item["event_id"] == event["event_id"]), None)
        if not saved:
            return

        if 0 <= seconds_to_start <= 600 and not saved["notified_10min"]:
            await self._notify_subscribers(subscribers, event, playing, "⏰ Через 10 минут начнется Roblox Event")
            await self.db.mark_event_notified(event["event_id"], "notified_10min")

        if -60 <= seconds_to_start <= 0 and not saved["notified_started"]:
            await self._notify_subscribers(subscribers, event, playing, "🚀 Roblox Event начался")
            await self.db.mark_event_notified(event["event_id"], "notified_started")

    async def _notify_subscribers(self, subscribers: list[dict], event: dict, playing: int, prefix: str) -> None:
        for sub in subscribers:
            text = build_event_text(
                game_name=sub["game_name"],
                place_id=int(sub["place_id"]),
                event=event,
                prefix=prefix,
            )
            try:
                await self.bot.send_message(chat_id=sub["telegram_id"], text=text)
            except Exception:
                logger.exception("Could not notify telegram_id=%s", sub["telegram_id"])

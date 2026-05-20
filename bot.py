from __future__ import annotations

import asyncio
import hashlib
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message

from config import settings
from database import Database
from keyboards import main_menu, search_results_keyboard, subscriptions_events_keyboard, subscriptions_keyboard
from roblox_api import (
    OMNI_SEARCH_URL,
    PLACE_UNIVERSE_URL,
    close_api,
    get_api,
    get_game_events,
    resolve_game,
    search_games,
)
from scheduler import EventScheduler, event_time_label, format_time, parse_roblox_time
from translator import translate_to_ru
from webapp import WebAppServer
from datetime import datetime, timezone


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

router = Router()
db = Database(settings.db_path)
scheduler: EventScheduler | None = None
webapp_server: WebAppServer | None = None
search_cache: dict[int, dict[int, dict]] = {}


class AddGame(StatesGroup):
    waiting_for_game = State()


@router.channel_post()
async def save_channel_news(message: Message) -> None:
    if not is_news_channel(message):
        logger.info(
            "Ignored channel post: chat_id=%s username=%s allowed=%s",
            message.chat.id,
            message.chat.username,
            settings.news_telegram_channels,
        )
        return

    text = (message.text or message.caption or "").strip()
    if not text:
        logger.info("Ignored channel post without text: chat_id=%s message_id=%s", message.chat.id, message.message_id)
        return

    link = channel_post_link(message)
    title, description = split_news_text(text)
    image = channel_post_image(message)
    source_id = hashlib.sha256(f"telegram:{message.chat.id}:{message.message_id}".encode()).hexdigest()
    title_ru = await translate_to_ru(title)
    description_ru = await translate_to_ru(description)
    await db.upsert_news_item(
        {
            "source_id": source_id,
            "category": "roblox",
            "title": title,
            "title_ru": title_ru,
            "description": description,
            "description_ru": description_ru,
            "link": link,
            "image": image,
            "published_ts": message.date.timestamp() if message.date else 0,
            "source": f"telegram:{message.chat.username or message.chat.id}",
        }
    )
    logger.info("Saved Telegram channel news: chat=%s message_id=%s", message.chat.id, message.message_id)


def is_news_channel(message: Message) -> bool:
    if message.chat.type != "channel":
        return False
    allowed = settings.news_telegram_channels
    if not allowed:
        return True
    username = (message.chat.username or "").lower()
    return username in allowed or str(message.chat.id) in allowed


def channel_post_link(message: Message) -> str:
    if message.chat.username:
        return f"https://t.me/{message.chat.username}/{message.message_id}"
    return ""


def split_news_text(text: str) -> tuple[str, str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "Roblox news", ""
    title = lines[0][:120]
    description = " ".join(lines[1:]).strip() or lines[0]
    return title, description


def channel_post_image(message: Message) -> str:
    if message.photo:
        return f"/api/telegram-file?file_id={message.photo[-1].file_id}"
    return ""


@router.message(CommandStart())
async def start(message: Message) -> None:
    await db.add_user(message.from_user.id, message.from_user.username if message.from_user else None)
    if settings.webapp_url:
        text = (
            "Привет! Я Roblox Notification бот.\n\n"
            "Все функции теперь в приложении: новости, подписки, события и настройки."
        )
    else:
        text = (
            "Привет! Приложение пока недоступно.\n\n"
            "Администратор еще не настроил WEBAPP_URL и HTTPS."
        )
    await message.answer(
        text,
        reply_markup=main_menu(message.from_user.id if message.from_user else None),
    )


@router.message(F.text == "Приложение временно недоступно")
async def webapp_unavailable(message: Message) -> None:
    await message.answer("Mini App заработает после настройки HTTPS-домена.")


@router.message(F.text == "➕ Добавить игру")
async def add_game_button(message: Message, state: FSMContext) -> None:
    await state.set_state(AddGame.waiting_for_game)
    await message.answer("Отправьте ссылку Roblox игры, placeId или universeId.")


@router.message(AddGame.waiting_for_game)
async def add_game_value(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Нужна ссылка, placeId или universeId текстом.")
        return

    if message.text == "📋 Мои подписки":
        await state.clear()
        await subscriptions(message)
        return
    if message.text == "🔥 Ближайшие события":
        await state.clear()
        await events(message)
        return
    if message.text == "⚙️ Настройки":
        await state.clear()
        await settings_button(message)
        return
    if message.text == "➕ Добавить игру":
        await message.answer("Отправьте ссылку Roblox игры, placeId или universeId.")
        return

    status_message = await message.answer("Ищу игру в Roblox...")

    game = await resolve_game(message.text)
    if not game:
        results = await search_games(message.text)
        if results:
            search_cache[message.from_user.id] = {int(item["universe_id"]): item for item in results}
            await status_message.edit_text(
                "Я нашел несколько игр. Выберите нужную:",
                reply_markup=search_results_keyboard(results),
            )
            return
        await status_message.edit_text(
            "Не удалось найти игру. Проверьте ссылку или ID.\n"
            "Можно отправить ссылку, placeId, universeId или название игры."
        )
        return

    await add_subscription_message(
        message=message,
        universe_id=game.universe_id,
        place_id=game.place_id,
        fallback_name=game.name,
    )
    await state.clear()


async def add_subscription_message(
    message: Message,
    universe_id: int,
    place_id: int,
    fallback_name: str = "Roblox Game",
    playing: int = 0,
    visits: int = 0,
) -> None:
    game_name = fallback_name
    final_place_id = int(place_id or 0)
    if not final_place_id:
        await message.answer("Нашел игру, но Roblox не вернул placeId. Попробуйте ссылку на игру.")
        return

    added = await db.add_subscription(message.chat.id, universe_id, final_place_id, game_name)
    status = "Подписка добавлена." if added else "Эта игра уже есть в ваших подписках."
    text = (
        f"{status}\n\n"
        f"🎮 {game_name}\n"
        f"Universe ID: {universe_id}\n"
        f"Place ID: {final_place_id}\n"
        f"👥 Онлайн: {playing}\n"
        f"👁 Visits: {visits}"
    )
    await message.answer(text, reply_markup=main_menu(message.from_user.id if message.from_user else None))


@router.callback_query(F.data.startswith("pickgame:"))
async def pick_game_callback(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 2:
        await callback.answer("Не удалось выбрать игру")
        return

    await state.clear()
    universe_id = int(parts[1])
    cached = search_cache.get(callback.from_user.id, {}).get(universe_id)
    if not cached:
        await callback.answer("Поиск устарел")
        await callback.message.answer("Нажмите ➕ Добавить игру и найдите игру заново.")
        return

    place_id = int(cached.get("place_id") or 0)
    await callback.answer("Добавляю игру")
    await add_subscription_message(
        message=callback.message,
        universe_id=universe_id,
        place_id=place_id,
        fallback_name=cached.get("name") or "Roblox Game",
        playing=int(cached.get("playing") or 0),
        visits=int(cached.get("visits") or 0),
    )


@router.message(Command("subscriptions"))
@router.message(F.text == "📋 Мои подписки")
async def subscriptions(message: Message) -> None:
    items = await db.get_user_subscriptions(message.from_user.id)
    if not items:
        await message.answer("У вас пока нет подписок. Нажмите ➕ Добавить игру.")
        return
    await message.answer(
        "Выберите игру, чтобы посмотреть события:",
        reply_markup=subscriptions_events_keyboard(items),
    )


@router.callback_query(F.data.startswith("showevents:"))
async def show_events_callback(callback: CallbackQuery) -> None:
    subscription_id = int(callback.data.split(":", 1)[1])
    items = await db.get_user_subscriptions(callback.from_user.id)
    item = next((sub for sub in items if int(sub["id"]) == subscription_id), None)
    if not item:
        await callback.answer("Подписка не найдена")
        return

    await callback.answer("Проверяю события")
    await callback.message.answer(f"Проверяю события: {item['game_name']}...")
    await send_subscription_events(callback.message, item)


async def send_subscription_events(message: Message, item: dict) -> None:
    events_list = await get_game_events(int(item["universe_id"]))
    active_events = [event for event in events_list if not is_event_finished(event)]
    if not active_events:
        await message.answer(
            f"🎮 {item['game_name']}\n"
            "Активных или будущих событий сейчас нет."
        )
        return

    for event in active_events[:5]:
        await db.upsert_event(event)
        description = await translate_to_ru(event.get("description") or "нет описания")
        text = (
            f"🔔 Roblox Event\n"
            f"🎮 Игра: {item['game_name']}\n"
            f"🎁 Событие: {event['title']}\n"
            f"📝 Описание: {description or 'нет описания'}\n"
            f"🕒 {event_time_label(event.get('start_time'), event.get('end_time'))}"
        )
        image_url = event.get("image_url")
        if image_url:
            try:
                await message.answer_photo(image_url, caption=telegram_caption(text))
            except Exception:
                logger.warning("Could not send event image: %s", image_url)
                await message.answer(text)
        else:
            await message.answer(text)


def is_event_finished(event: dict) -> bool:
    end_time = parse_roblox_time(event.get("end_time"))
    return bool(end_time and datetime.now(timezone.utc) >= end_time)


def telegram_caption(text: str) -> str:
    if len(text) <= 1024:
        return text
    return text[:1021] + "..."


@router.message(Command("remove"))
async def remove_command(message: Message) -> None:
    items = await db.get_user_subscriptions(message.from_user.id)
    if not items:
        await message.answer("Удалять нечего: подписок пока нет.")
        return
    await message.answer("Выберите подписку для удаления:", reply_markup=subscriptions_keyboard(items))


@router.callback_query(F.data.startswith("remove:"))
async def remove_callback(callback: CallbackQuery) -> None:
    subscription_id = int(callback.data.split(":", 1)[1])
    removed = await db.remove_subscription(callback.from_user.id, subscription_id)
    await callback.answer("Удалено" if removed else "Подписка не найдена")
    await callback.message.answer("Готово." if removed else "Не удалось удалить подписку.")


@router.message(Command("events"))
@router.message(F.text == "🔥 Ближайшие события")
async def events(message: Message) -> None:
    items = await db.get_upcoming_events()
    if not items:
        await message.answer(
            "Пока нет сохраненных событий. Бот добавит их после успешной проверки Roblox events endpoint."
        )
        return
    lines = ["Ближайшие события:"]
    for item in items:
        lines.append(
            f"\n🎮 {item.get('game_name') or item['universe_id']}\n"
            f"🎁 {item['title']}\n"
            f"🕒 {format_time(item.get('start_time'))}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("admin"))
async def admin(message: Message) -> None:
    if settings.admin_id is None or message.from_user.id != settings.admin_id:
        await message.answer("Команда доступна только администратору.")
        return
    stats = await db.get_stats()
    await message.answer(
        "Статистика:\n"
        f"Пользователи: {stats['users']}\n"
        f"Подписки: {stats['subscriptions']}\n"
        f"События: {stats['events']}"
    )


@router.message(Command("debug_roblox"))
async def debug_roblox(message: Message) -> None:
    if settings.admin_id is None or message.from_user.id != settings.admin_id:
        await message.answer("Команда доступна только администратору.")
        return

    api = await get_api()
    checks = [
        (
            "place universe",
            PLACE_UNIVERSE_URL.format(place_id=90148635862803),
            {},
        ),
        (
            "omni search",
            OMNI_SEARCH_URL,
            {
                "searchQuery": "Adopt Me",
                "pageType": "all",
                "sessionId": "roblox-notification-bot",
            },
        ),
    ]
    lines = ["Roblox debug:"]
    for title, url, params in checks:
        lines.append(await api.debug_request(title, url, params))
    await message.answer("\n".join(lines))


@router.message(Command("news_debug"))
async def news_debug(message: Message) -> None:
    if settings.admin_id and message.from_user and message.from_user.id != settings.admin_id:
        return
    rows = await db.get_news_items("roblox", 5)
    lines = [
        "News debug:",
        f"NEWS_TELEGRAM_CHANNELS={', '.join(settings.news_telegram_channels) or '(all channels)'}",
        f"Saved Roblox news: {len(rows)} shown / latest 5",
    ]
    for row in rows:
        lines.append(f"- {row.get('title_ru') or row.get('title')} | {row.get('source')}")
    await message.answer("\n".join(lines))


@router.message(Command("news_delete"))
async def news_delete(message: Message) -> None:
    if settings.admin_id and message.from_user and message.from_user.id != settings.admin_id:
        return
    if not message.text:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /news_delete <id поста канала или ссылка>")
        return
    value = parts[1].strip()
    rows = await db.get_news_items("roblox", 100)
    removed = 0
    for row in rows:
        link = row.get("link") or ""
        if link.endswith(f"/{value}") or link == value:
            await db.delete_news_item(row["source_id"])
            removed += 1
    await message.answer(f"Удалено новостей: {removed}")


@router.message(F.text == "⚙️ Настройки")
async def settings_button(message: Message) -> None:
    await message.answer(
        f"Интервал проверки: {settings.check_interval_seconds} сек.\n"
        "Настройки меняются через .env и перезапуск бота."
    )


async def main() -> None:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is empty. Create .env from .env.example")

    await db.connect()
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    global scheduler
    scheduler = EventScheduler(bot, db, settings.check_interval_seconds)
    scheduler.start()

    global webapp_server
    if settings.webapp_url:
        webapp_server = WebAppServer(db)
        await webapp_server.start()

    try:
        logger.info("Bot started")
        await dp.start_polling(bot)
    finally:
        if webapp_server:
            await webapp_server.stop()
        if scheduler:
            await scheduler.stop()
        await close_api()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

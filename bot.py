from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message

from config import settings
from database import Database
from keyboards import main_menu, search_results_keyboard, subscriptions_keyboard
from roblox_api import (
    OMNI_SEARCH_URL,
    PLACE_UNIVERSE_URL,
    close_api,
    get_api,
    get_game_events,
    resolve_game,
    search_games,
)
from scheduler import EventScheduler, event_time_label, format_time


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

router = Router()
db = Database(settings.db_path)
scheduler: EventScheduler | None = None
search_cache: dict[int, dict[int, dict]] = {}


class AddGame(StatesGroup):
    waiting_for_game = State()


@router.message(CommandStart())
async def start(message: Message) -> None:
    await db.add_user(message.from_user.id, message.from_user.username if message.from_user else None)
    await message.answer(
        "Привет! Я Roblox Notification бот.\n\n"
        "Добавь Roblox игру по ссылке, placeId или universeId, а я буду следить за Experience Events.",
        reply_markup=main_menu(),
    )


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
    await message.answer(text, reply_markup=main_menu())


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
    await send_subscription_events(message, items)


async def send_subscription_events(message: Message, subscriptions_items: list[dict]) -> None:
    await message.answer("Проверяю события по вашим подпискам...")
    for item in subscriptions_items:
        events_list = await get_game_events(int(item["universe_id"]))
        if not events_list:
            await message.answer(
                f"🎮 {item['game_name']}\n"
                "События: Roblox events endpoint сейчас не вернул данные."
            )
            continue

        for event in events_list[:3]:
            await db.upsert_event(event)
            text = (
                f"🔔 Roblox Event\n"
                f"🎮 Игра: {item['game_name']}\n"
                f"🎁 Событие: {event['title']}\n"
                f"📝 Описание: {event.get('description') or 'нет описания'}\n"
                f"🕒 {event_time_label(event.get('start_time'), event.get('end_time'))}"
            )
            image_url = event.get("image_url")
            if image_url:
                await message.answer_photo(image_url, caption=telegram_caption(text))
            else:
                await message.answer(text)


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
    test_events = await get_game_events(7326934954)
    lines.append(f"events test: {len(test_events)} events")
    await message.answer("\n".join(lines))


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

    try:
        logger.info("Bot started")
        await dp.start_polling(bot)
    finally:
        if scheduler:
            await scheduler.stop()
        await close_api()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

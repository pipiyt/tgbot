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
from roblox_api import close_api, get_game_info, get_thumbnail, resolve_game, search_games
from scheduler import EventScheduler, format_time


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

router = Router()
db = Database(settings.db_path)
scheduler: EventScheduler | None = None


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

    game = await resolve_game(message.text)
    if not game:
        results = await search_games(message.text)
        if results:
            await message.answer(
                "Я нашел несколько игр. Выберите нужную:",
                reply_markup=search_results_keyboard(results),
            )
            return
        await message.answer(
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
) -> None:
    info = await get_game_info(universe_id) or {}
    thumbnail = await get_thumbnail(universe_id)
    game_name = info.get("name") or fallback_name
    final_place_id = int(info.get("place_id") or place_id)
    if not final_place_id:
        await message.answer("Нашел игру, но Roblox не вернул placeId. Попробуйте ссылку на игру.")
        return

    added = await db.add_subscription(message.from_user.id, universe_id, final_place_id, game_name)
    status = "Подписка добавлена." if added else "Эта игра уже есть в ваших подписках."
    text = (
        f"{status}\n\n"
        f"🎮 {game_name}\n"
        f"Universe ID: {universe_id}\n"
        f"Place ID: {final_place_id}\n"
        f"👥 Онлайн: {info.get('playing', 0)}\n"
        f"👁 Visits: {info.get('visits', 0)}"
    )
    if thumbnail:
        await message.answer_photo(thumbnail, caption=text, reply_markup=main_menu())
    else:
        await message.answer(text, reply_markup=main_menu())


@router.callback_query(F.data.startswith("pickgame:"))
async def pick_game_callback(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Не удалось выбрать игру")
        return

    await state.clear()
    universe_id = int(parts[1])
    place_id = int(parts[2])
    await callback.answer("Добавляю игру")
    await add_subscription_message(
        message=callback.message,
        universe_id=universe_id,
        place_id=place_id,
    )


@router.message(Command("subscriptions"))
@router.message(F.text == "📋 Мои подписки")
async def subscriptions(message: Message) -> None:
    items = await db.get_user_subscriptions(message.from_user.id)
    if not items:
        await message.answer("У вас пока нет подписок. Нажмите ➕ Добавить игру.")
        return
    lines = ["Ваши подписки:"]
    for item in items:
        lines.append(
            f"\n#{item['id']} 🎮 {item['game_name']}\n"
            f"Universe ID: {item['universe_id']}\n"
            f"▶️ https://www.roblox.com/games/{item['place_id']}"
        )
    await message.answer("\n".join(lines), reply_markup=subscriptions_keyboard(items))


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

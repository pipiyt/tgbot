from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить игру"), KeyboardButton(text="📋 Мои подписки")],
            [KeyboardButton(text="🔥 Ближайшие события"), KeyboardButton(text="⚙️ Настройки")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def subscriptions_keyboard(subscriptions: list[dict]) -> InlineKeyboardMarkup | None:
    if not subscriptions:
        return None
    rows = [
        [
            InlineKeyboardButton(
                text=f"Удалить: {item['game_name'][:32]}",
                callback_data=f"remove:{item['id']}",
            )
        ]
        for item in subscriptions
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def search_results_keyboard(results: list[dict]) -> InlineKeyboardMarkup | None:
    if not results:
        return None
    rows = []
    for item in results[:8]:
        title = item["name"][:34]
        playing = item.get("playing", 0)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{title} | онлайн {playing}",
                    callback_data=f"pickgame:{item['universe_id']}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)

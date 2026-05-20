from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

from config import settings


def main_menu(user_id: int | None = None) -> ReplyKeyboardMarkup:
    if settings.webapp_url:
        rows = [[KeyboardButton(text="Открыть приложение", web_app=WebAppInfo(url=webapp_url(user_id)))]]
    else:
        rows = [[KeyboardButton(text="Приложение временно недоступно")]]
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


def webapp_url(user_id: int | None = None) -> str:
    parts = urlsplit(settings.webapp_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["tg_v"] = str(int(time.time()))
    if user_id and settings.bot_token:
        query["telegram_id"] = str(user_id)
        query["auth_sig"] = sign_webapp_user(user_id)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def sign_webapp_user(user_id: int) -> str:
    return hmac.new(settings.bot_token.encode(), f"webapp:{user_id}".encode(), hashlib.sha256).hexdigest()


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


def subscriptions_events_keyboard(subscriptions: list[dict]) -> InlineKeyboardMarkup | None:
    if not subscriptions:
        return None
    rows = []
    for item in subscriptions:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🔔 {item['game_name'][:34]}",
                    callback_data=f"showevents:{item['id']}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Удалить подписку: {item['game_name'][:26]}",
                    callback_data=f"remove:{item['id']}",
                )
            ]
        )
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

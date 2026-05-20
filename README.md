# Roblox Notification Telegram Bot

Telegram-бот на Python 3.11+, который принимает Roblox game URL, `placeId` или `universeId`, сохраняет подписки в SQLite и проверяет Roblox Experience Events в фоне.

Roblox Experience Events поддерживают механику `Notify Me`: игроки могут подписаться на событие и получить уведомление при старте. Веб-эндпоинты для чтения этих событий могут быть неофициальными или измениться, поэтому URL вынесены в `roblox_api.py`.

## Возможности

- `/start` с кнопками меню.
- Добавление игры по ссылке Roblox, `placeId` или `universeId`.
- Получение названия, онлайна, visits и thumbnail.
- SQLite-хранилище пользователей, подписок и событий.
- Фоновая проверка событий каждые 60 секунд.
- Уведомления о новом событии, напоминание за 10 минут и уведомление о старте.
- `/subscriptions`, `/remove`, `/events`, `/admin`.

## 1. Создать Telegram bot token через BotFather

1. Откройте Telegram и найдите `@BotFather`.
2. Отправьте команду `/newbot`.
3. Введите имя бота, например `Roblox Notification`.
4. Введите username, который заканчивается на `bot`, например `my_roblox_notification_bot`.
5. BotFather выдаст токен. Он выглядит примерно так: `123456:ABC-DEF...`.

## 2. Создать `.env`

Скопируйте `.env.example` в `.env` и заполните значения:

```env
BOT_TOKEN=ваш_telegram_bot_token
ADMIN_ID=ваш_telegram_id
DB_PATH=roblox_notifications.sqlite3
CHECK_INTERVAL_SECONDS=60
HTTP_TIMEOUT_SECONDS=5
HTTP_RETRIES=1
WEBAPP_URL=https://your-domain.example
WEBAPP_HOST=0.0.0.0
WEBAPP_PORT=8080
NEWS_RSS_URLS=https://blog.roblox.com/feed/
```

Узнать свой Telegram ID можно через ботов вроде `@userinfobot`.

## 3. Установить зависимости

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

На macOS/Linux активация окружения будет такой:

```bash
source .venv/bin/activate
```

## 4. Запустить

```bash
python bot.py
```

После запуска откройте своего бота в Telegram, нажмите `/start` и добавьте Roblox игру.

## Telegram WebApp

Если задать `WEBAPP_URL`, бот добавит кнопку `Открыть меню`. Она открывает WebApp с разделами:

- Новости Roblox
- Уведомления в играх
- Добавление игры
- Настройки

WebApp сервер запускается вместе с ботом на `WEBAPP_HOST:WEBAPP_PORT`. Для Telegram нужен публичный HTTPS URL. Обычно на VPS ставят Nginx и проксируют домен на локальный порт `8080`.

Пример `.env`:

```env
WEBAPP_URL=https://bot.example.com
WEBAPP_HOST=127.0.0.1
WEBAPP_PORT=8080
```

Новости берутся из `NEWS_RSS_URLS`. Можно указать несколько RSS через запятую. Для Twitter/X без платного API используйте внешний RSS-мост или свой endpoint, например RSSHub/Nitter, если он доступен на вашем сервере.

## 5. Где менять Roblox Events endpoint

Все URL Roblox API находятся в `roblox_api.py`:

```python
PLACE_DETAILS_URL = "https://games.roblox.com/v1/games/multiget-place-details"
UNIVERSE_DETAILS_URL = "https://games.roblox.com/v1/games"
THUMBNAIL_URL = "https://thumbnails.roblox.com/v1/games/icons"
PLACE_UNIVERSE_URL = "https://apis.roblox.com/universes/v1/places/{place_id}/universe"
OMNI_SEARCH_URL = "https://apis.roblox.com/search-api/omni-search"
EXPERIENCE_EVENTS_URL = "https://apis.roblox.com/virtual-events/v1/universes/{universe_id}/virtual-events"
```

Если текущий `EXPERIENCE_EVENTS_URL` перестал отвечать или Roblox изменил формат, замените только эту константу. Бот сам добавляет query-параметр `fromUtc`. Код нормализует несколько распространенных вариантов ответа: `data`, `events`, `virtualEvents`, `experienceEvents`, а также поля `id/eventId/virtualEventId`, `title/name`, `startTime/startsAt/startUtc`.

Если поиск игры по названию перестал работать, проверьте `OMNI_SEARCH_URL`. Бот использует один быстрый поиск без лишних fallback-запросов, чтобы Telegram-ответ был быстрее.

Бот не использует Roblox cookie, не обходит защиту Roblox и не требует платных сервисов. Если endpoint событий недоступен, бот не падает: ошибка пишется в лог, а следующая проверка выполнится по расписанию.

## Перевод описаний

Описание событий переводится на русский через `translator.py`. URL переводчика вынесен в константу `TRANSLATE_URL`. Если endpoint перевода не отвечает, бот отправит оригинальный текст и продолжит работу.

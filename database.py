from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    universe_id INTEGER NOT NULL,
    place_id INTEGER NOT NULL,
    game_name TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(telegram_id, universe_id)
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    universe_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    start_time TEXT,
    end_time TEXT,
    image_url TEXT,
    notified_new INTEGER DEFAULT 0,
    notified_10min INTEGER DEFAULT 0,
    notified_started INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_universe_id ON subscriptions(universe_id);
CREATE INDEX IF NOT EXISTS idx_events_universe_id ON events(universe_id);
CREATE INDEX IF NOT EXISTS idx_events_start_time ON events(start_time);

CREATE TABLE IF NOT EXISTS news_items (
    source_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    title_ru TEXT,
    description TEXT,
    description_ru TEXT,
    link TEXT,
    image TEXT,
    published_ts REAL DEFAULT 0,
    source TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_news_items_category ON news_items(category);
CREATE INDEX IF NOT EXISTS idx_news_items_published_ts ON news_items(published_ts);

CREATE TABLE IF NOT EXISTS webapp_activity (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webapp_visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    visited_at TEXT NOT NULL,
    visit_date TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_webapp_activity_last_seen ON webapp_activity(last_seen);
CREATE INDEX IF NOT EXISTS idx_webapp_visits_date ON webapp_visits(visit_date);
"""


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    def _db(self) -> aiosqlite.Connection:
        if self.conn is None:
            raise RuntimeError("Database is not connected")
        return self.conn

    async def add_user(self, telegram_id: int, username: str | None) -> None:
        await self._db().execute(
            """
            INSERT INTO users (telegram_id, username)
            VALUES (?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET username = excluded.username
            """,
            (telegram_id, username),
        )
        await self._db().commit()

    async def add_subscription(
        self,
        telegram_id: int,
        universe_id: int,
        place_id: int,
        game_name: str,
    ) -> bool:
        cursor = await self._db().execute(
            """
            INSERT OR IGNORE INTO subscriptions (telegram_id, universe_id, place_id, game_name)
            VALUES (?, ?, ?, ?)
            """,
            (telegram_id, universe_id, place_id, game_name),
        )
        await self._db().commit()
        return cursor.rowcount > 0

    async def get_user_subscriptions(self, telegram_id: int) -> list[dict]:
        cursor = await self._db().execute(
            "SELECT * FROM subscriptions WHERE telegram_id = ? ORDER BY created_at DESC",
            (telegram_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def remove_subscription(self, telegram_id: int, subscription_id: int) -> bool:
        cursor = await self._db().execute(
            "DELETE FROM subscriptions WHERE telegram_id = ? AND id = ?",
            (telegram_id, subscription_id),
        )
        await self._db().commit()
        return cursor.rowcount > 0

    async def get_all_subscriptions(self) -> list[dict]:
        cursor = await self._db().execute("SELECT * FROM subscriptions ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_subscribers_for_universe(self, universe_id: int) -> list[dict]:
        cursor = await self._db().execute(
            "SELECT * FROM subscriptions WHERE universe_id = ?",
            (universe_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def upsert_event(self, event: dict) -> bool:
        existing = await self._db().execute(
            "SELECT event_id FROM events WHERE event_id = ?",
            (event["event_id"],),
        )
        was_saved = await existing.fetchone()
        cursor = await self._db().execute(
            """
            INSERT INTO events (
                event_id, universe_id, title, description, start_time, end_time, image_url
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                universe_id = excluded.universe_id,
                title = excluded.title,
                description = excluded.description,
                start_time = excluded.start_time,
                end_time = excluded.end_time,
                image_url = excluded.image_url
            """,
            (
                event["event_id"],
                event["universe_id"],
                event["title"],
                event.get("description"),
                event.get("start_time"),
                event.get("end_time"),
                event.get("image_url"),
            ),
        )
        await self._db().commit()
        return cursor.rowcount > 0 and was_saved is None

    async def mark_event_notified(self, event_id: str, field: str) -> None:
        if field not in {"notified_new", "notified_10min", "notified_started"}:
            raise ValueError(f"Unknown notification field: {field}")
        await self._db().execute(f"UPDATE events SET {field} = 1 WHERE event_id = ?", (event_id,))
        await self._db().commit()

    async def get_upcoming_events(self, limit: int = 20) -> list[dict]:
        cursor = await self._db().execute(
            """
            SELECT e.*, s.place_id, s.game_name
            FROM events e
            LEFT JOIN subscriptions s ON s.universe_id = e.universe_id
            WHERE e.start_time IS NOT NULL
            GROUP BY e.event_id
            ORDER BY e.start_time ASC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_stats(self) -> dict:
        result = {}
        for table in ("users", "subscriptions", "events"):
            cursor = await self._db().execute(f"SELECT COUNT(*) AS count FROM {table}")
            row = await cursor.fetchone()
            result[table] = row["count"]
        today = _moscow_today()
        online_cutoff = _utc_now() - timedelta(minutes=5)
        cursor = await self._db().execute(
            "SELECT COUNT(*) AS count FROM users WHERE date(datetime(created_at, '+3 hours')) = ?",
            (today,),
        )
        row = await cursor.fetchone()
        result["users_today"] = row["count"]
        cursor = await self._db().execute(
            "SELECT COUNT(*) AS count FROM webapp_activity WHERE last_seen >= ?",
            (online_cutoff.isoformat(),),
        )
        row = await cursor.fetchone()
        result["online"] = row["count"]
        cursor = await self._db().execute(
            "SELECT COUNT(*) AS count FROM webapp_visits WHERE visit_date = ?",
            (today,),
        )
        row = await cursor.fetchone()
        result["visits_today"] = row["count"]
        cursor = await self._db().execute(
            "SELECT COUNT(DISTINCT telegram_id) AS count FROM webapp_visits WHERE visit_date = ?",
            (today,),
        )
        row = await cursor.fetchone()
        result["visitors_today"] = row["count"]
        return result

    async def record_webapp_activity(self, telegram_id: int, username: str | None = None) -> None:
        now = _utc_now()
        now_value = now.isoformat()
        today = _moscow_today(now)
        cursor = await self._db().execute(
            "SELECT last_seen FROM webapp_activity WHERE telegram_id = ?",
            (telegram_id,),
        )
        row = await cursor.fetchone()
        should_count_visit = True
        if row and row["last_seen"]:
            try:
                last_seen = datetime.fromisoformat(row["last_seen"])
                should_count_visit = now - last_seen > timedelta(minutes=30)
            except ValueError:
                should_count_visit = True
        await self._db().execute(
            """
            INSERT INTO webapp_activity (telegram_id, username, first_seen, last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = COALESCE(excluded.username, webapp_activity.username),
                last_seen = excluded.last_seen
            """,
            (telegram_id, username, now_value, now_value),
        )
        if should_count_visit:
            await self._db().execute(
                """
                INSERT INTO webapp_visits (telegram_id, visited_at, visit_date)
                VALUES (?, ?, ?)
                """,
                (telegram_id, now_value, today),
            )
        await self._db().commit()

    async def upsert_news_item(self, item: dict) -> None:
        await self._db().execute(
            """
            INSERT INTO news_items (
                source_id, category, title, title_ru, description, description_ru,
                link, image, published_ts, source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                category = excluded.category,
                title = excluded.title,
                title_ru = excluded.title_ru,
                description = excluded.description,
                description_ru = excluded.description_ru,
                link = excluded.link,
                image = excluded.image,
                published_ts = excluded.published_ts,
                source = excluded.source,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                item["source_id"],
                item["category"],
                item["title"],
                item.get("title_ru"),
                item.get("description"),
                item.get("description_ru"),
                item.get("link"),
                item.get("image"),
                item.get("published_ts", 0),
                item.get("source"),
            ),
        )
        await self._db().commit()

    async def get_news_items(self, category: str | None = None, limit: int = 30) -> list[dict]:
        if category:
            cursor = await self._db().execute(
                """
                SELECT * FROM news_items
                WHERE category = ?
                ORDER BY published_ts DESC, updated_at DESC
                LIMIT ?
                """,
                (category, limit),
            )
        else:
            cursor = await self._db().execute(
                """
                SELECT * FROM news_items
                ORDER BY published_ts DESC, updated_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def delete_news_item(self, source_id: str) -> None:
        await self._db().execute("DELETE FROM news_items WHERE source_id = ?", (source_id,))
        await self._db().commit()


MOSCOW_TZ = timezone(timedelta(hours=3))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _moscow_today(now: datetime | None = None) -> str:
    return (now or _utc_now()).astimezone(MOSCOW_TZ).date().isoformat()

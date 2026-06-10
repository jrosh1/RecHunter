"""
RecHunter Database Layer
========================

Async SQLite persistence via **aiosqlite**.  Provides full CRUD for watches,
availability history, notification dedup, and event logging.

Usage::

    from recsniper.database import get_db

    async with get_db() as db:
        watches = await db.list_watches()
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import AsyncIterator, Optional

import aiosqlite

from recsniper.config import settings
from recsniper.models import (
    AvailabilitySlot,
    CheckResult,
    EventLog,
    Watch,
    WatchStatus,
)

# ---------------------------------------------------------------------------
# SQL DDL
# ---------------------------------------------------------------------------

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    username        TEXT UNIQUE NOT NULL,
    phone_number    TEXT,
    carrier_gateway TEXT DEFAULT 'telegram',
    callmebot_key   TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS otps (
    user_id         TEXT REFERENCES users(id) ON DELETE CASCADE,
    code            TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    PRIMARY KEY (user_id, code)
);

CREATE TABLE IF NOT EXISTS watches (
    id              TEXT PRIMARY KEY,
    user_id         TEXT REFERENCES users(id),
    name            TEXT NOT NULL,
    facility_id     TEXT NOT NULL,
    reservation_type TEXT NOT NULL,
    date_start      TEXT NOT NULL,
    date_end        TEXT,
    mode            TEXT NOT NULL DEFAULT 'cancellation',
    poll_interval_minutes INTEGER NOT NULL DEFAULT 10,
    drop_time       TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    filters         TEXT NOT NULL DEFAULT '{}',
    last_checked    TEXT,
    next_check      TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS availability_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id        TEXT NOT NULL,
    watch_name      TEXT NOT NULL DEFAULT '',
    facility_id     TEXT NOT NULL,
    facility_name   TEXT NOT NULL DEFAULT '',
    site_id         TEXT NOT NULL DEFAULT '',
    site_name       TEXT NOT NULL DEFAULT '',
    date            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'Available',
    booking_url     TEXT NOT NULL DEFAULT '',
    raw_data        TEXT NOT NULL DEFAULT '{}',
    discovered_at   TEXT NOT NULL,
    FOREIGN KEY (watch_id) REFERENCES watches(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS notification_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id        TEXT NOT NULL,
    message         TEXT NOT NULL,
    sent_at         TEXT NOT NULL,
    success         INTEGER NOT NULL DEFAULT 1,
    error           TEXT,
    FOREIGN KEY (watch_id) REFERENCES watches(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS event_log (
    id              TEXT PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    watch_id        TEXT,
    watch_name      TEXT,
    message         TEXT NOT NULL,
    details         TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_availability_watch ON availability_history(watch_id);
CREATE INDEX IF NOT EXISTS idx_availability_date  ON availability_history(discovered_at);
CREATE INDEX IF NOT EXISTS idx_notification_watch  ON notification_log(watch_id);
CREATE INDEX IF NOT EXISTS idx_event_log_type      ON event_log(event_type);
CREATE INDEX IF NOT EXISTS idx_event_log_ts        ON event_log(timestamp);
"""


# ---------------------------------------------------------------------------
# Database wrapper
# ---------------------------------------------------------------------------

class Database:
    """Async wrapper around an aiosqlite connection.

    Handles table creation, watch CRUD, availability recording, notification
    dedup, and event logging.
    """

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    # -- Initialisation -----------------------------------------------------

    async def init(self) -> None:
        """Create tables and indexes if they don't already exist."""
        await self._conn.executescript(_CREATE_TABLES_SQL)
        await self._conn.commit()

        # Check if user_id column exists on watches (migration)
        cursor = await self._conn.execute("PRAGMA table_info(watches)")
        columns = await cursor.fetchall()
        column_names = [col['name'] for col in columns]
        if 'user_id' not in column_names:
            await self._conn.execute("ALTER TABLE watches ADD COLUMN user_id TEXT REFERENCES users(id)")
            await self._conn.commit()

    # -- Watch CRUD ---------------------------------------------------------

    async def create_watch(self, watch: Watch) -> Watch:
        """Insert a new watch and return it."""
        await self._conn.execute(
            """
            INSERT INTO watches
                (id, user_id, name, facility_id, reservation_type, date_start, date_end,
                 mode, poll_interval_minutes, drop_time, status, filters,
                 last_checked, next_check, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                watch.id,
                watch.user_id,
                watch.name,
                watch.facility_id,
                watch.reservation_type.value,
                watch.date_start.isoformat(),
                watch.date_end.isoformat() if watch.date_end else None,
                watch.mode.value,
                watch.poll_interval_minutes,
                watch.drop_time,
                watch.status.value,
                json.dumps(watch.filters),
                watch.last_checked.isoformat() if watch.last_checked else None,
                watch.next_check.isoformat() if watch.next_check else None,
                watch.created_at.isoformat(),
            ),
        )
        await self._conn.commit()
        return watch

    async def get_watch(self, watch_id: str, user_id: Optional[str] = None) -> Optional[Watch]:
        """Fetch a single watch by ID, optionally filtered by user_id."""
        if user_id:
            cursor = await self._conn.execute(
                "SELECT * FROM watches WHERE id = ? AND user_id = ?", (watch_id, user_id)
            )
        else:
            cursor = await self._conn.execute(
                "SELECT * FROM watches WHERE id = ?", (watch_id,)
            )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_watch(row)

    async def list_watches(
        self, user_id: Optional[str] = None, status: Optional[WatchStatus] = None
    ) -> list[Watch]:
        """Return all watches, optionally filtered by user_id and status."""
        query = "SELECT * FROM watches WHERE 1=1"
        params = []
        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)
        if status is not None:
            query += " AND status = ?"
            params.append(status.value)
        query += " ORDER BY created_at DESC"

        cursor = await self._conn.execute(query, tuple(params))
        rows = await cursor.fetchall()
        return [self._row_to_watch(r) for r in rows]

    async def update_watch(self, watch_id: str, user_id: Optional[str] = None, **fields: object) -> Optional[Watch]:
        """Update specific fields on a watch."""
        if not fields:
            return await self.get_watch(watch_id, user_id=user_id)

        set_clauses: list[str] = []
        values: list[object] = []
        for col, val in fields.items():
            set_clauses.append(f"{col} = ?")
            if isinstance(val, dict):
                values.append(json.dumps(val))
            elif isinstance(val, datetime):
                values.append(val.isoformat())
            elif hasattr(val, "isoformat"):
                values.append(val.isoformat())  # type: ignore[union-attr]
            elif hasattr(val, "value"):
                values.append(val.value)  # Enum
            else:
                values.append(val)
        
        if user_id:
            sql = f"UPDATE watches SET {', '.join(set_clauses)} WHERE id = ? AND user_id = ?"
            values.extend([watch_id, user_id])
        else:
            sql = f"UPDATE watches SET {', '.join(set_clauses)} WHERE id = ?"
            values.append(watch_id)

        await self._conn.execute(sql, tuple(values))
        await self._conn.commit()
        return await self.get_watch(watch_id, user_id=user_id)

    async def delete_watch(self, watch_id: str, user_id: Optional[str] = None) -> bool:
        """Delete a watch by ID, optionally filtered by user_id."""
        if user_id:
            cursor = await self._conn.execute(
                "DELETE FROM watches WHERE id = ? AND user_id = ?", (watch_id, user_id)
            )
        else:
            cursor = await self._conn.execute(
                "DELETE FROM watches WHERE id = ?", (watch_id,)
            )
        await self._conn.commit()
        return cursor.rowcount > 0

    # -- Availability history -----------------------------------------------

    async def add_availability_record(
        self,
        watch_id: str,
        watch_name: str,
        slot: AvailabilitySlot,
    ) -> None:
        """Persist a single discovered availability slot."""
        await self._conn.execute(
            """
            INSERT INTO availability_history
                (watch_id, watch_name, facility_id, facility_name, site_id,
                 site_name, date, status, booking_url, raw_data, discovered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                watch_id,
                watch_name,
                slot.facility_id,
                slot.facility_name,
                slot.site_id,
                slot.site_name,
                slot.date.isoformat(),
                slot.status,
                slot.booking_url,
                json.dumps(slot.raw_data),
                datetime.utcnow().isoformat(),
            ),
        )
        await self._conn.commit()

    async def get_recent_availability(
        self,
        watch_id: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Return recent availability records, newest first."""
        conditions: list[str] = []
        params: list[object] = []

        if watch_id:
            conditions.append("a.watch_id = ?")
            params.append(watch_id)
        if user_id:
            conditions.append("w.user_id = ?")
            params.append(user_id)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        if user_id:
            sql = f"""
                SELECT a.* FROM availability_history a
                JOIN watches w ON a.watch_id = w.id
                {where}
                ORDER BY a.discovered_at DESC LIMIT ?
            """
        else:
            sql = f"SELECT a.* FROM availability_history a {where} ORDER BY a.discovered_at DESC LIMIT ?"

        params.append(limit)

        cursor = await self._conn.execute(sql, tuple(params))
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in rows]

    # -- Event log ----------------------------------------------------------

    async def add_event_log(self, event: EventLog) -> None:
        """Insert an event into the audit log."""
        await self._conn.execute(
            """
            INSERT INTO event_log (id, timestamp, event_type, watch_id, watch_name, message, details)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.timestamp.isoformat(),
                event.event_type,
                event.watch_id,
                event.watch_name,
                event.message,
                json.dumps(event.details),
            ),
        )
        await self._conn.commit()

    async def get_recent_events(
        self,
        event_type: Optional[str] = None,
        watch_id: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Return recent event log entries, newest first."""
        conditions: list[str] = []
        params: list[object] = []

        if event_type:
            conditions.append("e.event_type = ?")
            params.append(event_type)
        if watch_id:
            conditions.append("e.watch_id = ?")
            params.append(watch_id)
        if user_id:
            conditions.append("w.user_id = ?")
            params.append(user_id)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        if user_id:
            sql = f"""
                SELECT e.* FROM event_log e
                JOIN watches w ON e.watch_id = w.id
                {where}
                ORDER BY e.timestamp DESC LIMIT ?
            """
        else:
            sql = f"SELECT e.* FROM event_log e {where} ORDER BY e.timestamp DESC LIMIT ?"

        params.append(limit)

        cursor = await self._conn.execute(sql, tuple(params))
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in rows]

    # -- Notification log & dedup -------------------------------------------

    async def add_notification_log(
        self,
        watch_id: str,
        message: str,
        success: bool = True,
        error: Optional[str] = None,
    ) -> None:
        """Record that a notification was sent (or attempted)."""
        await self._conn.execute(
            """
            INSERT INTO notification_log (watch_id, message, sent_at, success, error)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                watch_id,
                message,
                datetime.utcnow().isoformat(),
                1 if success else 0,
                error,
            ),
        )
        await self._conn.commit()

    async def was_recently_notified(
        self,
        watch_id: str,
        slot_key: Optional[str] = None,
        minutes: int = 30,
    ) -> bool:
        """Check if a successful notification was sent for this watch
        within the last *minutes* minutes (used for dedup)."""
        cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
        if slot_key:
            cursor = await self._conn.execute(
                """
                SELECT COUNT(*) FROM notification_log
                WHERE watch_id = ? AND success = 1 AND sent_at >= ? AND message LIKE ?
                """,
                (watch_id, cutoff, f"%({slot_key})%"),
            )
        else:
            cursor = await self._conn.execute(
                """
                SELECT COUNT(*) FROM notification_log
                WHERE watch_id = ? AND success = 1 AND sent_at >= ?
                """,
                (watch_id, cutoff),
            )
        row = await cursor.fetchone()
        return (row[0] if row else 0) > 0

    # -- Internal helpers ---------------------------------------------------

    def _row_to_watch(self, row: aiosqlite.Row) -> Watch:
        """Convert a raw SQLite row into a :class:`Watch` model."""
        user_id = None
        try:
            user_id = row["user_id"]
        except (IndexError, KeyError):
            pass

        return Watch(
            id=row["id"],
            user_id=user_id,
            name=row["name"],
            facility_id=row["facility_id"],
            reservation_type=row["reservation_type"],
            date_start=row["date_start"],
            date_end=row["date_end"] if row["date_end"] else None,
            mode=row["mode"],
            poll_interval_minutes=row["poll_interval_minutes"],
            drop_time=row["drop_time"],
            status=row["status"],
            filters=json.loads(row["filters"]) if row["filters"] else {},
            last_checked=datetime.fromisoformat(row["last_checked"]) if row["last_checked"] else None,
            next_check=datetime.fromisoformat(row["next_check"]) if row["next_check"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    # -- User & OTP Helpers -------------------------------------------------

    async def create_user(self, user_id: str, username: str, phone_number: str, carrier_gateway: str, callmebot_key: str) -> dict:
        created_at = datetime.utcnow().isoformat()
        await self._conn.execute(
            """
            INSERT INTO users (id, username, phone_number, carrier_gateway, callmebot_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, username, phone_number, carrier_gateway, callmebot_key, created_at),
        )
        await self._conn.commit()
        return {
            "id": user_id,
            "username": username,
            "phone_number": phone_number,
            "carrier_gateway": carrier_gateway,
            "callmebot_key": callmebot_key,
            "created_at": created_at
        }

    async def get_user_by_username(self, username: str) -> Optional[dict]:
        cursor = await self._conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def get_user_by_id(self, user_id: str) -> Optional[dict]:
        cursor = await self._conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def update_user_settings(self, user_id: str, phone_number: str, carrier_gateway: str, callmebot_key: str) -> Optional[dict]:
        await self._conn.execute(
            """
            UPDATE users
            SET phone_number = ?, carrier_gateway = ?, callmebot_key = ?
            WHERE id = ?
            """,
            (phone_number, carrier_gateway, callmebot_key, user_id),
        )
        await self._conn.commit()
        return await self.get_user_by_id(user_id)

    async def create_otp(self, user_id: str, code: str, expires_in_minutes: int = 5) -> None:
        # Clear existing OTPs for the user to keep it simple (one active login flow at a time)
        await self._conn.execute("DELETE FROM otps WHERE user_id = ?", (user_id,))
        
        expires_at = (datetime.utcnow() + timedelta(minutes=expires_in_minutes)).isoformat()
        await self._conn.execute(
            """
            INSERT INTO otps (user_id, code, expires_at)
            VALUES (?, ?, ?)
            """,
            (user_id, code, expires_at),
        )
        await self._conn.commit()

    async def verify_otp(self, user_id: str, code: str) -> bool:
        # Check if code matches and not expired
        now = datetime.utcnow().isoformat()
        cursor = await self._conn.execute(
            """
            SELECT COUNT(*) FROM otps
            WHERE user_id = ? AND code = ? AND expires_at > ?
            """,
            (user_id, code, now),
        )
        row = await cursor.fetchone()
        is_valid = (row[0] if row else 0) > 0
        if is_valid:
            # Delete OTP after successful verification to prevent replay attacks
            await self._conn.execute("DELETE FROM otps WHERE user_id = ? AND code = ?", (user_id, code))
            await self._conn.commit()
        return is_valid

    async def clear_expired_otps(self) -> None:
        now = datetime.utcnow().isoformat()
        await self._conn.execute("DELETE FROM otps WHERE expires_at <= ?", (now,))
        await self._conn.commit()


# ---------------------------------------------------------------------------
# Connection factory / context manager
# ---------------------------------------------------------------------------

@asynccontextmanager
async def get_db(db_path: Optional[str] = None) -> AsyncIterator[Database]:
    """Async context manager that yields an initialised :class:`Database`.

    Usage::

        async with get_db() as db:
            watches = await db.list_watches()

    The underlying aiosqlite connection is closed when the context exits.
    """
    path = db_path or settings.db_path

    # Ensure the parent directory exists
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(path)
    # Enable WAL mode for better concurrent read performance
    await conn.execute("PRAGMA journal_mode=WAL")
    # Enable foreign keys
    await conn.execute("PRAGMA foreign_keys=ON")
    # Return rows as tuples (default), but keep column info via description
    conn.row_factory = aiosqlite.Row  # type: ignore[assignment]

    db = Database(conn)
    await db.init()

    try:
        yield db
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Singleton helper (for long-running server processes)
# ---------------------------------------------------------------------------

_singleton_db: Optional[Database] = None
_singleton_conn: Optional[aiosqlite.Connection] = None


async def get_singleton_db(db_path: Optional[str] = None) -> Database:
    """Return a long-lived :class:`Database` instance (singleton).

    Useful for server processes that want to keep one connection open rather
    than opening/closing on every request.  Call :func:`close_singleton_db`
    during shutdown.
    """
    global _singleton_db, _singleton_conn

    if _singleton_db is not None:
        return _singleton_db

    path = db_path or settings.db_path
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    _singleton_conn = await aiosqlite.connect(path)
    await _singleton_conn.execute("PRAGMA journal_mode=WAL")
    await _singleton_conn.execute("PRAGMA foreign_keys=ON")
    _singleton_conn.row_factory = aiosqlite.Row  # type: ignore[assignment]

    _singleton_db = Database(_singleton_conn)
    await _singleton_db.init()
    return _singleton_db


async def close_singleton_db() -> None:
    """Close the singleton database connection (call during shutdown)."""
    global _singleton_db, _singleton_conn
    if _singleton_conn is not None:
        await _singleton_conn.close()
    _singleton_db = None
    _singleton_conn = None


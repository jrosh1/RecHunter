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
CREATE TABLE IF NOT EXISTS watches (
    id              TEXT PRIMARY KEY,
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

    # -- Watch CRUD ---------------------------------------------------------

    async def create_watch(self, watch: Watch) -> Watch:
        """Insert a new watch and return it."""
        await self._conn.execute(
            """
            INSERT INTO watches
                (id, name, facility_id, reservation_type, date_start, date_end,
                 mode, poll_interval_minutes, drop_time, status, filters,
                 last_checked, next_check, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                watch.id,
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

    async def get_watch(self, watch_id: str) -> Optional[Watch]:
        """Fetch a single watch by ID, or ``None`` if not found."""
        cursor = await self._conn.execute(
            "SELECT * FROM watches WHERE id = ?", (watch_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_watch(row)

    async def list_watches(
        self, status: Optional[WatchStatus] = None
    ) -> list[Watch]:
        """Return all watches, optionally filtered by status."""
        if status is not None:
            cursor = await self._conn.execute(
                "SELECT * FROM watches WHERE status = ? ORDER BY created_at DESC",
                (status.value,),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT * FROM watches ORDER BY created_at DESC"
            )
        rows = await cursor.fetchall()
        return [self._row_to_watch(r) for r in rows]

    async def update_watch(self, watch_id: str, **fields: object) -> Optional[Watch]:
        """Update specific fields on a watch.

        Parameters
        ----------
        watch_id : str
            The watch to update.
        **fields
            Column-name → new-value pairs.  JSON-serialisable dicts are
            automatically stringified.

        Returns
        -------
        Watch | None
            The updated watch, or ``None`` if the ID wasn't found.
        """
        if not fields:
            return await self.get_watch(watch_id)

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
        values.append(watch_id)

        sql = f"UPDATE watches SET {', '.join(set_clauses)} WHERE id = ?"
        await self._conn.execute(sql, tuple(values))
        await self._conn.commit()
        return await self.get_watch(watch_id)

    async def delete_watch(self, watch_id: str) -> bool:
        """Delete a watch by ID.  Returns ``True`` if a row was deleted."""
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
        limit: int = 50,
    ) -> list[dict]:
        """Return recent availability records, newest first.

        Parameters
        ----------
        watch_id : str | None
            If given, filter to this watch only.
        limit : int
            Maximum number of rows to return.
        """
        if watch_id:
            cursor = await self._conn.execute(
                "SELECT * FROM availability_history WHERE watch_id = ? "
                "ORDER BY discovered_at DESC LIMIT ?",
                (watch_id, limit),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT * FROM availability_history "
                "ORDER BY discovered_at DESC LIMIT ?",
                (limit,),
            )
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
        limit: int = 100,
    ) -> list[dict]:
        """Return recent event log entries, newest first."""
        conditions: list[str] = []
        params: list[object] = []

        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        if watch_id:
            conditions.append("watch_id = ?")
            params.append(watch_id)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM event_log {where} ORDER BY timestamp DESC LIMIT ?"
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
        # Column order must match the CREATE TABLE statement
        (
            id_,
            name,
            facility_id,
            reservation_type,
            date_start,
            date_end,
            mode,
            poll_interval_minutes,
            drop_time,
            status,
            filters,
            last_checked,
            next_check,
            created_at,
        ) = row

        return Watch(
            id=id_,
            name=name,
            facility_id=facility_id,
            reservation_type=reservation_type,
            date_start=date_start,
            date_end=date_end if date_end else None,
            mode=mode,
            poll_interval_minutes=poll_interval_minutes,
            drop_time=drop_time,
            status=status,
            filters=json.loads(filters) if filters else {},
            last_checked=datetime.fromisoformat(last_checked) if last_checked else None,
            next_check=datetime.fromisoformat(next_check) if next_check else None,
            created_at=datetime.fromisoformat(created_at),
        )


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

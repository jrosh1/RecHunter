"""
RecHunter – FastAPI Application
================================
Central API server that ties together the database, monitoring engine,
notifier, and the frontend dashboard.

Run via ``python run.py`` or ``uvicorn recsniper.app:app``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Set

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from recsniper.config import settings
from recsniper.database import Database, get_singleton_db, close_singleton_db
from recsniper.models import (
    EventLog,
    NotificationSettings,
    Watch,
    WatchCreate,
    WatchUpdate,
    WatchStatus,
)
from recsniper.monitor import MonitorEngine
from recsniper.notifier import SMSNotifier
from recsniper.utils import create_http_client, search_facilities, setup_logging

logger = logging.getLogger("recsniper.app")

# ---------------------------------------------------------------------------
# Globals populated during lifespan
# ---------------------------------------------------------------------------

_db: Database | None = None
_engine: MonitorEngine | None = None
_notifier: SMSNotifier | None = None

# SSE: connected client queues
_sse_clients: Set[asyncio.Queue] = set()


async def _broadcast_event(event: EventLog) -> None:
    """Push an EventLog to every connected SSE client."""
    ts = event.timestamp if hasattr(event, "timestamp") and event.timestamp else datetime.utcnow()
    ts_str = ts.isoformat() + ("Z" if ts.tzinfo is None else "")
    data = json.dumps(
        {
            "event_type": event.event_type,
            "watch_id": event.watch_id,
            "watch_name": event.watch_name,
            "message": event.message,
            "details": event.details if hasattr(event, "details") and event.details else {},
            "timestamp": ts_str,
        },
        default=str,
    )
    dead: list[asyncio.Queue] = []
    for q in _sse_clients:
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _sse_clients.discard(q)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    global _db, _engine, _notifier

    setup_logging()

    # Database (singleton connection for the server lifetime)
    _db = await get_singleton_db(settings.db_path)

    # Notifier
    _notifier = SMSNotifier(
        gmail_address=settings.gmail_address,
        gmail_app_password=settings.gmail_app_password,
        phone_number=settings.phone_number,
        carrier_gateway=settings.carrier_gateway,
    )

    # Monitor engine
    _engine = MonitorEngine(db=_db, notifier=_notifier, event_callback=_broadcast_event)
    await _engine.start()

    logger.info("RecHunter application started.")
    yield

    # Shutdown
    await _engine.stop()
    await close_singleton_db()
    logger.info("RecHunter application stopped.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="RecHunter",
    description="Recreation.gov availability monitoring agent",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS – allow all origins for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files – frontend dashboard
_frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if _frontend_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_frontend_dir)), name="static")


# ---------------------------------------------------------------------------
# Root – serve frontend index.html
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def root():
    index = _frontend_dir / "index.html"
    if index.is_file():
        return FileResponse(str(index), media_type="text/html")
    return JSONResponse(
        {"message": "RecHunter API is running. Frontend not found – place files in frontend/."},
        status_code=200,
    )


# ---------------------------------------------------------------------------
# Watches CRUD
# ---------------------------------------------------------------------------

@app.get("/api/watches")
async def list_watches():
    """Return all watches."""
    watches = await _db.list_watches()
    return [_watch_to_dict(w) for w in watches]


@app.post("/api/watches", status_code=201)
async def create_watch(payload: WatchCreate):
    """Create a new watch and register it with the monitor engine."""
    watch = Watch(**payload.model_dump())
    created = await _db.create_watch(watch)

    # Register with scheduler
    await _engine.add_watch(created)

    event = EventLog(
        event_type="watch_created",
        watch_id=created.id,
        watch_name=created.name,
        message=f"Watch '{created.name}' created",
    )
    await _db.add_event_log(event)
    await _broadcast_event(event)

    # Trigger an immediate check in the background for instant feedback!
    if created.status == WatchStatus.ACTIVE:
        asyncio.create_task(_engine.run_check_now(created.id))

    return _watch_to_dict(created)


@app.get("/api/watches/{watch_id}")
async def get_watch(watch_id: str):
    """Get a single watch by ID."""
    watch = await _db.get_watch(watch_id)
    if not watch:
        raise HTTPException(status_code=404, detail="Watch not found")
    return _watch_to_dict(watch)


@app.put("/api/watches/{watch_id}")
async def update_watch(watch_id: str, payload: WatchUpdate):
    """Update a watch and reschedule it if needed."""
    watch = await _db.get_watch(watch_id)
    if not watch:
        raise HTTPException(status_code=404, detail="Watch not found")

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updated = await _db.update_watch(watch_id, **updates)

    # Reschedule in the engine
    await _engine.reschedule_watch(updated)

    event = EventLog(
        event_type="watch_updated",
        watch_id=updated.id,
        watch_name=updated.name,
        message=f"Watch '{updated.name}' updated",
    )
    await _db.add_event_log(event)
    await _broadcast_event(event)

    return _watch_to_dict(updated)


@app.delete("/api/watches/{watch_id}", status_code=204)
async def delete_watch(watch_id: str):
    """Delete a watch and unschedule it."""
    watch = await _db.get_watch(watch_id)
    if not watch:
        raise HTTPException(status_code=404, detail="Watch not found")

    await _engine.remove_watch(watch_id)
    await _db.delete_watch(watch_id)

    event = EventLog(
        event_type="watch_deleted",
        watch_id=watch_id,
        watch_name=watch.name,
        message=f"Watch '{watch.name}' deleted",
    )
    await _db.add_event_log(event)
    await _broadcast_event(event)


@app.post("/api/watches/{watch_id}/check")
async def trigger_check(watch_id: str):
    """Trigger an immediate availability check for a watch."""
    watch = await _db.get_watch(watch_id)
    if not watch:
        raise HTTPException(status_code=404, detail="Watch not found")

    # Run the check in the background so the API responds quickly
    asyncio.create_task(_engine.run_check_now(watch_id))
    return {"message": f"Check triggered for '{watch.name}'", "watch_id": watch_id}


# ---------------------------------------------------------------------------
# Search (RIDB proxy)
# ---------------------------------------------------------------------------

@app.get("/api/search")
async def search(q: str = Query(..., min_length=2, description="Facility search query")):
    """Proxy search to the Recreation.gov search API."""
    try:
        results = await search_facilities(q)
        return results
    except Exception as exc:
        logger.error("Search failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Search failed: {str(exc)}")


@app.get("/api/facilities/{facility_id}/sub-entities")
async def get_facility_sub_entities(facility_id: str, type: str = Query(..., description="Reservation type")):
    """Retrieve sub-entities (entrances or tours) for a facility."""
    if type == "permit":
        # Fetch from permitcontent endpoint
        url = f"https://www.recreation.gov/api/permitcontent/{facility_id}"
        try:
            async with create_http_client() as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    payload = data.get("payload", {})
                    entrances = payload.get("entrances", [])
                    results = []
                    seen_ids = set()
                    for ent in entrances:
                        ent_id = ent.get("id")
                        ent_name = ent.get("name")
                        if ent_id and ent_name:
                            str_id = str(ent_id)
                            results.append({
                                "id": str_id,
                                "name": str(ent_name)
                            })
                            seen_ids.add(str_id)
                    
                    divisions = payload.get("divisions", {})
                    if isinstance(divisions, dict):
                        for div_id, div in divisions.items():
                            div_name = div.get("name")
                            if div_name:
                                str_id = str(div_id)
                                if str_id not in seen_ids:
                                    results.append({
                                        "id": str_id,
                                        "name": str(div_name)
                                    })
                                    seen_ids.add(str_id)
                    elif isinstance(divisions, list):
                        for div in divisions:
                            div_id = div.get("id")
                            div_name = div.get("name")
                            if div_id and div_name:
                                str_id = str(div_id)
                                if str_id not in seen_ids:
                                    results.append({
                                        "id": str_id,
                                        "name": str(div_name)
                                    })
                                    seen_ids.add(str_id)

                    results.sort(key=lambda x: x["name"])
                    return results
        except Exception as exc:
            logger.error("Failed to fetch permit content entrances for %s: %s", facility_id, exc)

    elif type == "timed_entry":
        # Fetch from ticket facility tour endpoint
        url = f"https://www.recreation.gov/api/ticket/facility/{facility_id}/tour"
        try:
            async with create_http_client() as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    tours = resp.json()
                    results = []
                    for t in tours:
                        t_id = t.get("tour_id")
                        t_name = t.get("tour_name")
                        if t_id and t_name:
                            results.append({
                                "id": str(t_id),
                                "name": str(t_name)
                            })
                    results.sort(key=lambda x: x["name"])
                    return results
        except Exception as exc:
            logger.error("Failed to fetch timed entry tours for %s: %s", facility_id, exc)

    return []


# ---------------------------------------------------------------------------
# Event logs
# ---------------------------------------------------------------------------

@app.get("/api/logs")
async def get_logs(limit: int = Query(50, ge=1, le=500)):
    """Return recent event logs."""
    events = await _db.get_recent_events(limit=limit)
    return events


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

@app.post("/api/notifications/test")
async def test_notification():
    """Send a test SMS to verify notification setup."""
    try:
        success = await _notifier.send_test()
        if success:
            event = EventLog(
                event_type="test_sms_sent",
                watch_id="",
                watch_name="",
                message="Test SMS sent successfully",
            )
            await _db.add_event_log(event)
            await _broadcast_event(event)
            return {"success": True, "message": "Test SMS sent successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to send test SMS")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Test notification failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Test notification failed: {str(exc)}")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.get("/api/settings")
async def get_settings():
    """Return current notification settings (sensitive values masked)."""
    return {
        "gmail_address": settings.gmail_address or "",
        "gmail_app_password_set": bool(settings.gmail_app_password),
        "phone_number": settings.phone_number or "",
        "carrier_gateway": settings.carrier_gateway or "",
        "ridb_api_key_set": bool(settings.ridb_api_key),
        "host": settings.host,
        "port": settings.port,
    }


@app.put("/api/settings")
async def update_settings(payload: NotificationSettings):
    """
    Update runtime notification settings.

    Note: These changes are applied in-memory only and do not persist across
    restarts.  For persistent changes, edit your ``.env`` or ``config.yaml``.
    """
    if payload.gmail_address:
        settings.gmail_address = payload.gmail_address
        _notifier.gmail_address = payload.gmail_address
    if payload.phone_number:
        settings.phone_number = payload.phone_number
        _notifier.phone_number = payload.phone_number
    if payload.carrier_gateway:
        settings.carrier_gateway = payload.carrier_gateway
        _notifier.carrier_gateway = payload.carrier_gateway
    if payload.gmail_app_password:
        settings.gmail_app_password = payload.gmail_app_password
        _notifier.gmail_app_password = payload.gmail_app_password

    event = EventLog(
        event_type="settings_updated",
        watch_id="",
        watch_name="",
        message="Notification settings updated",
    )
    await _db.add_event_log(event)
    await _broadcast_event(event)

    return {"message": "Settings updated"}


# ---------------------------------------------------------------------------
# Engine status
# ---------------------------------------------------------------------------

@app.get("/api/status")
async def get_status():
    """Return monitoring engine status."""
    status = _engine.get_status()
    try:
        watches = await _db.list_watches()
        status["total_watches"] = len(watches)
    except Exception:
        status["total_watches"] = 0
    return status


# ---------------------------------------------------------------------------
# SSE – Server-Sent Events
# ---------------------------------------------------------------------------

@app.get("/api/events")
async def sse_events(request: Request):
    """
    SSE stream of real-time events from the monitoring engine.

    The dashboard connects here for live updates.
    """

    async def event_generator() -> AsyncGenerator[dict, None]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        _sse_clients.add(queue)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield {"event": "message", "data": data}
                except asyncio.TimeoutError:
                    # Send keepalive comment to prevent proxy/browser timeouts
                    yield {"event": "ping", "data": ""}
        finally:
            _sse_clients.discard(queue)

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _watch_to_dict(watch: Watch) -> dict:
    """Convert a Watch model to a JSON-serializable dict."""
    data = {}
    for field in (
        "id", "name", "facility_id", "reservation_type", "mode", "status",
        "date_start", "date_end", "drop_time", "poll_interval_minutes",
        "last_checked", "next_check", "created_at", "filters",
    ):
        value = getattr(watch, field, None)
        if value is None:
            data[field] = None
        elif hasattr(value, "value"):
            # Enum
            data[field] = value.value
        elif isinstance(value, datetime):
            data[field] = value.isoformat() + ("Z" if value.tzinfo is None else "")
        elif hasattr(value, "isoformat"):
            data[field] = value.isoformat() + ("Z" if getattr(value, "tzinfo", None) is None else "")
        else:
            data[field] = value

    if _engine is not None:
        data["available_site_ids"] = list(_engine._active_availabilities.get(watch.id, set()))
    else:
        data["available_site_ids"] = []

    return data


def _event_to_dict(event: EventLog) -> dict:
    """Convert an EventLog to a JSON-serializable dict."""
    data = {}
    for field in ("event_type", "watch_id", "watch_name", "message", "details", "timestamp"):
        value = getattr(event, field, None)
        if isinstance(value, datetime):
            data[field] = value.isoformat() + ("Z" if value.tzinfo is None else "")
        else:
            data[field] = value
    return data

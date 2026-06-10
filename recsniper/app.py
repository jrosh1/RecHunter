"""
RecHunter – FastAPI Application
================================
Central API server that ties together the database, monitoring engine,
notifier, and the frontend dashboard with passwordless OTP Telegram authentication.

Run via ``python run.py`` or ``uvicorn recsniper.app:app``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import AsyncGenerator, Set, Optional

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
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
    UserRegister,
    OTPRequest,
    OTPVerify,
    UserOut,
    UserSettingsUpdate,
)
from recsniper.monitor import MonitorEngine
from recsniper.notifier import SMSNotifier
from recsniper.utils import (
    create_http_client,
    search_facilities,
    setup_logging,
    generate_otp,
    sign_token,
    verify_token,
)

logger = logging.getLogger("recsniper.app")

# ---------------------------------------------------------------------------
# Globals populated during lifespan
# ---------------------------------------------------------------------------

_db: Database | None = None
_engine: MonitorEngine | None = None
_notifier: SMSNotifier | None = None

# SSE: connected client queues (queue, user_id)
_sse_clients: Set[tuple[asyncio.Queue, str]] = set()


async def _broadcast_event(event: EventLog) -> None:
    """Push an EventLog to connected SSE clients belonging to the watch owner."""
    watch_user_id = None
    if event.watch_id:
        try:
            watch = await _db.get_watch(event.watch_id)
            if watch:
                watch_user_id = watch.user_id
        except Exception:
            pass

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
    dead: list[tuple[asyncio.Queue, str]] = []
    for q, uid in list(_sse_clients):
        # Filter: only broadcast if the event belongs to the client's user_id or is a global/system event
        if watch_user_id and uid != watch_user_id:
            continue
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            dead.append((q, uid))
    for client in dead:
        _sse_clients.discard(client)


# ---------------------------------------------------------------------------
# Authentication Helper / Dependency
# ---------------------------------------------------------------------------

async def get_current_user(request: Request) -> dict:
    """Helper to authenticate requests using cookie-based signed session tokens."""
    token = request.cookies.get("recsniper_session")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
        
    token_data = verify_token(token)
    if not token_data or "user_id" not in token_data:
        raise HTTPException(status_code=401, detail="Invalid session or session expired")
        
    user = await _db.get_user_by_id(token_data["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
        
    return user


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
    description="Recreation.gov availability monitoring agent with multi-user Telegram auth",
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
async def root(request: Request):
    try:
        await get_current_user(request)
        index = _frontend_dir / "index.html"
        if index.is_file():
            return FileResponse(str(index), media_type="text/html")
        return JSONResponse(
            {"message": "RecHunter API is running. Frontend not found – place files in frontend/."},
            status_code=200,
        )
    except HTTPException:
        return RedirectResponse(url="/login", status_code=307)


@app.get("/login", include_in_schema=False)
async def login_page(request: Request):
    try:
        await get_current_user(request)
        return RedirectResponse(url="/", status_code=307)
    except HTTPException:
        login_html = _frontend_dir / "login.html"
        if login_html.is_file():
            return FileResponse(str(login_html), media_type="text/html")
        return JSONResponse(
            {"message": "RecHunter API is running. Login page not found – place files in frontend/."},
            status_code=200,
        )


# ---------------------------------------------------------------------------
# Passwordless Auth Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/auth/register", status_code=201)
async def register(payload: UserRegister):
    """Register a new user and send a verification OTP to Telegram."""
    existing = await _db.get_user_by_username(payload.username)
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    import uuid
    user_id = str(uuid.uuid4())

    # Create the user directly
    await _db.create_user(
        user_id=user_id,
        username=payload.username,
        phone_number=payload.phone_number,
        carrier_gateway=payload.carrier_gateway or "telegram",
        callmebot_key=payload.callmebot_key
    )

    # Generate and store OTP code
    code = generate_otp()
    await _db.create_otp(user_id, code, expires_in_minutes=5)

    # Send initial activation code via CallMeBot
    temp_notifier = SMSNotifier(
        gmail_address="",
        gmail_app_password=payload.callmebot_key,
        phone_number=payload.phone_number,
        carrier_gateway=payload.carrier_gateway or "telegram"
    )

    msg = f"🎯 RecHunter Registration Code: {code} (expires in 5 min)"
    sent = await temp_notifier.send_sms(msg)
    if not sent:
        # Roll back user creation
        await _db._conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await _db._conn.commit()
        raise HTTPException(
            status_code=400,
            detail="Failed to send Telegram message. Please check that you have started the @CallMeBot_txtbot on Telegram and that your API key is correct."
        )

    return {"message": "Verification code sent to Telegram. Please check your messages.", "username": payload.username}


@app.post("/api/auth/request-otp")
async def request_otp(payload: OTPRequest):
    """Generate and send login OTP to the user's registered Telegram account."""
    user = await _db.get_user_by_username(payload.username)
    if not user:
        raise HTTPException(status_code=404, detail="Username not found")

    user_id = user["id"]
    code = generate_otp()
    await _db.create_otp(user_id, code, expires_in_minutes=5)

    temp_notifier = SMSNotifier(
        gmail_address="",
        gmail_app_password=user["callmebot_key"],
        phone_number=user["phone_number"],
        carrier_gateway=user["carrier_gateway"]
    )

    msg = f"🔑 RecHunter Login Code: {code} (expires in 5 min)"
    sent = await temp_notifier.send_sms(msg)
    if not sent:
        raise HTTPException(
            status_code=500,
            detail="Failed to send OTP to Telegram. Please verify your CallMeBot configuration."
        )

    return {"message": "Verification code sent to Telegram."}


@app.post("/api/auth/verify-otp")
async def verify_otp(payload: OTPVerify, response: Response):
    """Validate OTP and issue a cookie-based session token."""
    user = await _db.get_user_by_username(payload.username)
    if not user:
        raise HTTPException(status_code=404, detail="Username not found")

    is_valid = await _db.verify_otp(user["id"], payload.code)
    if not is_valid:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")

    token_data = {"user_id": user["id"], "username": user["username"]}
    token = sign_token(token_data)

    response.set_cookie(
        key="recsniper_session",
        value=token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=86400 * 30  # 30 days
    )

    return {
        "id": user["id"],
        "username": user["username"],
        "phone_number": user["phone_number"],
        "carrier_gateway": user["carrier_gateway"]
    }


@app.post("/api/auth/logout")
async def logout(response: Response):
    """Log the user out by deleting the session cookie."""
    response.delete_cookie(key="recsniper_session")
    return {"message": "Logged out successfully"}


@app.get("/api/auth/me")
async def get_me(request: Request):
    """Retrieve details for the currently authenticated session."""
    user = await get_current_user(request)
    return {
        "id": user["id"],
        "username": user["username"],
        "phone_number": user["phone_number"],
        "carrier_gateway": user["carrier_gateway"]
    }


@app.put("/api/auth/settings")
async def update_user_settings(payload: UserSettingsUpdate, request: Request):
    """Update notification credentials for the authenticated user."""
    user = await get_current_user(request)
    
    # Send a quick test to make sure new settings work!
    temp_notifier = SMSNotifier(
        gmail_address="",
        gmail_app_password=payload.callmebot_key,
        phone_number=payload.phone_number,
        carrier_gateway=payload.carrier_gateway or "telegram"
    )
    msg = "⚙️ RecHunter settings updated successfully!"
    sent = await temp_notifier.send_sms(msg)
    if not sent:
        raise HTTPException(
            status_code=400,
            detail="Failed to send verification to Telegram with new settings. Please verify details."
        )

    updated = await _db.update_user_settings(
        user_id=user["id"],
        phone_number=payload.phone_number,
        carrier_gateway=payload.carrier_gateway or "telegram",
        callmebot_key=payload.callmebot_key
    )
    return {
        "id": updated["id"],
        "username": updated["username"],
        "phone_number": updated["phone_number"],
        "carrier_gateway": updated["carrier_gateway"]
    }


# ---------------------------------------------------------------------------
# Watches CRUD (User-Scoped)
# ---------------------------------------------------------------------------

@app.get("/api/watches")
async def list_watches(request: Request):
    """Return all watches owned by the authenticated user."""
    user = await get_current_user(request)
    watches = await _db.list_watches(user_id=user["id"])
    return [_watch_to_dict(w) for w in watches]


@app.post("/api/watches", status_code=201)
async def create_watch(payload: WatchCreate, request: Request):
    """Create a new watch owned by the authenticated user."""
    user = await get_current_user(request)
    watch_data = payload.model_dump()
    watch_data["user_id"] = user["id"]
    
    watch = Watch(**watch_data)
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

    # Trigger immediate check in the background
    if created.status == WatchStatus.ACTIVE:
        asyncio.create_task(_engine.run_check_now(created.id))

    return _watch_to_dict(created)


@app.get("/api/watches/{watch_id}")
async def get_watch(watch_id: str, request: Request):
    """Get a watch by ID, scoping it to the authenticated user."""
    user = await get_current_user(request)
    watch = await _db.get_watch(watch_id, user_id=user["id"])
    if not watch:
        raise HTTPException(status_code=404, detail="Watch not found")
    return _watch_to_dict(watch)


@app.put("/api/watches/{watch_id}")
async def update_watch(watch_id: str, payload: WatchUpdate, request: Request):
    """Update a watch, scoping it to the authenticated user."""
    user = await get_current_user(request)
    watch = await _db.get_watch(watch_id, user_id=user["id"])
    if not watch:
        raise HTTPException(status_code=404, detail="Watch not found")

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updated = await _db.update_watch(watch_id, user_id=user["id"], **updates)

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
async def delete_watch(watch_id: str, request: Request):
    """Delete a watch, scoping it to the authenticated user."""
    user = await get_current_user(request)
    watch = await _db.get_watch(watch_id, user_id=user["id"])
    if not watch:
        raise HTTPException(status_code=404, detail="Watch not found")

    await _engine.remove_watch(watch_id)
    await _db.delete_watch(watch_id, user_id=user["id"])

    event = EventLog(
        event_type="watch_deleted",
        watch_id=watch_id,
        watch_name=watch.name,
        message=f"Watch '{watch.name}' deleted",
    )
    await _db.add_event_log(event)
    await _broadcast_event(event)


@app.post("/api/watches/{watch_id}/check")
async def trigger_check(watch_id: str, request: Request):
    """Trigger an immediate check, scoping it to the authenticated user."""
    user = await get_current_user(request)
    watch = await _db.get_watch(watch_id, user_id=user["id"])
    if not watch:
        raise HTTPException(status_code=404, detail="Watch not found")

    asyncio.create_task(_engine.run_check_now(watch_id))
    return {"message": f"Check triggered for '{watch.name}'", "watch_id": watch_id}


# ---------------------------------------------------------------------------
# Search (RIDB proxy)
# ---------------------------------------------------------------------------

@app.get("/api/search")
async def search(q: str = Query(..., min_length=2, description="Facility search query"), request: Request = None):
    """Proxy search to the Recreation.gov search API."""
    if request:
        await get_current_user(request)
    try:
        results = await search_facilities(q)
        return results
    except Exception as exc:
        logger.error("Search failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Search failed: {str(exc)}")


@app.get("/api/facilities/{facility_id}/sub-entities")
async def get_facility_sub_entities(facility_id: str, type: str = Query(..., description="Reservation type"), request: Request = None):
    """Retrieve sub-entities (entrances or tours) for a facility."""
    if request:
        await get_current_user(request)
    if type == "permit":
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
async def get_logs(request: Request, limit: int = Query(50, ge=1, le=500)):
    """Return recent event logs for the authenticated user's watches."""
    user = await get_current_user(request)
    events = await _db.get_recent_events(user_id=user["id"], limit=limit)
    return events


# ---------------------------------------------------------------------------
# Global Status
# ---------------------------------------------------------------------------

@app.get("/api/status")
async def get_status(request: Request):
    """Return monitoring engine status and total user watches."""
    user = await get_current_user(request)
    status = _engine.get_status()
    try:
        watches = await _db.list_watches(user_id=user["id"])
        status["total_watches"] = len(watches)
        # Scope active watches count to this user
        status["active_watches"] = len([w for w in watches if w.status in (WatchStatus.ACTIVE, WatchStatus.TRIGGERED)])
        
        # Scope total checks count to this user (checks in the last 24 hours)
        cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        cursor = await _db._conn.execute(
            """
            SELECT COUNT(*) FROM event_log e
            JOIN watches w ON e.watch_id = w.id
            WHERE w.user_id = ? AND e.event_type IN ('check_complete', 'availability_found') AND e.timestamp >= ?
            """,
            (user["id"], cutoff),
        )
        row = await cursor.fetchone()
        status["total_checks"] = row[0] if row else 0
    except Exception as exc:
        logger.error("Failed to calculate user status stats: %s", exc)
        status["total_watches"] = 0
        status["active_watches"] = 0
        status["total_checks"] = 0
    return status


# ---------------------------------------------------------------------------
# SSE – Server-Sent Events (User-Filtered)
# ---------------------------------------------------------------------------

@app.get("/api/events")
async def sse_events(request: Request):
    """SSE stream of real-time events scoped to the authenticated user."""
    user = await get_current_user(request)
    user_id = user["id"]

    async def event_generator() -> AsyncGenerator[dict, None]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        client_tuple = (queue, user_id)
        _sse_clients.add(client_tuple)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield {"event": "message", "data": data}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
        finally:
            _sse_clients.discard(client_tuple)

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

"""
RecHunter Utilities
===================

Shared helpers used across the application:

* HTTP client factory with realistic headers
* Async rate limiter
* Logging setup (loguru: console + rotating file)
* Timezone helpers
* RIDB facility search
* Deep-link builders for Recreation.gov
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, date
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import httpx
from loguru import logger

from recsniper.config import settings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_RIDB_BASE_URL = "https://ridb.recreation.gov/api/v1"

_REC_GOV_BASE = "https://www.recreation.gov"

# Timezone objects
TZ_EASTERN = ZoneInfo("America/New_York")
TZ_CENTRAL = ZoneInfo("America/Chicago")
TZ_MOUNTAIN = ZoneInfo("America/Denver")
TZ_PACIFIC = ZoneInfo("America/Los_Angeles")
TZ_UTC = ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(log_path: Optional[str] = None) -> None:
    """Configure loguru for console + rotating file output.

    Call once at application startup.  Safe to call multiple times (removes
    existing sinks first).

    Parameters
    ----------
    log_path : str | None
        Path to the rotating log file.  Defaults to ``settings.log_path``.
    """
    log_file = Path(log_path or settings.log_path)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Remove default loguru handler
    logger.remove()

    # Console sink — colourised, human-friendly
    logger.add(
        sys.stderr,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> — "
            "<level>{message}</level>"
        ),
        level="DEBUG",
        colorize=True,
    )

    # File sink — rotating, machine-parseable
    logger.add(
        str(log_file),
        rotation="10 MB",
        retention="7 days",
        compression="zip",
        format="{time:YYYY-MM-DDTHH:mm:ss.SSSZ} | {level: <8} | {name}:{function}:{line} | {message}",
        level="DEBUG",
        enqueue=True,  # thread-safe async writes
    )

    logger.info("Logging initialised → {}", log_file)


# ---------------------------------------------------------------------------
# HTTP client factory
# ---------------------------------------------------------------------------

# Module-level shared client (lazy-init singleton)
_shared_client: httpx.AsyncClient | None = None


async def get_http_client() -> httpx.AsyncClient:
    """Return (or create) the shared ``httpx.AsyncClient`` singleton.

    Providers call this to get a client with realistic browser headers.
    The client is reused across the application for connection pooling.
    """
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = create_http_client()
    return _shared_client

def create_http_client(
    timeout: float = 30.0,
    follow_redirects: bool = True,
) -> httpx.AsyncClient:
    """Create an ``httpx.AsyncClient`` with realistic browser headers.

    The returned client should be used as an async context manager or closed
    explicitly when no longer needed.
    """
    return httpx.AsyncClient(
        headers={
            "User-Agent": _USER_AGENT,
            "Referer": "https://www.recreation.gov/",
            "Accept": "application/json, text/html, */*",
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=httpx.Timeout(timeout),
        follow_redirects=follow_redirects,
    )


# ---------------------------------------------------------------------------
# Async rate limiter
# ---------------------------------------------------------------------------

class AsyncRateLimiter:
    """Token-bucket-style async rate limiter.

    Parameters
    ----------
    max_per_minute : int
        Maximum number of requests (tokens) allowed per 60-second window.

    Usage::

        limiter = AsyncRateLimiter(max_per_minute=30)
        await limiter.acquire()          # blocks until a token is available
        response = await client.get(url)
    """

    def __init__(self, max_per_minute: int = 30) -> None:
        self.max_per_minute = max_per_minute
        self._interval = 60.0 / max_per_minute  # seconds between tokens
        self._lock = asyncio.Lock()
        self._last_call: float = 0.0

    async def acquire(self) -> None:
        """Wait until the next request is permitted, then consume a token."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self._interval:
                await asyncio.sleep(self._interval - elapsed)
            self._last_call = time.monotonic()

    async def __aenter__(self) -> "AsyncRateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, *exc: object) -> None:
        pass


# Module-level singleton – providers import this directly
rate_limiter = AsyncRateLimiter(max_per_minute=30)


# ---------------------------------------------------------------------------
# Timezone helpers
# ---------------------------------------------------------------------------

def to_eastern(dt: datetime) -> datetime:
    """Convert a datetime to US/Eastern."""
    return dt.astimezone(TZ_EASTERN)


def to_central(dt: datetime) -> datetime:
    """Convert a datetime to US/Central."""
    return dt.astimezone(TZ_CENTRAL)


def to_mountain(dt: datetime) -> datetime:
    """Convert a datetime to US/Mountain."""
    return dt.astimezone(TZ_MOUNTAIN)


def to_pacific(dt: datetime) -> datetime:
    """Convert a datetime to US/Pacific."""
    return dt.astimezone(TZ_PACIFIC)


def now_eastern() -> datetime:
    """Return the current time in US/Eastern."""
    return datetime.now(TZ_EASTERN)


def now_central() -> datetime:
    """Return the current time in US/Central."""
    return datetime.now(TZ_CENTRAL)


def now_mountain() -> datetime:
    """Return the current time in US/Mountain."""
    return datetime.now(TZ_MOUNTAIN)


def now_pacific() -> datetime:
    """Return the current time in US/Pacific."""
    return datetime.now(TZ_PACIFIC)


def now_utc() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(TZ_UTC)


# ---------------------------------------------------------------------------
# RIDB facility search
# ---------------------------------------------------------------------------

async def search_facilities(
    query: str,
    api_key: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """Search the Recreation.gov search API for facilities matching *query*.

    This replaces the limited RIDB API with the comprehensive Recreation.gov internal
    search API, allowing the user to search campgrounds, permits, and tours/timed-entry alike.
    """
    url = "https://www.recreation.gov/api/search"
    params = {"q": query}

    try:
        async with create_http_client() as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            raw_results = data.get("results", [])

            results = []
            for item in raw_results[:limit]:
                results.append({
                    "facility_id": item.get("entity_id", ""),
                    "name": item.get("name", "Unknown"),
                    "parent_name": item.get("parent_name", ""),
                    "type": item.get("entity_type", ""),
                })
            return results
    except Exception as exc:
        logger.error("Recreation.gov search failed: {}", exc)
        return []


# ---------------------------------------------------------------------------
# Deep link builders
# ---------------------------------------------------------------------------

def campground_url(facility_id: str) -> str:
    """Build a deep link to a campground on Recreation.gov.

    Example: ``https://www.recreation.gov/camping/campgrounds/232447``
    """
    return f"{_REC_GOV_BASE}/camping/campgrounds/{facility_id}"


def campground_availability_url(facility_id: str, month: Optional[date] = None) -> str:
    """Build a deep link to the availability calendar for a campground.

    If *month* is provided the URL includes the ``&start_date=`` parameter so
    the calendar opens on the correct page.
    """
    base = f"{_REC_GOV_BASE}/camping/campgrounds/{facility_id}/availability"
    if month:
        return f"{base}?start_date={month.strftime('%Y-%m-01')}"
    return base


def permit_url(facility_id: str) -> str:
    """Build a deep link to a permit facility on Recreation.gov.

    Example: ``https://www.recreation.gov/permits/233262``
    """
    return f"{_REC_GOV_BASE}/permits/{facility_id}"


def permit_availability_url(facility_id: str) -> str:
    """Build a deep link to the permit availability page."""
    return f"{_REC_GOV_BASE}/permits/{facility_id}/registration/detailed-availability"


def timed_entry_url(facility_id: str) -> str:
    """Build a deep link to a timed-entry ticket page.

    Example: ``https://www.recreation.gov/timed-entry/10088802``
    """
    return f"{_REC_GOV_BASE}/timed-entry/{facility_id}"


def timed_entry_availability_url(facility_id: str) -> str:
    """Build a deep link to the timed-entry availability page."""
    return f"{_REC_GOV_BASE}/timed-entry/{facility_id}/availability"


def build_booking_url(
    facility_id: str,
    reservation_type: str,
    target_date: Optional[date] = None,
) -> str:
    """Build the most useful booking deep link for the given reservation type.

    Parameters
    ----------
    facility_id : str
        The Recreation.gov facility ID.
    reservation_type : str
        One of ``"campground"``, ``"permit"``, ``"timed_entry"``.
    target_date : date | None
        Optional date to focus the availability view.
    """
    if reservation_type == "campground":
        return campground_availability_url(facility_id, target_date)
    elif reservation_type == "permit":
        return permit_availability_url(facility_id)
    elif reservation_type == "timed_entry":
        return timed_entry_availability_url(facility_id)
    else:
        return campground_url(facility_id)


# ---------------------------------------------------------------------------
# Authentication Utilities
# ---------------------------------------------------------------------------

def generate_otp() -> str:
    """Generate a secure 6-digit numeric One-Time Password."""
    import secrets
    return str(secrets.randbelow(900000) + 100000)


def sign_token(data: dict, expires_in: int = 86400 * 30) -> str:
    """Generate a signed base64-encoded token."""
    import base64
    import hmac
    import hashlib
    import json
    import time

    secret_key = "recsniper-super-secret-key-12345!"
    payload = {
        "data": data,
        "exp": int(time.time()) + expires_in
    }
    payload_bytes = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b'=')
    signature = hmac.new(secret_key.encode(), payload_bytes, hashlib.sha256).digest()
    sig_bytes = base64.urlsafe_b64encode(signature).rstrip(b'=')
    return f"{payload_bytes.decode()}.{sig_bytes.decode()}"


def verify_token(token: str) -> Optional[dict]:
    """Verify and decode a signed base64-encoded token."""
    import base64
    import hmac
    import hashlib
    import json
    import time

    secret_key = "recsniper-super-secret-key-12345!"
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return None
        payload_part, sig_part = parts[0], parts[1]
        
        # Verify signature
        expected_sig = base64.urlsafe_b64encode(
            hmac.new(secret_key.encode(), payload_part.encode(), hashlib.sha256).digest()
        ).rstrip(b'=').decode()
        
        if not hmac.compare_digest(sig_part, expected_sig):
            return None
            
        # Decode payload
        padding = '=' * (4 - len(payload_part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_part + padding).decode())
        
        if payload["exp"] < time.time():
            return None
            
        return payload["data"]
    except Exception:
        return None

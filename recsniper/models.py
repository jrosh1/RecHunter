"""
RecHunter Data Models
=====================

Pydantic v2 data models for watches, availability slots, check results,
event logging, and notification settings. These models form the shared
schema used across the API, database, and scheduler layers.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ReservationType(str, Enum):
    """The kind of Recreation.gov reservation being monitored."""
    CAMPGROUND = "campground"
    PERMIT = "permit"
    TIMED_ENTRY = "timed_entry"


class WatchMode(str, Enum):
    """How the watch should poll for availability.

    DROP_TIME    – Burst-check at the exact release moment (e.g. 10:00 ET).
    CANCELLATION – Continuous polling for cancellations at a set interval.
    ONE_SHOT     – Single check, then mark completed.
    """
    DROP_TIME = "drop_time"
    CANCELLATION = "cancellation"
    ONE_SHOT = "one_shot"


class WatchStatus(str, Enum):
    """Lifecycle status of a watch."""
    ACTIVE = "active"
    PAUSED = "paused"
    TRIGGERED = "triggered"       # Availability was found
    ERROR = "error"
    COMPLETED = "completed"       # One-shot finished


# ---------------------------------------------------------------------------
# Core domain models
# ---------------------------------------------------------------------------

class Watch(BaseModel):
    """A monitored reservation watch.

    Each watch tracks a single facility + date range + mode and stores its
    own polling cadence, filters, and scheduling metadata.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    name: str
    facility_id: str
    reservation_type: ReservationType
    date_start: date
    date_end: Optional[date] = None
    mode: WatchMode = WatchMode.CANCELLATION
    poll_interval_minutes: int = 10
    drop_time: Optional[str] = None          # e.g. "10:00 ET"
    status: WatchStatus = WatchStatus.ACTIVE
    filters: dict = Field(default_factory=dict)  # min_consecutive_nights, equipment, etc.
    last_checked: Optional[datetime] = None
    next_check: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AvailabilitySlot(BaseModel):
    """A single available slot discovered during a check."""
    facility_id: str
    facility_name: str = ""
    site_id: str = ""            # campsite ID or permit entrance ID
    site_name: str = ""          # e.g. "Site 042" or "Whitney Portal"
    date: date
    status: str = "Available"
    booking_url: str = ""
    raw_data: dict = Field(default_factory=dict)


class CheckResult(BaseModel):
    """The outcome of a single availability check for one watch."""
    watch_id: str
    watch_name: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    new_slots: list[AvailabilitySlot] = []
    total_available: int = 0
    error: Optional[str] = None


class EventLog(BaseModel):
    """An auditable event record for the system timeline."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    event_type: str   # check_complete, availability_found, sms_sent, error, watch_added, …
    watch_id: Optional[str] = None
    watch_name: Optional[str] = None
    message: str
    details: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Notification settings
# ---------------------------------------------------------------------------

class NotificationSettings(BaseModel):
    """User notification preferences (SMS-via-email gateway)."""
    gmail_address: str = ""
    gmail_app_password: str = ""
    phone_number: str = ""
    carrier_gateway: str = "tmomail.net"
    enabled: bool = True


# ---------------------------------------------------------------------------
# API request schemas
# ---------------------------------------------------------------------------

class WatchCreate(BaseModel):
    """Schema for creating a new watch via API."""
    name: str
    facility_id: str
    reservation_type: ReservationType
    date_start: date
    date_end: Optional[date] = None
    mode: WatchMode = WatchMode.CANCELLATION
    poll_interval_minutes: int = 10
    drop_time: Optional[str] = None
    filters: dict = Field(default_factory=dict)


class WatchUpdate(BaseModel):
    """Schema for partially updating a watch via API."""
    name: Optional[str] = None
    date_start: Optional[date] = None
    date_end: Optional[date] = None
    mode: Optional[WatchMode] = None
    poll_interval_minutes: Optional[int] = None
    drop_time: Optional[str] = None
    status: Optional[WatchStatus] = None
    filters: Optional[dict] = None


# ---------------------------------------------------------------------------
# Multi-User Authentication & Profile schemas
# ---------------------------------------------------------------------------

class UserRegister(BaseModel):
    """Schema for registering a new user."""
    username: str
    phone_number: str            # Telegram username (e.g. "@jrosh") or phone number
    carrier_gateway: str = "telegram"
    callmebot_key: Optional[str] = ""  # CallMeBot API key (optional for Telegram)

class OTPRequest(BaseModel):
    """Schema for requesting a login One-Time Password."""
    username: str

class OTPVerify(BaseModel):
    """Schema for verifying a One-Time Password."""
    username: str
    code: str

class UserOut(BaseModel):
    """Schema for returning user information."""
    id: str
    username: str
    phone_number: Optional[str] = None
    carrier_gateway: str = "telegram"
    created_at: datetime

class UserSettingsUpdate(BaseModel):
    """Schema for updating user notification preferences."""
    phone_number: str
    carrier_gateway: str = "telegram"
    callmebot_key: Optional[str] = ""


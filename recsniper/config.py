"""
RecHunter Configuration
=======================

Loads settings from environment variables (via ``.env``) and an optional
``config.yaml`` for pre-configured watches.  Uses a plain dataclass to
avoid circular dependencies with Pydantic models.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Resolve project root & load .env
# ---------------------------------------------------------------------------

# Walk up from this file to find the project root (contains .env / config.yaml)
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent  # recsniper/ package sits one level below root

# Load .env from project root (no-op if the file doesn't exist)
_dotenv_path = _PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=_dotenv_path)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = str(_PROJECT_ROOT / "data" / "recsniper.db")
_DEFAULT_LOG_PATH = str(_PROJECT_ROOT / "data" / "recsniper.log")
_DEFAULT_HOST = "0.0.0.0"
_DEFAULT_PORT = 8080
_DEFAULT_CARRIER_GATEWAY = "tmomail.net"


# ---------------------------------------------------------------------------
# Settings dataclass
# ---------------------------------------------------------------------------

@dataclass
class Settings:
    """Application-wide settings populated from env vars + ``config.yaml``.

    Attributes
    ----------
    gmail_address : str
        Gmail address used to send SMS-via-email notifications.
    gmail_app_password : str
        Gmail *App Password* (not the regular account password).
    phone_number : str
        Recipient phone number (digits only, no country code prefix).
    carrier_gateway : str
        Email-to-SMS gateway domain (e.g. ``tmomail.net``).
    ridb_api_key : str
        API key for the RIDB (Recreation Information Database) API.
    host : str
        Host the web server binds to.
    port : int
        Port the web server listens on.
    db_path : str
        Absolute path to the SQLite database file.
    log_path : str
        Absolute path to the rotating log file.
    watches : list[dict[str, Any]]
        Pre-configured watches loaded from ``config.yaml``.
    """

    # --- Notification ---
    gmail_address: str = ""
    gmail_app_password: str = ""
    phone_number: str = ""
    carrier_gateway: str = _DEFAULT_CARRIER_GATEWAY

    # --- APIs ---
    ridb_api_key: str = ""

    # --- Server ---
    host: str = _DEFAULT_HOST
    port: int = _DEFAULT_PORT

    # --- Storage ---
    db_path: str = _DEFAULT_DB_PATH
    log_path: str = _DEFAULT_LOG_PATH

    # --- Pre-configured watches from config.yaml ---
    watches: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loader helpers
# ---------------------------------------------------------------------------

def _load_yaml_config(path: Path) -> dict[str, Any]:
    """Load ``config.yaml`` and return its contents as a dict.

    Returns an empty dict if the file doesn't exist or is empty.
    """
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        # Malformed YAML should not crash startup
        return {}


def load_settings() -> Settings:
    """Build a :class:`Settings` instance from env vars and config.yaml.

    Resolution order (highest priority wins):

    1. Environment variables (including those loaded from ``.env``).
    2. ``config.yaml`` values.
    3. Built-in defaults in the dataclass.
    """
    yaml_cfg = _load_yaml_config(_PROJECT_ROOT / "config.yaml")

    # Merge env → yaml → defaults
    def _get(env_key: str, yaml_key: str, default: Any = "") -> str:
        """Return the first non-empty value from env, yaml, or default."""
        val = os.getenv(env_key, "").strip()
        if val:
            return val
        yaml_val = yaml_cfg.get(yaml_key, "")
        if yaml_val:
            return str(yaml_val).strip()
        return str(default)

    settings = Settings(
        gmail_address=_get("GMAIL_ADDRESS", "gmail_address"),
        gmail_app_password=_get("GMAIL_APP_PASSWORD", "gmail_app_password"),
        phone_number=_get("PHONE_NUMBER", "phone_number"),
        carrier_gateway=_get("CARRIER_GATEWAY", "carrier_gateway", _DEFAULT_CARRIER_GATEWAY),
        ridb_api_key=_get("RIDB_API_KEY", "ridb_api_key"),
        host=_get("HOST", "host", _DEFAULT_HOST),
        port=int(_get("PORT", "port", _DEFAULT_PORT)),
        db_path=_get("DB_PATH", "db_path", _DEFAULT_DB_PATH),
        log_path=_get("LOG_PATH", "log_path", _DEFAULT_LOG_PATH),
        watches=yaml_cfg.get("watches", []),
    )

    # Ensure the data directory exists
    data_dir = Path(settings.db_path).parent
    data_dir.mkdir(parents=True, exist_ok=True)

    return settings


# ---------------------------------------------------------------------------
# Module-level singleton (import `settings` from here)
# ---------------------------------------------------------------------------

settings: Settings = load_settings()

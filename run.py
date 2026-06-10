#!/usr/bin/env python3
"""
RecHunter – Entry Point
=======================
Launch the monitoring dashboard and API server.

Usage::

    python run.py
"""

import os
import sys
import threading
import time
import webbrowser

# Force UTF-8 output on Windows consoles
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import uvicorn

from recsniper.config import settings


def main() -> None:
    host = settings.host
    port = settings.port

    # Bind to localhost for browser auto-open if 0.0.0.0
    display_host = "127.0.0.1" if host == "0.0.0.0" else host

    print()
    print("  🌲  RecHunter – Recreation.gov Booking Agent")
    print("  ─────────────────────────────────────────────")
    print(f"  📡  Dashboard : http://{display_host}:{port}")
    print(f"  📂  Database  : {settings.db_path}")
    print(f"  📝  Logs      : {settings.log_path}")
    print()
    print("  Press Ctrl+C to stop")
    print()

    # Open browser after a short delay so the server has time to start
    def open_browser() -> None:
        time.sleep(1.5)
        try:
            webbrowser.open(f"http://{display_host}:{port}")
        except Exception:
            pass  # non-critical

    threading.Thread(target=open_browser, daemon=True).start()

    try:
        uvicorn.run(
            "recsniper.app:app",
            host=host,
            port=port,
            log_level="info",
        )
    except KeyboardInterrupt:
        print("\n  👋  RecHunter stopped. Happy camping!")
        sys.exit(0)


if __name__ == "__main__":
    main()

"""Single source of truth for cross-CLI and runtime defaults.

The dashboard FastAPI app (``api/main.py``) and the Vite dev proxy both assume
port **8000** on 127.0.0.1. ``Sponsio(..., dashboard=True)`` and
``sponsio serve`` must use the same host/port to avoid a split-brain where
pushes and the UI point at different URLs.
"""

from __future__ import annotations

DASHBOARD_DEFAULT_HOST: str = "127.0.0.1"
DASHBOARD_DEFAULT_PORT: int = 8000


def default_dashboard_url() -> str:
    """Return ``http://127.0.0.1:8000`` (or the configured host/port)."""
    return f"http://{DASHBOARD_DEFAULT_HOST}:{DASHBOARD_DEFAULT_PORT}"


__all__ = [
    "DASHBOARD_DEFAULT_HOST",
    "DASHBOARD_DEFAULT_PORT",
    "default_dashboard_url",
]

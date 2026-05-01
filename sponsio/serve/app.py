"""FastAPI app for the local single-user dashboard backend.

Endpoints (all read-only, all served from ``127.0.0.1``):

* ``GET /api/capabilities`` — feature-flag map. The frontend uses this
  to gate Cloud-only tabs (sto judge, multi-tenant, ingestion).
* ``GET /api/sessions`` — list every agent that has session logs.
* ``GET /api/sessions/{agent_id}/traces`` — list trace files for one agent.
* ``GET /api/sessions/{agent_id}/traces/{trace_id}`` — events for one trace.

The dashboard reads ``~/.sponsio/sessions/`` (overridable with
``SPONSIO_SESSIONS_DIR``). It does **not** ingest spans, run sto judges,
or persist anything — Sponsio Cloud handles those.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sponsio._paths import PathEscapeError, safe_join_segment
from sponsio.runtime.session_log import _resolve_default_base_dir

if TYPE_CHECKING:
    from fastapi import FastAPI


# ---------------------------------------------------------------------------
# Capabilities — the OSS feature-flag map.
# ---------------------------------------------------------------------------

# Frontend reads this to hide Cloud-only routes (sto judge views,
# multi-tenant org switcher, hosted-ingestion settings, etc.). Cloud
# overrides ``create_app`` with a richer capability set.
_OSS_FEATURES: dict[str, bool] = {
    "session_browser": True,
    "trace_viewer": True,
    # Not yet wired; the next iteration adds these endpoints.
    "contract_browser": False,
    "violations": False,
    "host_buckets": False,
    "live_trace": False,
    # Permanently Cloud-only.
    "sto_judge": False,
    "multi_tenant": False,
    "hosted_ingestion": False,
    "leaderboard": False,
    "alerting": False,
}


def _resolve_sessions_dir(override: Path | None) -> Path:
    """Pick the sessions directory: explicit override → env → user home."""
    if override is not None:
        return Path(override).expanduser()
    return _resolve_default_base_dir()


def _list_agents(sessions_dir: Path) -> list[dict[str, Any]]:
    """Scan ``sessions_dir`` for agent subdirectories."""
    if not sessions_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(sessions_dir.iterdir()):
        if not child.is_dir():
            continue
        traces = sorted(child.glob("*.jsonl"))
        if not traces:
            continue
        latest = max((p.stat().st_mtime for p in traces), default=0.0)
        out.append(
            {
                "agent_id": child.name,
                "trace_count": len(traces),
                "latest_mtime": latest,
            }
        )
    return out


def _resolve_agent_dir(sessions_dir: Path, agent_id: str) -> Path:
    """Safely resolve ``sessions_dir/agent_id``. Raises on traversal."""
    return safe_join_segment(sessions_dir, agent_id)


def _list_traces(agent_dir: Path) -> list[dict[str, Any]]:
    """Return one entry per ``*.jsonl`` file in ``agent_dir``."""
    if not agent_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(agent_dir.glob("*.jsonl")):
        st = path.stat()
        out.append(
            {
                "trace_id": path.stem,
                "filename": path.name,
                "size_bytes": st.st_size,
                "mtime": st.st_mtime,
            }
        )
    return out


def _read_trace(agent_dir: Path, trace_id: str) -> list[dict[str, Any]]:
    """Parse one JSONL trace file into a list of event records.

    Malformed lines are dropped silently — the session logger writes
    best-effort, so a partial line at the tail of a live file is
    expected.
    """
    try:
        path = safe_join_segment(agent_dir, f"{trace_id}.jsonl")
    except PathEscapeError as exc:
        raise FileNotFoundError(trace_id) from exc
    if not path.is_file():
        raise FileNotFoundError(trace_id)
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def create_app(sessions_dir: Path | None = None) -> "FastAPI":
    """Build the local dashboard FastAPI app.

    Args:
        sessions_dir: Override the session log root. Tests pass a
            ``tmp_path``; production reads ``SPONSIO_SESSIONS_DIR`` or
            falls back to ``~/.sponsio/sessions``.

    Raises:
        ImportError: If the ``[web]`` extra is not installed. The CLI
            wrapper translates this into a friendly hint.
    """
    try:
        from fastapi import FastAPI, HTTPException
    except ImportError as exc:
        raise ImportError(
            "sponsio serve requires the [web] extra. "
            "Install with: pip install 'sponsio[web]'"
        ) from exc

    resolved = _resolve_sessions_dir(sessions_dir)
    app = FastAPI(
        title="Sponsio Local Dashboard",
        description="Single-user, read-only view over local session logs.",
        version="0.1.0",
    )

    @app.get("/api/capabilities")
    def capabilities() -> dict[str, Any]:
        from sponsio import __version__ as sponsio_version

        return {
            "tier": "oss",
            "version": sponsio_version,
            "sessions_dir": str(resolved),
            "features": dict(_OSS_FEATURES),
        }

    @app.get("/api/sessions")
    def sessions() -> dict[str, Any]:
        return {
            "sessions_dir": str(resolved),
            "agents": _list_agents(resolved),
        }

    @app.get("/api/sessions/{agent_id}/traces")
    def traces(agent_id: str) -> dict[str, Any]:
        try:
            agent_dir = _resolve_agent_dir(resolved, agent_id)
        except PathEscapeError as exc:
            raise HTTPException(status_code=400, detail="invalid agent_id") from exc
        if not agent_dir.is_dir():
            raise HTTPException(status_code=404, detail="agent not found")
        return {"agent_id": agent_id, "traces": _list_traces(agent_dir)}

    @app.get("/api/sessions/{agent_id}/traces/{trace_id}")
    def trace_events(agent_id: str, trace_id: str) -> dict[str, Any]:
        try:
            agent_dir = _resolve_agent_dir(resolved, agent_id)
        except PathEscapeError as exc:
            raise HTTPException(status_code=400, detail="invalid agent_id") from exc
        if not agent_dir.is_dir():
            raise HTTPException(status_code=404, detail="agent not found")
        try:
            events = _read_trace(agent_dir, trace_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="trace not found") from exc
        return {
            "agent_id": agent_id,
            "trace_id": trace_id,
            "events": events,
        }

    return app

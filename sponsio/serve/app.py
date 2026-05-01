"""FastAPI app for the local single-user dashboard backend.

Endpoints (all read-only, all served from ``127.0.0.1``):

* ``GET  /api/capabilities``                                — feature-flag map
* ``GET  /api/sessions``                                    — list agents
* ``GET  /api/sessions/{agent_id}/traces``                  — trace files for one agent
* ``GET  /api/sessions/{agent_id}/traces/{trace_id}``       — events for one trace
* ``GET  /api/contracts``                                   — pattern catalog + sponsio.yaml
* ``GET  /api/host/buckets``                                — list ``~/.sponsio/plugins/<bucket>``
* ``GET  /api/host/buckets/{bucket}/events``                — recent events from one bucket
* ``WS   /api/live``                                        — tail new session-log events

The dashboard reads ``~/.sponsio/sessions/`` (overridable with
``SPONSIO_SESSIONS_DIR``) and ``~/.sponsio/plugins/`` (overridable with
``SPONSIO_PLUGINS_DIR``). It does **not** ingest spans, run sto judges,
or persist anything — Sponsio Cloud handles those.
"""

import asyncio
import inspect
import json
import os
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
    "contract_browser": True,
    "host_buckets": True,
    "live_trace": True,
    # Not yet wired in OSS — re-verifying past traces against contracts
    # is a separate next batch.
    "violations": False,
    # Permanently Cloud-only.
    "sto_judge": False,
    "multi_tenant": False,
    "hosted_ingestion": False,
    "leaderboard": False,
    "alerting": False,
}


def _resolve_plugins_dir(override: Path | None) -> Path:
    """Resolve the host-bucket root, mirroring the sessions-dir logic."""
    if override is not None:
        return Path(override).expanduser()
    env = os.environ.get("SPONSIO_PLUGINS_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".sponsio" / "plugins"


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


# ---------------------------------------------------------------------------
# Contract catalog — patterns from the OSS library + optional sponsio.yaml.
# ---------------------------------------------------------------------------


# Imported at first call to avoid pulling the formula AST at module load
# (the dashboard is happy to start without sponsio.patterns parsed).
_PATTERN_RETURN_TYPES = {"DetFormula", "AnnotatedFormula", "Formula"}


def _list_patterns() -> list[dict[str, Any]]:
    """Enumerate deterministic pattern factories in ``sponsio.patterns.library``.

    Includes any module-level callable whose return annotation looks
    like a Sponsio formula type. Returns the parameter names (minus the
    boilerplate ``desc``) and the first paragraph of the docstring.
    """
    from sponsio.patterns import library

    out: list[dict[str, Any]] = []
    for name, fn in inspect.getmembers(library, inspect.isfunction):
        if name.startswith("_"):
            continue
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            continue
        ret = sig.return_annotation
        ret_name = getattr(ret, "__name__", str(ret))
        if ret_name not in _PATTERN_RETURN_TYPES:
            continue
        params = [p.name for p in sig.parameters.values() if p.name != "desc"]
        doc = (fn.__doc__ or "").strip()
        # First paragraph only — keep the catalog scannable.
        summary = doc.split("\n\n", 1)[0].replace("\n", " ").strip()
        out.append(
            {
                "name": name,
                "params": params,
                "summary": summary,
                "kind": "det",
            }
        )
    return sorted(out, key=lambda p: p["name"])


def _find_sponsio_yaml() -> Path | None:
    """Find a sponsio.yaml in CWD or via ``SPONSIO_CONFIG``."""
    env = os.environ.get("SPONSIO_CONFIG")
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None
    cwd = Path.cwd() / "sponsio.yaml"
    return cwd if cwd.is_file() else None


def _load_yaml_contracts(path: Path) -> dict[str, Any]:
    """Parse a sponsio.yaml. Returns ``{path, contracts: [...]}`` or an
    error stub if PyYAML isn't installed or parsing fails — never raises.
    """
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return {"path": str(path), "error": "pyyaml not installed", "contracts": []}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return {"path": str(path), "error": str(exc), "contracts": []}
    contracts = data.get("contracts") or []
    if not isinstance(contracts, list):
        contracts = []
    return {"path": str(path), "contracts": contracts}


# ---------------------------------------------------------------------------
# Host buckets — `~/.sponsio/plugins/<bucket>/conv-*.shield-trace.jsonl`.
# ---------------------------------------------------------------------------


def _list_host_buckets(plugins_dir: Path) -> list[dict[str, Any]]:
    """List subdirectories of ``plugins_dir`` that look like host buckets."""
    if not plugins_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(plugins_dir.iterdir()):
        if not child.is_dir():
            continue
        convs = list(child.glob("conv-*.shield-trace.jsonl"))
        latest = max((p.stat().st_mtime for p in convs), default=0.0)
        out.append(
            {
                "name": child.name,
                "conv_count": len(convs),
                "latest_mtime": latest,
                "has_yaml": (child / "sponsio.yaml").is_file(),
            }
        )
    return out


def _list_bucket_events(
    plugins_dir: Path, bucket: str, limit: int
) -> list[dict[str, Any]]:
    """Return up to ``limit`` most-recent events from one host bucket.

    Reads every ``conv-*.shield-trace.jsonl`` under the bucket, parses
    each line as JSON, sorts by the ``ts`` field if present, and returns
    the tail. Malformed lines are dropped.
    """
    bucket_dir = safe_join_segment(plugins_dir, bucket)
    if not bucket_dir.is_dir():
        raise FileNotFoundError(bucket)
    events: list[dict[str, Any]] = []
    for path in bucket_dir.glob("conv-*.shield-trace.jsonl"):
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    rec.setdefault("_conv", path.stem)
                    events.append(rec)
        except OSError:
            continue
    events.sort(key=lambda e: e.get("ts", 0.0))
    return events[-limit:] if limit > 0 else events


# ---------------------------------------------------------------------------
# Live tail — poll session logs and push new events over WebSocket.
# ---------------------------------------------------------------------------


def _snapshot_offsets(sessions_dir: Path) -> dict[Path, int]:
    """Record current EOF byte offset for every session JSONL file."""
    snap: dict[Path, int] = {}
    if not sessions_dir.exists():
        return snap
    for path in sessions_dir.rglob("*.jsonl"):
        try:
            snap[path] = path.stat().st_size
        except OSError:
            continue
    return snap


def _read_new_lines(
    sessions_dir: Path, offsets: dict[Path, int]
) -> list[dict[str, Any]]:
    """Compare current sizes to ``offsets``; emit any new event records.

    Mutates ``offsets`` in place — moves each known cursor forward and
    inserts entries for newly-discovered files (starting at offset 0).
    Files that disappear (rotation) are dropped from the cursor map.
    """
    out: list[dict[str, Any]] = []
    if not sessions_dir.exists():
        return out
    seen: set[Path] = set()
    for path in sessions_dir.rglob("*.jsonl"):
        seen.add(path)
        try:
            size = path.stat().st_size
        except OSError:
            continue
        prev = offsets.get(path, 0)
        if size <= prev:
            offsets[path] = size  # truncated/rotated → reset
            continue
        try:
            with path.open(encoding="utf-8") as f:
                f.seek(prev)
                chunk = f.read(size - prev)
        except OSError:
            continue
        offsets[path] = size
        # Only emit complete (newline-terminated) lines; a partial
        # trailing line will be re-read with more bytes next tick.
        if "\n" not in chunk:
            offsets[path] = prev
            continue
        complete, _, tail = chunk.rpartition("\n")
        if tail:
            offsets[path] = size - len(tail.encode("utf-8"))
        agent_id = path.parent.name
        for line in complete.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec.setdefault("_agent_id", agent_id)
            rec.setdefault("_trace_id", path.stem)
            out.append(rec)
    # Drop cursors for files that vanished (rotated).
    for stale in list(offsets.keys() - seen):
        offsets.pop(stale, None)
    return out


def create_app(
    sessions_dir: Path | None = None,
    plugins_dir: Path | None = None,
    poll_interval: float = 0.5,
) -> "FastAPI":
    """Build the local dashboard FastAPI app.

    Args:
        sessions_dir: Override the session log root. Tests pass a
            ``tmp_path``; production reads ``SPONSIO_SESSIONS_DIR`` or
            falls back to ``~/.sponsio/sessions``.
        plugins_dir: Override the host-bucket root. Defaults to
            ``SPONSIO_PLUGINS_DIR`` env or ``~/.sponsio/plugins``.
        poll_interval: Seconds between WS live-tail polls. Tests pass a
            small value; production uses 500ms.

    Raises:
        ImportError: If the ``[web]`` extra is not installed. The CLI
            wrapper translates this into a friendly hint.
    """
    try:
        from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
    except ImportError as exc:
        raise ImportError(
            "sponsio serve requires the [web] extra. "
            "Install with: pip install 'sponsio[web]'"
        ) from exc

    resolved = _resolve_sessions_dir(sessions_dir)
    plugins = _resolve_plugins_dir(plugins_dir)
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

    @app.get("/api/contracts")
    def contracts() -> dict[str, Any]:
        yaml_path = _find_sponsio_yaml()
        return {
            "patterns": _list_patterns(),
            "yaml": _load_yaml_contracts(yaml_path) if yaml_path else None,
        }

    @app.get("/api/host/buckets")
    def host_buckets() -> dict[str, Any]:
        return {
            "plugins_dir": str(plugins),
            "buckets": _list_host_buckets(plugins),
        }

    @app.get("/api/host/buckets/{bucket}/events")
    def host_bucket_events(bucket: str, limit: int = 200) -> dict[str, Any]:
        try:
            events = _list_bucket_events(plugins, bucket, limit)
        except PathEscapeError as exc:
            raise HTTPException(status_code=400, detail="invalid bucket") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="bucket not found") from exc
        return {"bucket": bucket, "events": events}

    @app.websocket("/api/live")
    async def live(ws: WebSocket) -> None:
        await ws.accept()
        offsets = _snapshot_offsets(resolved)
        try:
            await ws.send_json({"type": "ready", "sessions_dir": str(resolved)})
            while True:
                await asyncio.sleep(poll_interval)
                new_events = _read_new_lines(resolved, offsets)
                for ev in new_events:
                    await ws.send_json({"type": "event", "data": ev})
        except WebSocketDisconnect:
            return

    return app

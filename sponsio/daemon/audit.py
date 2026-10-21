"""The daemon's audit log: one JSON record per line, append only.

``<state_dir>/audit.jsonl`` holds what the daemon did and what it saw
done to it: starts and restarts (this phase), later config drift,
trace deletion, admin calls, tamper attempts (P0.7 adds the hash
chain). Records carry ``ts`` (UTC, ISO 8601), ``event`` (the kind),
``pid``, and the kind's own fields.

Writing is best effort: a full disk must not stop the daemon from
deciding, so an ``OSError`` here is logged and swallowed.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
from pathlib import Path
from typing import Any

_logger = logging.getLogger("sponsio.daemon.audit")

AUDIT_FILE = "audit.jsonl"

DAEMON_START = "daemon_start"
"""A daemon came up with no prior instance on record."""
DAEMON_RESTART = "daemon_restart"
"""A daemon came up after a prior instance went away without stopping
cleanly (its pid was still in the lock file and is not alive)."""
DAEMON_STOP = "daemon_stop"


def audit_path(state_dir: Path) -> Path:
    return Path(state_dir) / AUDIT_FILE


def append(state_dir: Path, event: str, **fields: Any) -> dict[str, Any] | None:
    """Append one record. Returns it, or ``None`` when it could not be
    written."""
    record: dict[str, Any] = {
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds"),
        "event": event,
        "pid": os.getpid(),
    }
    record.update(fields)
    path = audit_path(state_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, separators=(",", ":"), default=str) + "\n"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except OSError as e:
        _logger.warning("could not write audit record %s: %s", event, e)
        return None
    return record


def read(state_dir: Path, event: str | None = None) -> list[dict[str, Any]]:
    """Every record, oldest first, optionally of one kind."""
    path = audit_path(state_dir)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if isinstance(rec, dict) and (event is None or rec.get("event") == event):
            out.append(rec)
    return out


def pid_alive(pid: int) -> bool:
    """Is a process with ``pid`` running (as far as this user can tell)?"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

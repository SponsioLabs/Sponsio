"""Append-only JSONL session logger for shadow mode.

Shadow mode (``mode="observe"``) captures every monitor event to a
per-session JSONL file so users can see how their agent would behave
under a set of contracts *without* blocking anything.

The logger is a callable that plugs into ``RuntimeMonitor.register_callback``.
Each :class:`~sponsio.runtime.monitor.MonitorEvent` is serialized to one
JSON line and appended to::

    ~/.sponsio/sessions/{agent_id}/{YYYYMMDD_HHMMSS}.jsonl

Rotation keeps the directory small:

* Files older than ``keep_days`` days are pruned on startup.
* If total size exceeds ``max_mb`` megabytes after pruning, the oldest
  files are deleted until the budget is met.

The logger has no external dependencies — it uses only stdlib.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sponsio.runtime.monitor import MonitorEvent


DEFAULT_BASE_DIR = Path.home() / ".sponsio" / "sessions"
DEFAULT_KEEP_DAYS = 7
DEFAULT_MAX_MB = 100


def default_session_dir(agent_id: str, base_dir: Path | None = None) -> Path:
    """Return the per-agent session directory, creating it if needed."""
    base = base_dir if base_dir is not None else DEFAULT_BASE_DIR
    d = base / agent_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def rotate_sessions(
    base_dir: Path,
    keep_days: int = DEFAULT_KEEP_DAYS,
    max_mb: int = DEFAULT_MAX_MB,
) -> list[Path]:
    """Prune old or oversized session files in ``base_dir``.

    Walks every ``*.jsonl`` file under ``base_dir`` (recursively) and
    removes:

    * Files whose ``mtime`` is older than ``keep_days`` days.
    * The oldest remaining files until total size is at or below
      ``max_mb`` megabytes.

    Silently skips errors (stale NFS handles, perms, etc.) since the
    logger must never break the agent.

    Returns:
        Paths of files that were removed.
    """
    removed: list[Path] = []
    if not base_dir.exists():
        return removed

    now = time.time()
    cutoff = now - keep_days * 86400

    files: list[tuple[Path, float, int]] = []  # (path, mtime, size)
    try:
        for path in base_dir.rglob("*.jsonl"):
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff:
                try:
                    path.unlink()
                    removed.append(path)
                except OSError:
                    pass
                continue
            files.append((path, st.st_mtime, st.st_size))
    except OSError:
        return removed

    # Size-based rotation: drop oldest until under budget.
    budget = max_mb * 1024 * 1024
    total = sum(sz for _, _, sz in files)
    if total > budget:
        files.sort(key=lambda x: x[1])  # oldest first
        for path, _, size in files:
            if total <= budget:
                break
            try:
                path.unlink()
                removed.append(path)
                total -= size
            except OSError:
                pass

    return removed


class SessionLogger:
    """Append-only JSONL logger for monitor events.

    Instances are callable and conform to the
    :class:`~sponsio.runtime.monitor.RuntimeMonitor` callback signature::

        logger = SessionLogger(agent_id="bot")
        monitor.register_callback(logger)

    Each call appends one JSON line with the event.

    Args:
        agent_id: Logical agent id — used in the path.
        base_dir: Override the base directory (``~/.sponsio/sessions``).
            Tests typically point this at ``tmp_path``.
        keep_days: Rotate files older than this (default 7).
        max_mb: Rotate files when total size exceeds this (default 100).
        timestamp: Fixed timestamp for filename (testing only).

    Attributes:
        path: The JSONL file being appended to.
    """

    def __init__(
        self,
        agent_id: str,
        base_dir: Path | None = None,
        keep_days: int = DEFAULT_KEEP_DAYS,
        max_mb: int = DEFAULT_MAX_MB,
        timestamp: str | None = None,
    ) -> None:
        self.agent_id = agent_id
        self._dir = default_session_dir(agent_id, base_dir=base_dir)

        # Run rotation once on startup. Cheap for small directories.
        parent = self._dir.parent
        rotate_sessions(parent, keep_days=keep_days, max_mb=max_mb)

        ts = timestamp or time.strftime("%Y%m%d_%H%M%S")
        # Tie-break with pid so two guards in the same second don't
        # clobber one another.
        fname = f"{ts}_{os.getpid()}.jsonl"
        self.path = self._dir / fname

    # ---- Callback interface ----

    def __call__(self, event: "MonitorEvent") -> None:
        try:
            record = self._serialize(event)
            line = json.dumps(record, default=str)
        except Exception:
            # Never break the agent because of log serialization.
            return
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            # Disk full, perms, etc. — silently drop.
            return

    # ---- Helpers ----

    @staticmethod
    def _serialize(event: "MonitorEvent") -> dict:
        """Convert a MonitorEvent into a JSON-serializable record."""
        rec: dict = {
            "ts": time.time(),
            "agent_id": event.agent_id,
            "action": event.action,
            "pipeline": event.pipeline,
            "constraint": event.constraint_name,
            "result": {
                "action": event.result.action,
                "message": event.result.message,
            },
        }
        if event.result.retry_prompt:
            rec["result"]["retry_prompt"] = event.result.retry_prompt
        if event.sto_result is not None:
            try:
                rec["sto"] = {
                    "score": event.sto_result.score,
                    "evidence": event.sto_result.evidence,
                }
            except Exception:
                try:
                    rec["sto"] = asdict(event.sto_result)
                except Exception:
                    pass
        return rec

"""JSONL reader for shadow-mode session logs.

Discovers and parses files written by
:class:`sponsio.runtime.session_log.SessionLogger`.  The reader is a
generator; callers can stream through millions of events without
loading them all into memory.

Record schema (produced by ``SessionLogger._serialize``)::

    {
      "ts": 1713456789.123,          # unix seconds
      "agent_id": "support_bot",
      "action": "issue_refund",
      "pipeline": "det" | "sto",
      "constraint": "...",
      "result": {
        "action": "blocked" | "allowed" | "warned" | "observed" | "retrying" | ...,
        "message": "...",
        "retry_prompt": "..."        # optional
      },
      "sto": {                        # optional, sto pipeline only
        "score": 0.73,
        "evidence": "..."
      }
    }

Malformed lines are skipped silently — one corrupt record must not
poison a whole report.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from sponsio.runtime.session_log import DEFAULT_BASE_DIR


# ---------------------------------------------------------------------------
# Time window parsing
# ---------------------------------------------------------------------------


_SINCE_RE = re.compile(r"^(\d+)([smhd])$")


def parse_since(spec: str, now: float | None = None) -> float:
    """Return the unix-ts lower bound for a ``--since`` flag value.

    Accepts ``all``, ``30s``, ``45m``, ``24h``, ``7d``.  Returns ``0.0``
    for ``all`` (i.e. no lower bound).  Raises ``ValueError`` on
    malformed input.

    Args:
        spec: The flag value, e.g. ``"24h"`` or ``"all"``.
        now: Reference "now" timestamp; defaults to ``time.time()``.
    """
    if spec is None or spec == "" or spec.lower() == "all":
        return 0.0
    m = _SINCE_RE.match(spec.strip().lower())
    if not m:
        raise ValueError(
            f"Invalid --since value: {spec!r}. "
            f"Expected 'all' or a duration like '30m', '24h', '7d'."
        )
    n, unit = int(m.group(1)), m.group(2)
    seconds = n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return (now if now is not None else time.time()) - seconds


# ---------------------------------------------------------------------------
# Event dataclass
# ---------------------------------------------------------------------------


@dataclass
class SessionEvent:
    """One decoded record from a session JSONL file.

    Wraps the raw dict with typed accessors so downstream code doesn't
    repeatedly dig through nested ``result.*`` keys.
    """

    ts: float
    agent_id: str
    action: str
    pipeline: str  # "det" or "sto"
    constraint: str
    result_action: str  # "blocked" | "observed" | "retrying" | "allowed" | ...
    result_message: str
    sto_score: float | None = None
    sto_evidence: str | None = None
    source_file: Path | None = None  # for `--live` dedup; not rendered

    @property
    def is_violation(self) -> bool:
        """True if this event represents a caught or would-have-caught violation."""
        return self.result_action in {"blocked", "observed", "retrying", "escalated"}

    @property
    def is_observed(self) -> bool:
        return self.result_action == "observed"

    @property
    def is_blocked(self) -> bool:
        return self.result_action == "blocked"

    @property
    def is_retrying(self) -> bool:
        return self.result_action == "retrying"

    @property
    def is_pass(self) -> bool:
        return self.result_action in {"allowed", "warned"}


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _iter_session_files(base_dir: Path, agent: str | None = None) -> Iterator[Path]:
    """Yield JSONL files under ``base_dir``, optionally filtered by agent."""
    if not base_dir.exists():
        return
    if agent is not None:
        agent_dir = base_dir / agent
        if not agent_dir.exists():
            return
        yield from sorted(agent_dir.glob("*.jsonl"))
    else:
        yield from sorted(base_dir.rglob("*.jsonl"))


def _parse_line(line: str, source_file: Path | None = None) -> SessionEvent | None:
    """Decode one JSONL line into a SessionEvent, or None if malformed."""
    try:
        rec = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(rec, dict):
        return None
    result = rec.get("result") or {}
    if not isinstance(result, dict):
        result = {}
    sto = rec.get("sto") or {}
    if not isinstance(sto, dict):
        sto = {}
    try:
        ts = float(rec.get("ts", 0.0))
    except (TypeError, ValueError):
        ts = 0.0
    return SessionEvent(
        ts=ts,
        agent_id=str(rec.get("agent_id", "")),
        action=str(rec.get("action", "")),
        pipeline=str(rec.get("pipeline", "")),
        constraint=str(rec.get("constraint", "")),
        result_action=str(result.get("action", "")),
        result_message=str(result.get("message", "")),
        sto_score=sto.get("score") if "score" in sto else None,
        sto_evidence=str(sto.get("evidence")) if sto.get("evidence") else None,
        source_file=source_file,
    )


def load_events(
    since: str = "all",
    agent: str | None = None,
    base_dir: Path | None = None,
    now: float | None = None,
) -> Iterator[SessionEvent]:
    """Stream decoded events from the shadow-mode session log.

    Args:
        since: Time window (``"all"`` / ``"30m"`` / ``"24h"`` / ``"7d"``).
        agent: If given, only files under ``<base_dir>/<agent>/`` are read.
        base_dir: Override the default session directory
            (``~/.sponsio/sessions``).  Tests point this at ``tmp_path``.
        now: Reference timestamp for ``since`` calculation.

    Yields:
        ``SessionEvent`` instances ordered by discovery (per-file order
        is preserved; files themselves are sorted alphabetically, which
        is chronologically-sorted-enough for the ``YYYYMMDD_HHMMSS_PID``
        naming scheme).

    Malformed or unreadable files / lines are skipped silently — the
    report must never crash on one bad record.
    """
    bd = base_dir if base_dir is not None else DEFAULT_BASE_DIR
    cutoff = parse_since(since, now=now)
    for fp in _iter_session_files(bd, agent=agent):
        try:
            with fp.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    ev = _parse_line(line, source_file=fp)
                    if ev is None:
                        continue
                    if ev.ts < cutoff:
                        continue
                    yield ev
        except OSError:
            continue

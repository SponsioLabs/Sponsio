"""Reconstruct a session view from a recorded ``*.jsonl`` log.

The session view (``sponsio/render/session_view.py``) renders a tree of
``AgentTurnSpan`` objects — that's what ``RuntimeMonitor.turn_spans``
produces live. The session log on disk, however, is a flat sequence of
``MonitorEvent`` records (one JSON object per line). To replay, we
synthesise a span tree from the flat events and feed it into the same
renderer — same visual layout, no live agent required.

V1 scope: deterministic replay only (re-render the original verdict).
``--step``, ``--with``, and ``--diff`` are deferred — the replay engine
in ``sponsio.discovery.trace_replay`` already exists for the
verifier-driven scoring case (``sponsio eval``); this module is the
visual analog focused on debugging / sharing.

OSS — Cloud's replay overlay adds fleet replay (same session ID across
customers) and statistical regression diff.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sponsio.render.derive import short_session_id


def find_session_file(
    query: str, sessions_dir: Path | None = None
) -> tuple[Path | None, str | None]:
    """Resolve ``query`` to an on-disk session log file.

    Resolution precedence:
      1. If ``query`` is a path that exists, return it verbatim.
      2. If ``query`` matches the ``short_session_id`` hash of a file
         under ``sessions_dir``, return that file.
      3. If ``query`` matches the filename stem (e.g.
         ``20260501_120000_999``), return that file.

    Returns ``(path, agent_id)``. ``agent_id`` is derived from the
    parent directory name (sessions are stored as
    ``<base>/<agent_id>/<stem>.jsonl``).
    """
    if not query:
        return None, None

    direct = Path(query).expanduser()
    if direct.is_file():
        return direct, direct.parent.name

    if sessions_dir is None:
        from sponsio.runtime.session_log import _resolve_default_base_dir

        sessions_dir = _resolve_default_base_dir()
    if not sessions_dir.exists():
        return None, None

    for path in sorted(sessions_dir.rglob("*.jsonl")):
        if short_session_id(path.stem) == query or path.stem == query:
            return path, path.parent.name
    return None, None


def list_sessions(sessions_dir: Path | None = None) -> list[dict[str, Any]]:
    """List all sessions in ``sessions_dir`` with their derived short IDs.

    Used by the CLI when no match is found — surface the catalog so
    the user can copy a known short ID instead of guessing.
    """
    if sessions_dir is None:
        from sponsio.runtime.session_log import _resolve_default_base_dir

        sessions_dir = _resolve_default_base_dir()
    if not sessions_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(sessions_dir.rglob("*.jsonl")):
        try:
            stat = path.stat()
        except OSError:
            continue
        out.append(
            {
                "session_id": short_session_id(path.stem),
                "agent_id": path.parent.name,
                "path": str(path),
                "stem": path.stem,
                "size_bytes": stat.st_size,
                "mtime": stat.st_mtime,
            }
        )
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# Flat MonitorEvent records → synthetic span tree.
# ---------------------------------------------------------------------------


def _event_iter(path: Path):
    """Yield parsed JSONL records, skipping malformed lines."""
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _strip_assumption_prefix(name: str) -> tuple[str, bool]:
    """Strip the ``assumption:`` prefix the runtime adds to constraint
    names so we can route the event through the right span subtype."""
    prefix = "assumption: "
    if name.startswith(prefix):
        return name.removeprefix(prefix), True
    return name, False


# Tightness window for grouping consecutive events into one
# AgentTurnSpan. The verifier emits all checks for one tool call within
# microseconds of each other; conservative 50ms catches even slow CI
# replays without merging genuinely separate turns.
_TURN_GROUP_WINDOW_S = 0.050

# Result actions that mean "no violation" — the live monitor uses both
# spellings depending on which strategy ran. Anything outside this set
# (blocked / escalated / retrying / observed / etc.) renders as a
# violation in the trace tree.
_ALLOW_ACTIONS: frozenset[str] = frozenset({"allow", "allowed"})


def reconstruct_turn_spans(events: list[dict]) -> list:
    """Build a synthetic ``AgentTurnSpan`` tree from flat MonitorEvent records.

    One AgentTurnSpan covers a contiguous run of events that share the
    same ``action`` and fall within ``_TURN_GROUP_WINDOW_S`` of each
    other. Each MonitorEvent under a turn becomes one
    ContractCheckSpan; the check's children are synthesised based on
    whether the constraint name carries the ``assumption:`` prefix
    (→ PreconditionSpan) or not (→ GuaranteeSpan, with ViolationSpan +
    EnforcementSpan children when the result indicates a violation).

    The synthesis is intentionally lossy:

    * Latency in the source jsonl is wall-clock ``ts``, not span
      duration. We approximate ``duration_ms`` as the gap to the next
      event in the same turn (or 0 for the last). Good enough for
      rendering — a true microsecond span recording would need richer
      jsonl, which is a Phase 6+ schema discussion.
    * The pipeline label maps ``"det"`` → ``"hard"`` to match the live
      span vocabulary the renderer already understands.
    """
    # Local imports keep the module load-time cost down — the span
    # types pull in time / dataclass machinery we don't need just to
    # call list_sessions / find_session_file.
    from sponsio.models.spans import (
        AgentTurnSpan,
        ContractCheckSpan,
        EnforcementSpan,
        GuaranteeSpan,
        PreconditionSpan,
        ViolationSpan,
    )

    turns: list = []
    if not events:
        return turns

    current: AgentTurnSpan | None = None
    last_ts: float = 0.0
    for ev in events:
        ts = float(ev.get("ts") or 0)
        action = str(ev.get("action") or "<unknown>")
        agent_id = str(ev.get("agent_id") or "")
        constraint_raw = str(ev.get("constraint") or "")
        constraint, is_assume = _strip_assumption_prefix(constraint_raw)
        result = ev.get("result") or {}
        result_action = str(result.get("action") or "")
        pipeline_in = str(ev.get("pipeline") or "det")
        # session_view's renderer treats "hard" and anything-else as
        # det-style. The live verifier emits "hard" for legacy reasons.
        pipeline = "sto" if pipeline_in == "sto" else "hard"

        # Decide whether to start a new turn.
        is_new_turn = (
            current is None
            or current.action != action
            or (ts - last_ts) > _TURN_GROUP_WINDOW_S
        )
        if is_new_turn:
            # Close the previous turn's duration estimate.
            if current is not None:
                current.end_time = max(current.end_time or 0, last_ts)
            current = AgentTurnSpan(
                span_type="sponsio.agent_turn",
                start_time=ts,
                end_time=ts,
                agent_id=agent_id,
                action=action,
            )
            turns.append(current)

        # Build the per-event ContractCheckSpan.
        check = ContractCheckSpan(
            span_type="sponsio.contract_check",
            start_time=ts,
            end_time=ts,
            contract_name=constraint or "(unnamed)",
            pipeline=pipeline,
        )
        if is_assume:
            # Assume satisfied iff the runtime didn't escalate. The
            # session log doesn't carry a separate "satisfied?" boolean
            # for assumes, but the convention is: action ∈ {"allow",
            # "allowed"} for a newly-fired satisfied assumption,
            # action=="escalated" for a still-dormant one (matches the
            # logic terminal.py / monitor.py use).
            satisfied = result_action in _ALLOW_ACTIONS
            check.children.append(
                PreconditionSpan(
                    span_type="sponsio.precondition",
                    start_time=ts,
                    end_time=ts,
                    formula_desc=constraint,
                    result=satisfied,
                )
            )
        else:
            holds = result_action in _ALLOW_ACTIONS or result_action == ""
            guar = GuaranteeSpan(
                span_type="sponsio.guarantee",
                start_time=ts,
                end_time=ts,
                formula_desc=constraint,
                result=holds,
                status="ok" if holds else "violated",
            )
            if not holds:
                guar.children.append(
                    ViolationSpan(
                        span_type="sponsio.violation",
                        start_time=ts,
                        end_time=ts,
                        kind="guarantee",
                        evidence=str(result.get("message") or ""),
                    )
                )
                guar.children.append(
                    EnforcementSpan(
                        span_type="sponsio.enforcement",
                        start_time=ts,
                        end_time=ts,
                        strategy="DetBlock",
                        result_action=result_action or "blocked",
                    )
                )
            check.children.append(guar)

        current.children.append(check)
        last_ts = ts

    # Final turn duration estimate.
    if current is not None:
        current.end_time = max(current.end_time or 0, last_ts)

    return turns


def load_replay(path: Path) -> tuple[list, str | None]:
    """Read the jsonl at ``path`` and return ``(turn_spans, agent_id)``."""
    events = list(_event_iter(path))
    spans = reconstruct_turn_spans(events)
    agent_id = events[0].get("agent_id") if events else None
    return spans, agent_id

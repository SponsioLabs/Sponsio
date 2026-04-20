"""Tests for shadow mode (``mode="observe"``) and the session logger."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

import sponsio
from sponsio.integrations.base import BaseGuard, _resolve_mode
from sponsio.runtime.monitor import MonitorEvent, RuntimeMonitor
from sponsio.runtime.session_log import (
    DEFAULT_KEEP_DAYS,
    SessionLogger,
    rotate_sessions,
)
from sponsio.runtime.strategies import EnforcementResult


@pytest.fixture(autouse=True)
def _clear_env_mode(monkeypatch):
    """Make sure ``SPONSIO_MODE`` is unset unless a test opts in."""
    monkeypatch.delenv("SPONSIO_MODE", raising=False)


# ---------------------------------------------------------------------------
# _resolve_mode
# ---------------------------------------------------------------------------


def test_resolve_mode_defaults_to_enforce():
    assert _resolve_mode(None) == "enforce"


def test_resolve_mode_passes_through_explicit():
    assert _resolve_mode("observe") == "observe"
    assert _resolve_mode("enforce") == "enforce"


def test_resolve_mode_env_var_overrides(monkeypatch):
    monkeypatch.setenv("SPONSIO_MODE", "observe")
    # Even when caller asked for enforce, env var wins.
    assert _resolve_mode("enforce") == "observe"
    assert _resolve_mode(None) == "observe"


def test_resolve_mode_rejects_unknown():
    with pytest.raises(ValueError, match="mode must be one of"):
        _resolve_mode("lenient")


def test_resolve_mode_rejects_unknown_env_var(monkeypatch):
    monkeypatch.setenv("SPONSIO_MODE", "ghost")
    with pytest.raises(ValueError, match="mode must be one of"):
        _resolve_mode("observe")


# ---------------------------------------------------------------------------
# RuntimeMonitor.mode
# ---------------------------------------------------------------------------


def test_runtime_monitor_rejects_unknown_mode():
    from sponsio.models.system import System

    with pytest.raises(ValueError, match="mode must be"):
        RuntimeMonitor(system=System(name="s"), mode="lenient")


# ---------------------------------------------------------------------------
# Shadow-mode behavior end-to-end
# ---------------------------------------------------------------------------


def _make_guard(tmp_path: Path, mode: str = "observe") -> BaseGuard:
    """Guard with one det rule that is trivially violated on the first call."""
    return BaseGuard(
        agent_id="bot",
        contracts=[
            "tool `issue_refund` at most 0 times",
        ],
        mode=mode,
        session_log_dir=tmp_path,
        verbose=False,
    )


def test_observe_mode_does_not_block(tmp_path):
    guard = _make_guard(tmp_path, mode="observe")
    # In enforce mode this would be blocked immediately (at_most_0).
    result = guard.guard_before("issue_refund")
    assert result.allowed, "observe mode must never block execution"
    assert not result.blocked
    assert not result.rollback_performed
    # Trace is preserved (no rollback).
    assert len(guard.trace.events) == 1


def test_enforce_mode_still_blocks(tmp_path):
    guard = _make_guard(tmp_path, mode="enforce")
    result = guard.guard_before("issue_refund")
    assert not result.allowed
    assert result.blocked
    assert result.rollback_performed


def test_observe_mode_records_would_have_blocked(tmp_path):
    guard = _make_guard(tmp_path, mode="observe")
    guard.guard_before("issue_refund")

    # Violations are still recorded so downstream reporting / sponsio
    # report can surface them.
    observed_actions = [v["action"] for v in guard.violations]
    assert "OBSERVED" in observed_actions


def test_observe_mode_writes_jsonl(tmp_path):
    guard = _make_guard(tmp_path, mode="observe")
    guard.guard_before("issue_refund")

    log_path = guard.session_log_path
    assert log_path is not None
    assert log_path.exists()

    lines = [json.loads(ln) for ln in log_path.read_text().splitlines() if ln]
    assert lines, "session log must contain at least one record"

    actions = {line["result"]["action"] for line in lines}
    # At least one record should be downgraded to observed.
    assert "observed" in actions


def test_enforce_mode_no_jsonl_by_default(tmp_path, monkeypatch):
    # Point XDG_HOME somewhere safe to avoid scribbling on a real $HOME
    # if the test system misbehaves; the session_log_dir=None path takes
    # the default, and we just assert we didn't spin up a logger.
    guard = BaseGuard(
        agent_id="bot",
        contracts=["tool `A` at most 1 times"],
        mode="enforce",
        verbose=False,
    )
    assert guard.session_log_path is None


def test_enforce_mode_with_explicit_log_dir_writes(tmp_path):
    """If the caller insists on a log dir, log in enforce mode too."""
    guard = BaseGuard(
        agent_id="bot",
        contracts=["tool `A` at most 1 times"],
        mode="enforce",
        session_log_dir=tmp_path,
        verbose=False,
    )
    assert guard.session_log_path is not None
    guard.guard_before("A")  # pass
    guard.guard_before("A")  # blocked
    assert guard.session_log_path.exists()


def test_env_var_forces_observe(tmp_path, monkeypatch):
    monkeypatch.setenv("SPONSIO_MODE", "observe")
    guard = BaseGuard(
        agent_id="bot",
        contracts=["tool `A` at most 0 times"],
        session_log_dir=tmp_path,
        verbose=False,
        # Caller asked for enforce, but env var wins.
        mode="enforce",
    )
    assert guard.mode == "observe"
    result = guard.guard_before("A")
    assert result.allowed


def test_observe_preserves_trace_across_multiple_calls(tmp_path):
    guard = _make_guard(tmp_path, mode="observe")
    for _ in range(3):
        guard.guard_before("issue_refund")
    # Nothing rolled back: full three-event trace.
    assert len(guard.trace.events) == 3


# ---------------------------------------------------------------------------
# sponsio.init() wiring
# ---------------------------------------------------------------------------


def test_sponsio_init_accepts_mode(tmp_path):
    guard = sponsio.init(
        agent_id="bot",
        contracts=["tool `X` at most 0 times"],
        mode="observe",
        session_log_dir=tmp_path,
        verbose=False,
    )
    assert guard.mode == "observe"
    assert guard.guard_before("X").allowed


# ---------------------------------------------------------------------------
# SessionLogger unit tests
# ---------------------------------------------------------------------------


def _fake_event(action: str = "blocked") -> MonitorEvent:
    return MonitorEvent(
        agent_id="bot",
        action="tool_x",
        pipeline="det",
        constraint_name="tool `tool_x` at most 0 times",
        result=EnforcementResult(action=action, message="hello"),
    )


def test_session_logger_writes_one_line_per_event(tmp_path):
    logger = SessionLogger(
        agent_id="bot", base_dir=tmp_path, timestamp="20260101_000000"
    )
    logger(_fake_event("blocked"))
    logger(_fake_event("observed"))

    lines = [ln for ln in logger.path.read_text().splitlines() if ln]
    assert len(lines) == 2
    rec0 = json.loads(lines[0])
    assert rec0["pipeline"] == "det"
    assert rec0["result"]["action"] == "blocked"


def test_session_logger_filename_unique_per_pid(tmp_path):
    logger = SessionLogger(
        agent_id="bot", base_dir=tmp_path, timestamp="20260101_000000"
    )
    assert str(os.getpid()) in logger.path.name


def test_session_logger_never_raises_on_bad_event(tmp_path):
    """Malformed event shouldn't crash the agent — logger swallows errors."""
    logger = SessionLogger(
        agent_id="bot", base_dir=tmp_path, timestamp="20260101_000000"
    )
    # Passing a non-MonitorEvent should not raise.
    logger(object())  # type: ignore[arg-type]


def test_rotate_sessions_prunes_old_files(tmp_path):
    # Create a "stale" file whose mtime is DEFAULT_KEEP_DAYS + 1 days ago.
    stale_dir = tmp_path / "bot"
    stale_dir.mkdir()
    stale = stale_dir / "old.jsonl"
    stale.write_text("{}\n")
    old_ts = time.time() - (DEFAULT_KEEP_DAYS + 1) * 86400
    os.utime(stale, (old_ts, old_ts))

    fresh = stale_dir / "new.jsonl"
    fresh.write_text("{}\n")

    removed = rotate_sessions(tmp_path, keep_days=DEFAULT_KEEP_DAYS)
    assert stale in removed
    assert not stale.exists()
    assert fresh.exists()


def test_rotate_sessions_prunes_oversized(tmp_path):
    d = tmp_path / "bot"
    d.mkdir()

    # Two files of ~1 MB each; budget of 1 MB means the oldest goes.
    payload = "x" * (1024 * 1024)
    older = d / "older.jsonl"
    newer = d / "newer.jsonl"
    older.write_text(payload)
    newer.write_text(payload)
    # Give them distinct mtimes.
    os.utime(older, (time.time() - 10, time.time() - 10))

    removed = rotate_sessions(tmp_path, keep_days=365, max_mb=1)
    assert older in removed
    assert not older.exists()
    assert newer.exists()

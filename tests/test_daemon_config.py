"""The daemon decides from daemon.yaml, never from an environment variable.

A hook's environment comes from the host's settings, which the agent
under guard can edit; ``SPONSIO_GUARD_MODE=observe`` in Claude Code's
``env`` block used to turn the guard into a logger (corpus V5). Every
knob a hook decision depends on is now in ``<state_dir>/daemon.yaml``,
changed through the admin socket, and a mode sent with the request is
ignored.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path

import pytest

from sponsio.daemon import audit
from sponsio.daemon import config as dc
from sponsio.daemon.client import DaemonClient
from sponsio.daemon.guard_service import FAIL_OPEN, GuardService
from sponsio.daemon.handlers import register_default_handlers
from sponsio.daemon.server import DaemonServer
from sponsio.integrations.hosts import set_daemon_mode

BUCKET = "_host_claude_code"
LIBRARY = """
version: "1"
agents:
  {agent}:
    contracts:
      - desc: "no rm -rf"
        G:
          pattern: arg_blacklist
          args: [Bash, command, ["rm\\\\s+-rf"]]
"""


@pytest.fixture
def tmp(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="spc-", dir="/tmp"))
    for root in ("plugins", "other"):
        lib = d / root / BUCKET / "sponsio.yaml"
        lib.parent.mkdir(parents=True)
        lib.write_text(LIBRARY.format(agent=BUCKET))
    (d / "state").mkdir()
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _write(tmp: Path, text: str) -> None:
    (tmp / "state" / "daemon.yaml").write_text(text)


def _svc(tmp: Path) -> GuardService:
    return GuardService(root=tmp / "plugins", state_dir=tmp / "state")


def _rm(**extra) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /tmp/x"},
        "session_id": "s1",
        **extra,
    }


# ---------------------------------------------------------------------------
# The file
# ---------------------------------------------------------------------------


def test_no_file_means_the_strict_defaults(tmp):
    cfg = dc.load(tmp / "state")
    assert (cfg.mode, cfg.on_error, cfg.unconfigured) == ("enforce", "deny", None)


@pytest.mark.parametrize(
    "text",
    [
        "mode: [not, a, mode\n",  # does not parse
        "mode: off\non_error: maybe\ntrace_ttl_hours: -1\nhosts: [x]\n",  # invalid
        "- just\n- a list\n",  # not a mapping
    ],
)
def test_anything_invalid_takes_the_strict_default(tmp, text):
    _write(tmp, text)
    cfg = dc.load(tmp / "state")
    assert (cfg.mode, cfg.on_error, cfg.hosts, cfg.trace_ttl_hours) == (
        "enforce",
        "deny",
        {},
        24.0,
    )


def test_set_host_mode_round_trips_and_is_private(tmp):
    dc.set_host_mode(tmp / "state", "cursor", "observe")
    assert dc.load(tmp / "state").mode_for("cursor") == "observe"
    assert dc.load(tmp / "state").mode_for("claude-code") == "enforce"
    path = dc.config_path(tmp / "state")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # Back to the default: the host is no longer listed.
    dc.set_host_mode(tmp / "state", "cursor", "enforce")
    assert dc.load(tmp / "state").hosts == {}


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------

ENV_KNOBS = {
    "SPONSIO_GUARD_MODE": "observe",
    "SPONSIO_MODE": "observe",
    "SPONSIO_HOOK_ON_ERROR": "allow",
    "SPONSIO_UNCONFIGURED": "allow",
    "SPONSIO_SHIELD_TRACE_TTL_HOURS": "0.0001",
}


def test_no_environment_variable_reaches_the_decision(tmp, monkeypatch):
    svc = _svc(tmp)
    for k, v in ENV_KNOBS.items():
        monkeypatch.setenv(k, v)
    assert svc.check(host="claude-code", payload=_rm())["allowed"] is False


def test_a_mode_sent_with_the_request_is_ignored(tmp):
    svc = _svc(tmp)
    assert (
        svc.check(host="claude-code", payload=_rm(), mode="observe")["allowed"] is False
    )


def test_observe_in_the_config_lets_the_call_through(tmp):
    _write(tmp, "hosts:\n  claude-code: {mode: observe}\n")
    svc = _svc(tmp)
    assert svc.check(host="claude-code", payload=_rm())["allowed"] is True
    # Only for that host: Cursor still enforces.
    cursor = {"command": "rm -rf /tmp/x", "conversation_id": "c"}
    lib = tmp / "plugins" / "_host_cursor" / "sponsio.yaml"
    lib.parent.mkdir()
    lib.write_text(LIBRARY.format(agent="_host_cursor"))
    res = svc.check(host="cursor", hook_event="beforeShellExecution", payload=cursor)
    assert res["allowed"] is False


def test_a_config_change_applies_to_the_next_call(tmp):
    svc = _svc(tmp)
    assert svc.check(host="claude-code", payload=_rm())["allowed"] is False
    _write(tmp, "mode: observe\n")
    assert svc.check(host="claude-code", payload=_rm())["allowed"] is True


def test_on_error_allow_comes_from_the_config_and_is_audited(tmp):
    (tmp / "plugins" / BUCKET / "sponsio.yaml").write_text("agents: [broken\n")
    svc = _svc(tmp)
    assert svc.check(host="claude-code", payload=_rm())["allowed"] is False
    _write(tmp, "on_error: allow\n")
    assert svc.check(host="claude-code", payload=_rm())["allowed"] is True
    assert len(audit.read(tmp / "state", FAIL_OPEN)) == 1


def test_unconfigured_deny_comes_from_the_config(tmp):
    svc = _svc(tmp)
    mcp = {**_rm(), "tool_name": "mcp__nolib__do"}
    assert svc.check(host="claude-code", payload=mcp)["allowed"] is True
    _write(tmp, "unconfigured: deny\n")
    assert svc.check(host="claude-code", payload=mcp)["allowed"] is False


def test_the_configured_plugin_root_wins(tmp):
    (tmp / "other" / BUCKET / "sponsio.yaml").write_text(
        'version: "1"\nagents:\n  _host_claude_code:\n    contracts: []\n'
    )
    svc = _svc(tmp)
    assert svc.check(host="claude-code", payload=_rm())["allowed"] is False
    _write(tmp, f"plugin_root: {tmp / 'other'}\n")
    assert svc.check(host="claude-code", payload=_rm())["allowed"] is True


# ---------------------------------------------------------------------------
# How the mode gets there
# ---------------------------------------------------------------------------


def test_set_daemon_mode_goes_through_the_admin_socket_when_a_daemon_runs(tmp):
    svc = _svc(tmp)
    server = DaemonServer(tmp / "sock", accept_timeout_s=0.05)
    register_default_handlers(server, guard_service=svc)
    server.start()
    try:
        how = set_daemon_mode(
            "claude-code",
            "observe",
            state_dir=tmp / "nowhere",
            socket_path=tmp / "sock",
        )
        assert "through the daemon" in how
        agent = DaemonClient(socket_path=server.socket_path)
        res = agent.call("guard.check", {"host": "claude-code", "payload": _rm()})
        assert res["allowed"] is True
    finally:
        server.stop(join_timeout_s=0.5)
    # Persisted in the daemon's own state dir, and audited with the caller.
    assert dc.load(tmp / "state").mode_for("claude-code") == "observe"
    assert not (tmp / "nowhere").exists()
    (rec,) = audit.read(tmp / "state", "admin_call")
    assert rec["method"] == "mode.set" and rec["peer"]["uid"] == os.getuid()


def test_set_daemon_mode_writes_the_file_when_no_daemon_runs(tmp):
    how = set_daemon_mode(
        "cursor", "observe", state_dir=tmp / "state", socket_path=tmp / "sock"
    )
    assert "written to" in how
    assert dc.load(tmp / "state").mode_for("cursor") == "observe"

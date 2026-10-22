"""The hook shim: stdlib only, forwards to the daemon, fails closed.

Every installed hook now runs ``python -I -S sponsio_shim.py``. These
tests run the real script as a subprocess the way a host would, against
a daemon the shim itself starts, and check the three things the plan
asks of it: the daemon's verdict reaches the host unchanged, a daemon
that cannot be reached is a deny with exit 2 inside the deadline, and
eight shims that find no daemon at once end with one daemon and the
same verdict for all of them.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from sponsio.daemon import audit
from sponsio.daemon.client import daemon_is_running
from sponsio.shim import shim_path
from sponsio.shim.sponsio_shim import classify, deny_reply, parse_args

BUCKET = "_host_claude_code"

LIBRARY = f"""
version: "1"
agents:
  {BUCKET}:
    contracts:
      - desc: "no rm -rf"
        G:
          pattern: arg_blacklist
          args: [Bash, command, ["rm\\\\s+-rf"]]
  _host_cursor:
    contracts:
      - desc: "no rm -rf"
        G:
          pattern: arg_blacklist
          args: [Bash, command, ["rm\\\\s+-rf"]]
"""


def test_the_shim_imports_only_the_standard_library():
    src = shim_path().read_text(encoding="utf-8")
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "sponsio" not in stripped, line


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_parse_args_reads_every_option_and_ignores_unknown_ones():
    o = parse_args(
        [
            "cursor",
            "--event",
            "beforeShellExecution",
            "--socket",
            "/s",
            "--mode",
            "observe",
            "--deadline",
            "3.5",
            "--plugin-root",
            "/r",
            "--state-dir",
            "/d",
            "--sponsio",
            "/x/sponsio --flag",
            "--future",
            "value",
        ]
    )
    assert (o.host, o.event, o.socket, o.mode) == (
        "cursor",
        "beforeShellExecution",
        "/s",
        "observe",
    )
    assert o.deadline == 3.5
    assert (o.plugin_root, o.state_dir) == ("/r", "/d")
    assert o.sponsio == ["/x/sponsio", "--flag"]


def test_classify_picks_observe_for_events_that_already_ran():
    pre = json.dumps({"hook_event_name": "PreToolUse"})
    post = json.dumps({"hook_event_name": "PostToolUse"})
    assert classify("claude-code", None, pre) == ("guard.check", "PreToolUse")
    assert classify("claude-code", None, post) == ("guard.observe", "PostToolUse")
    assert classify("claude-code", None, "not json") == ("guard.check", "PreToolUse")
    assert classify("cursor", "beforeReadFile", "{}") == (
        "guard.check",
        "beforeReadFile",
    )
    assert classify("cursor", "afterShellExecution", "{}") == (
        "guard.observe",
        "afterShellExecution",
    )


def test_deny_shapes_match_each_host_and_always_exit_2():
    out, code = deny_reply("claude-code", "PreToolUse", "why")
    assert code == 2
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    out, code = deny_reply("claude-code", "PostToolUse", "why")
    assert code == 2 and json.loads(out) == {"decision": "block", "reason": "why"}
    out, code = deny_reply("cursor", "beforeShellExecution", "why")
    assert code == 2 and json.loads(out)["permission"] == "deny"
    out, code = deny_reply("cursor", "beforeSubmitPrompt", "why")
    assert code == 2 and json.loads(out)["continue"] is False
    out, code = deny_reply("cursor", "afterShellExecution", "why")
    assert code == 2 and "additional_context" in json.loads(out)


# ---------------------------------------------------------------------------
# The script, as a host runs it
# ---------------------------------------------------------------------------


class Home:
    """A throwaway HOME with a library, a socket path and a state dir.
    Any daemon started under it is stopped by the pid in its lock file."""

    def __init__(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="sp-", dir="/tmp"))
        self.root = self.home / "plugins"
        self.state = self.home / "state"
        self.socket = self.home / "sock"
        lib = self.root / BUCKET / "sponsio.yaml"
        lib.parent.mkdir(parents=True)
        lib.write_text(LIBRARY)
        cur = self.root / "_host_cursor" / "sponsio.yaml"
        cur.parent.mkdir(parents=True)
        cur.write_text(LIBRARY)

    def env(self, **extra: str) -> dict[str, str]:
        env = {
            "HOME": str(self.home),
            "PATH": os.pathsep.join(
                [str(Path(sys.executable).parent), "/usr/bin", "/bin"]
            ),
            "LANG": "C.UTF-8",
        }
        env.update(extra)
        return env

    def shim(
        self,
        payload: dict | str,
        *,
        host: str = "claude-code",
        event: str | None = None,
        mode: str | None = None,
        deadline: float = 2.0,
        sponsio: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        argv = [
            sys.executable,
            "-I",
            "-S",
            str(shim_path()),
            host,
            "--socket",
            str(self.socket),
            "--plugin-root",
            str(self.root),
            "--state-dir",
            str(self.state),
            "--deadline",
            str(deadline),
        ]
        if event:
            argv += ["--event", event]
        if mode:
            argv += ["--mode", mode]
        if sponsio:
            argv += ["--sponsio", sponsio]
        return subprocess.run(
            argv,
            input=payload if isinstance(payload, str) else json.dumps(payload),
            capture_output=True,
            text=True,
            env=self.env(**(env or {})),
            timeout=60,
        )

    def daemon_pid(self, state: Path | None = None) -> int | None:
        try:
            text = ((state or self.state) / "daemon.lock").read_text().strip()
        except OSError:
            return None
        return int(text) if text.isdigit() else None

    def close(self) -> None:
        # The shim pins the state dir; the legacy entry points let the
        # daemon default it under HOME. Stop whichever came up, by pid.
        for state in (self.state, self.home / ".sponsio" / "state"):
            pid = self.daemon_pid(state)
            if pid and audit.pid_alive(pid):
                os.kill(pid, signal.SIGTERM)
                deadline = time.monotonic() + 5
                while audit.pid_alive(pid) and time.monotonic() < deadline:
                    time.sleep(0.05)
        subprocess.run(["rm", "-rf", str(self.home)], check=False)


@pytest.fixture
def home() -> Home:
    h = Home()
    yield h
    h.close()


def _bash(command: str, **extra) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        **extra,
    }


def test_no_daemon_and_none_can_start_is_a_deny_with_exit_2_inside_the_deadline(
    home: Home,
):
    t0 = time.monotonic()
    r = home.shim(_bash("ls"), deadline=1.0, sponsio="/usr/bin/false")
    elapsed = time.monotonic() - t0
    assert r.returncode == 2
    body = json.loads(r.stdout)["hookSpecificOutput"]
    assert body["permissionDecision"] == "deny"
    reason = body["permissionDecisionReason"]
    assert "sponsio daemon run --detach" in reason
    assert "sponsio host uninstall claude-code" in reason
    assert "~/.claude/settings.json" in reason
    assert "not reachable" in r.stderr
    assert elapsed < 1.0 + 1.5, elapsed
    assert not home.socket.exists()


def test_the_shim_starts_a_daemon_and_the_verdict_comes_from_it(home: Home):
    assert not daemon_is_running(home.socket)
    allowed = home.shim(_bash("ls"))
    assert allowed.returncode == 0 and allowed.stdout == "", allowed.stderr
    assert daemon_is_running(home.socket)
    pid = home.daemon_pid()
    assert pid and audit.pid_alive(pid)

    denied = home.shim(_bash("rm -rf /"))
    assert denied.returncode == 0  # the daemon's exit code for a contract deny
    body = json.loads(denied.stdout)["hookSpecificOutput"]
    assert body["permissionDecision"] == "deny"
    assert "Bash" in body["permissionDecisionReason"]
    assert home.daemon_pid() == pid


def test_a_post_event_is_observed_not_gated(home: Home):
    assert home.shim(_bash("ls")).returncode == 0
    post = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "tool_response": "a b c",
    }
    r = home.shim(post)
    assert r.returncode == 0 and r.stdout == ""


def test_observe_mode_on_the_command_reaches_the_daemon(home: Home):
    r = home.shim(_bash("rm -rf /tmp/x"), mode="observe")
    assert r.returncode == 0 and r.stdout == "", r.stderr
    r = home.shim(_bash("rm -rf /tmp/x"))
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_the_hosts_environment_does_not_reach_the_daemon(home: Home):
    """An ``env`` block in the host settings is the agent's to edit. A
    daemon the shim starts under it must not read its mode from there."""
    r = home.shim(
        _bash("rm -rf /tmp/x"),
        env={"SPONSIO_GUARD_MODE": "observe", "SPONSIO_MODE": "observe"},
    )
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_cursor_events_get_cursor_shapes(home: Home):
    payload = {"command": "rm -rf /", "conversation_id": "c1"}
    r = home.shim(payload, host="cursor", event="beforeShellExecution")
    assert r.returncode == 2
    assert json.loads(r.stdout)["permission"] == "deny"
    ok = home.shim(
        {"command": "ls", "conversation_id": "c1"},
        host="cursor",
        event="beforeShellExecution",
    )
    assert ok.returncode == 0 and json.loads(ok.stdout)["permission"] == "allow"
    # Unreachable daemon, Cursor shape: deny JSON and exit 2.
    dead = Home()
    try:
        r = dead.shim(
            payload,
            host="cursor",
            event="beforeShellExecution",
            deadline=1.0,
            sponsio="/usr/bin/false",
        )
        assert r.returncode == 2 and json.loads(r.stdout)["permission"] == "deny"
    finally:
        dead.close()


def test_eight_shims_racing_to_start_the_daemon_leave_one_and_agree(home: Home):
    assert not daemon_is_running(home.socket)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: home.shim(_bash("ls"), deadline=5), range(8)))
    for r in results:
        assert r.returncode == 0 and r.stdout == "", (r.returncode, r.stdout, r.stderr)
    pid = home.daemon_pid()
    assert pid and audit.pid_alive(pid)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        others = _daemons_for(home)
        if others == [pid]:
            break
        time.sleep(0.1)
    assert _daemons_for(home) == [pid]
    assert len(audit.read(home.state, audit.DAEMON_START)) == 1
    # And they all talked to the one daemon: eight events in one trace.
    wal = list((home.state / "wal").glob("*.jsonl"))
    assert len(wal) == 1
    assert len(wal[0].read_text().splitlines()) == 8


def _daemons_for(home: Home) -> list[int]:
    r = subprocess.run(
        ["pgrep", "-f", f"daemon run --socket {home.socket}"],
        capture_output=True,
        text=True,
    )
    return [int(p) for p in r.stdout.split() if p.isdigit()]


def test_killing_the_daemon_denies_until_it_is_back_and_records_the_restart(
    home: Home,
):
    assert home.shim(_bash("ls")).returncode == 0
    pid = home.daemon_pid()
    assert pid
    os.kill(pid, signal.SIGKILL)
    while audit.pid_alive(pid):
        time.sleep(0.02)
    # With nothing able to start a daemon, the hook denies.
    r = home.shim(_bash("ls"), deadline=1.0, sponsio="/usr/bin/false")
    assert r.returncode == 2
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    # With the real starter, the next hook brings the daemon back.
    r = home.shim(_bash("ls"))
    assert r.returncode == 0 and r.stdout == "", r.stderr
    new_pid = home.daemon_pid()
    assert new_pid and new_pid != pid
    restarts = audit.read(home.state, audit.DAEMON_RESTART)
    assert restarts and restarts[-1]["previous_pid"] == pid


# ---------------------------------------------------------------------------
# The legacy entry points do the same through the daemon
# ---------------------------------------------------------------------------


def _legacy(home: Home, args: list[str], payload: dict, **env: str):
    return subprocess.run(
        [sys.executable, "-m", "sponsio.cli", *args],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=home.env(
            SPONSIO_DAEMON_SOCKET=str(home.socket),
            SPONSIO_PLUGIN_ROOT=str(home.root),
            **env,
        ),
        timeout=60,
    )


def test_host_guard_routes_through_the_daemon(home: Home):
    r = _legacy(home, ["host", "guard", "claude-code", "--stdin"], _bash("rm -rf /"))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert daemon_is_running(home.socket)
    r = _legacy(home, ["plugin", "guard", "--stdin"], _bash("ls"))
    assert r.returncode == 0 and r.stdout == ""


def test_host_guard_denies_when_no_daemon_can_come_up(home: Home):
    # A socket path whose parent is a regular file: nothing can bind it.
    blocker = home.home / "blocker"
    blocker.write_text("x")
    r = subprocess.run(
        [sys.executable, "-m", "sponsio.cli", "host", "guard", "claude-code"],
        input=json.dumps(_bash("ls")),
        capture_output=True,
        text=True,
        env=home.env(SPONSIO_DAEMON_SOCKET=str(blocker / "sock")),
        timeout=60,
    )
    assert r.returncode == 2
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "sponsio daemon run --detach" in r.stderr


def test_direct_keeps_the_in_process_path(home: Home):
    r = _legacy(home, ["host", "guard", "claude-code", "--direct"], _bash("rm -rf /"))
    assert r.returncode == 0
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert not home.socket.exists()

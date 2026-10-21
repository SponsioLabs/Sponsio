"""One daemon per state dir, and a live socket is never removed.

``DaemonServer`` used to unlink whatever socket file it found, so two
daemons started at once (every hook of a parallel tool batch finding
no socket and starting one) both came up, the second removed the
first one's socket, and the session had two traces. Now the daemon
holds an exclusive lock on ``<state-dir>/daemon.lock``, probes an
existing socket before touching it, and ``daemon run --detach`` from
eight callers at once leaves one daemon.
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
from sponsio.daemon.client import DaemonClient, daemon_is_running, wait_for_daemon
from sponsio.daemon.server import DaemonAlreadyRunning, DaemonServer


@pytest.fixture
def home() -> Path:
    d = Path(tempfile.mkdtemp(prefix="sp-", dir="/tmp"))
    yield d
    for pid in _pids_for(d):
        _stop(pid)
    subprocess.run(["rm", "-rf", str(d)], check=False)


def _env(home: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "PATH": os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"]),
        "LANG": "C.UTF-8",
        "SPONSIO_PLUGIN_ROOT": str(home / "plugins"),
    }


def _detach(home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "sponsio.cli",
            "daemon",
            "run",
            "--detach",
            "--socket",
            str(home / "sock"),
            "--state-dir",
            str(home / "state"),
        ],
        env=_env(home),
        capture_output=True,
        text=True,
        timeout=60,
    )


def _lock_pid(home: Path) -> int | None:
    try:
        text = (home / "state" / "daemon.lock").read_text().strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def _pids_for(home: Path) -> list[int]:
    """Daemon processes serving this test's socket, found by the unique
    temp path on their command line (read-only lookup)."""
    r = subprocess.run(
        ["pgrep", "-f", f"daemon run --socket {home / 'sock'}"],
        capture_output=True,
        text=True,
    )
    return [int(p) for p in r.stdout.split() if p.isdigit()]


def _stop(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not audit.pid_alive(pid):
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


# ---------------------------------------------------------------------------
# In-process: the lock and the probe
# ---------------------------------------------------------------------------


def test_second_server_on_the_same_lock_is_refused(home: Path):
    a = DaemonServer(home / "a.sock", lock_path=home / "daemon.lock")
    a.start()
    try:
        b = DaemonServer(home / "b.sock", lock_path=home / "daemon.lock")
        with pytest.raises(DaemonAlreadyRunning, match="holds"):
            b.start()
        assert not (home / "b.sock").exists()
        assert (home / "daemon.lock").read_text().strip() == str(os.getpid())
    finally:
        a.stop(join_timeout_s=0.5)
    # The lock is released on stop: a new server takes it.
    c = DaemonServer(home / "c.sock", lock_path=home / "daemon.lock")
    c.start()
    c.stop(join_timeout_s=0.5)


def test_a_socket_someone_answers_on_is_left_alone(home: Path):
    a = DaemonServer(home / "sock", accept_timeout_s=0.05)
    a.start()
    try:
        b = DaemonServer(home / "sock", accept_timeout_s=0.05)
        with pytest.raises(DaemonAlreadyRunning, match="answers"):
            b.start()
        # The first daemon still answers: its socket was not unlinked.
        assert DaemonClient(socket_path=home / "sock").call("ping")["pong"] is True
        b.stop(join_timeout_s=0.5)
        assert (home / "sock").is_socket()
    finally:
        a.stop(join_timeout_s=0.5)


def test_lock_file_is_private_and_names_the_holder(home: Path):
    s = DaemonServer(home / "sock", lock_path=home / "state" / "daemon.lock")
    s.start()
    try:
        lock = home / "state" / "daemon.lock"
        assert oct(lock.stat().st_mode & 0o777) == "0o600"
        assert lock.read_text().strip() == str(os.getpid())
        assert s.previous_pid is None
    finally:
        s.stop(join_timeout_s=0.5)


# ---------------------------------------------------------------------------
# Subprocess: --detach
# ---------------------------------------------------------------------------


def test_detach_starts_a_daemon_once_and_records_the_start(home: Path):
    first = _detach(home)
    assert first.returncode == 0, first.stderr
    assert daemon_is_running(home / "sock")
    pid = _lock_pid(home)
    assert pid and audit.pid_alive(pid)
    assert "running" in first.stdout

    second = _detach(home)
    assert second.returncode == 0, second.stderr
    assert "already running" in second.stdout
    assert _lock_pid(home) == pid
    assert _pids_for(home) == [pid]

    starts = audit.read(home / "state", audit.DAEMON_START)
    assert len(starts) == 1 and starts[0]["pid"] == pid
    assert not audit.read(home / "state", audit.DAEMON_RESTART)
    assert oct((home / "state" / "daemon.log").stat().st_mode & 0o777) == "0o600"


def test_a_daemon_killed_without_cleanup_is_replaced_and_the_restart_recorded(
    home: Path,
):
    assert _detach(home).returncode == 0
    first_pid = _lock_pid(home)
    assert first_pid
    os.kill(first_pid, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while audit.pid_alive(first_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    # The dead daemon's socket file is still there; nobody answers.
    assert (home / "sock").is_socket() and not daemon_is_running(home / "sock")

    assert _detach(home).returncode == 0
    second_pid = _lock_pid(home)
    assert second_pid and second_pid != first_pid
    assert daemon_is_running(home / "sock")
    restarts = audit.read(home / "state", audit.DAEMON_RESTART)
    assert len(restarts) == 1
    assert restarts[0]["previous_pid"] == first_pid
    assert restarts[0]["pid"] == second_pid


def test_eight_concurrent_detached_starts_leave_one_daemon(home: Path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: _detach(home), range(8)))
    assert all(r.returncode == 0 for r in results), [r.stderr for r in results]
    assert wait_for_daemon(home / "sock", timeout_s=5)
    # Give losers a moment to exit after finding the lock taken.
    deadline = time.monotonic() + 10
    while len(_pids_for(home)) > 1 and time.monotonic() < deadline:
        time.sleep(0.1)
    pids = _pids_for(home)
    assert len(pids) == 1, pids
    assert pids[0] == _lock_pid(home)
    # Every caller reaches the same daemon.
    seen = {
        DaemonClient(socket_path=home / "sock").call("ping")["pid"] for _ in range(8)
    }
    assert seen == {pids[0]}
    # Exactly one start record; the losers wrote nothing.
    assert len(audit.read(home / "state", audit.DAEMON_START)) == 1
    log = (home / "state" / "daemon.log").read_text()
    assert log.count("already running") == 7, log


def test_foreground_run_exits_zero_when_another_daemon_holds_the_lock(home: Path):
    assert _detach(home).returncode == 0
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "sponsio.cli",
            "daemon",
            "run",
            "--socket",
            str(home / "sock"),
            "--state-dir",
            str(home / "state"),
        ],
        env=_env(home),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0
    assert "already running" in r.stdout
    assert (
        json.loads((home / "state" / "audit.jsonl").read_text().splitlines()[0])[
            "event"
        ]
        == audit.DAEMON_START
    )

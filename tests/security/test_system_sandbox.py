"""The Tier 2 sandbox helper, and smoke cases for it on a real install.

The unit tests build the installer's layout under a temporary root, with
an in-process daemon on its socket, and run everywhere as the current
user (who is both the agent and "root" there). The smoke cases at the
bottom need ``SPONSIO_TEST_TIER=2`` and a real system install (the CI
job ``tier2``); they belong to no vector and show the helper works there.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from sponsio.system_install import SERVICE_USER, Layout
from tests.security.corpus import bash
from tests.security.system_sandbox import (
    AGENT_USER_ENV,
    REQUIRE_ENV,
    TIER_ENV,
    Privileges,
    SystemSandbox,
    Verdict,
    open_system_sandbox,
)

REPO = Path(__file__).resolve().parents[2]

MARKER = "sponsio-tier2-smoke-marker"
MARKER_CONTRACT = {
    "desc": "Tier 2 smoke: the marker command is refused",
    "G": {"pattern": "arg_blacklist", "args": ["Bash", "command", [MARKER]]},
}

pytestmark = pytest.mark.skipif(
    sys.platform not in ("darwin", "linux"), reason="system install is POSIX only"
)


# ---------------------------------------------------------------------------
# A fake install
# ---------------------------------------------------------------------------


def _fake_install(root: Path) -> Layout:
    """The installer's layout under ``root``: engine interpreter, shim,
    the bundled library in the rule store, daemon config, and Claude
    Code's managed settings, each written by the installer's own code."""
    from sponsio.cli.groups.plugin import _stamp_bundled_source
    from sponsio.daemon.config import CONFIG_FILE, DaemonConfig
    from sponsio.guard_stdin import library_path_for
    from sponsio.plugin.registry import read_bundled
    from sponsio.shim import shim_path
    from sponsio.system_install import HOST_BUCKETS, claude_managed_settings

    import yaml

    lay = Layout(platform=sys.platform, root=root)
    python = lay.fs(lay.python)
    python.parent.mkdir(parents=True)
    # The helpers run the engine with -I, which leaves this checkout off
    # sys.path unless sponsio is installed into the interpreter (and picks
    # another checkout's editable install if that is). So the fake engine
    # drops -I and puts this checkout first.
    python.write_text(
        "#!/bin/sh\n"
        '[ "$1" = "-I" ] && shift\n'
        f"PYTHONPATH={shlex.quote(str(REPO))} "
        f'exec {shlex.quote(sys.executable)} -s "$@"\n'
    )
    python.chmod(0o755)
    shim = lay.fs(lay.shim)
    shim.parent.mkdir(parents=True)
    shutil.copy(shim_path(), shim)
    bucket = HOST_BUCKETS["claude-code"][0]
    lib = library_path_for(bucket, root=lay.fs(lay.plugins))
    lib.parent.mkdir(parents=True)
    lib.write_text(_stamp_bundled_source(read_bundled(bucket), bucket))
    cfg = DaemonConfig(plugin_root=lay.fs(lay.plugins))
    (lay.fs(lay.state) / CONFIG_FILE).write_text(yaml.safe_dump(cfg.to_dict()))
    lay.fs(lay.run).mkdir(parents=True, exist_ok=True)
    settings = lay.fs(lay.claude_settings)
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(claude_managed_settings(lay, allow_user_hooks=False), indent=2)
    )
    return lay


@contextlib.contextmanager
def _daemon(lay: Layout, **server_kw):
    from sponsio.daemon.guard_service import GuardService
    from sponsio.daemon.handlers import register_default_handlers
    from sponsio.daemon.server import DaemonServer

    server = DaemonServer(lay.fs(lay.socket), accept_timeout_s=0.05, **server_kw)
    service = GuardService(root=lay.fs(lay.plugins), state_dir=lay.fs(lay.state))
    register_default_handlers(server, guard_service=service)
    server.start()
    try:
        yield server
    finally:
        server.stop(join_timeout_s=1.0)


@pytest.fixture
def root():
    # AF_UNIX paths are capped near 104 bytes on macOS; keep the root short.
    path = Path(tempfile.mkdtemp(prefix="spt2r-", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def layout(root):
    return _fake_install(root)


@pytest.fixture
def daemon(layout):
    with _daemon(layout) as server:
        yield server


@pytest.fixture
def sb(layout, daemon):
    box = SystemSandbox(layout, vector_id="VX", privileges=Privileges())
    yield box
    box.close()


# ---------------------------------------------------------------------------
# Skips, and finding the install
# ---------------------------------------------------------------------------


def test_skips_unless_tier_2(monkeypatch, root):
    monkeypatch.delenv(TIER_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception, match="SPONSIO_TEST_TIER=2"):
        open_system_sandbox("VX", root=root)


def test_skips_without_an_install_and_says_why(monkeypatch, root):
    monkeypatch.setenv(TIER_ENV, "2")
    monkeypatch.delenv(REQUIRE_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception, match=r"\[VX\] no system install"):
        open_system_sandbox("VX", root=root)


def test_a_daemon_that_is_down_is_named(monkeypatch, layout):
    monkeypatch.setenv(TIER_ENV, "2")
    monkeypatch.delenv(REQUIRE_ENV, raising=False)
    service = layout.fs(layout.service_file)
    service.parent.mkdir(parents=True)
    service.write_text("")
    with pytest.raises(pytest.skip.Exception, match="not listening"):
        open_system_sandbox("VX", root=layout.root)


def test_required_install_fails_instead_of_skipping(monkeypatch, root):
    monkeypatch.setenv(TIER_ENV, "2")
    monkeypatch.setenv(REQUIRE_ENV, "1")
    with pytest.raises(pytest.fail.Exception, match=r"\[VX\] no system install"):
        open_system_sandbox("VX", root=root)


def test_an_unknown_agent_user_is_a_skip(monkeypatch, layout, daemon):
    monkeypatch.setenv(TIER_ENV, "2")
    monkeypatch.delenv(REQUIRE_ENV, raising=False)
    monkeypatch.setenv(AGENT_USER_ENV, "sponsio-no-such-user")
    with pytest.raises(pytest.skip.Exception, match="not a user here"):
        open_system_sandbox("VX", root=layout.root)


def test_sudo_runs_the_agent_in_its_home_with_only_its_env(tmp_path):
    from tests.security.system_sandbox import SudoPrivileges

    cmd, env, cwd = SudoPrivileges("someone-else").agent(
        ["/bin/sh", "-c", 'pwd; echo "$HOME|$SECRET"'], {"HOME": "/h"}, str(tmp_path)
    )
    assert cmd[:5] == ["sudo", "-n", "-u", "someone-else", "/usr/bin/env"]
    assert cwd == "/"
    # Everything after `sudo -n -u <agent>` runs as the agent; here, as us.
    out = subprocess.run(
        cmd[4:], env={**env, "SECRET": "x"}, cwd=cwd, capture_output=True, text=True
    )
    assert out.stdout.split() == [str(tmp_path.resolve()), "/h|"]


def test_opens_on_an_install(monkeypatch, layout, daemon):
    monkeypatch.setenv(TIER_ENV, "2")
    box = open_system_sandbox("VX", root=layout.root, privileges=Privileges())
    try:
        assert box.socket_path == layout.fs(layout.socket)
        assert box.home.is_dir()
    finally:
        box.close()
    assert not box.home.exists()


# ---------------------------------------------------------------------------
# The hook path
# ---------------------------------------------------------------------------


def test_the_hook_command_is_the_managed_one(sb, layout):
    argv = sb.hook_argv()
    assert str(layout.fs(layout.shim)) in argv
    assert str(layout.fs(layout.socket)) in argv
    assert argv[0] == str(layout.fs(layout.python))


def test_ls_is_allowed_and_a_rule_denies(sb):
    assert sb.check(bash("ls")).allowed
    assert sb.check(bash(f"echo {MARKER}")).allowed

    sb.add_contracts([MARKER_CONTRACT])
    v = sb.check(bash(f"echo {MARKER}"))
    assert v.denied and not v.fail_closed, v
    assert v.reason
    assert sb.check(bash("ls")).allowed


def test_close_puts_the_rules_back(layout, daemon, sb):
    original = sb.library.read_bytes()
    sb.add_contracts([MARKER_CONTRACT])
    assert sb.library.read_bytes() != original
    # The daemon has now loaded the stronger rules, so putting the
    # original back is a weakening it must be told to accept.
    assert sb.check(bash(f"echo {MARKER}")).denied
    sb.close()
    assert sb.library.read_bytes() == original

    after = SystemSandbox(layout, vector_id="VX", privileges=Privileges())
    try:
        assert after.check(bash(f"echo {MARKER}")).allowed
    finally:
        after.close()


def test_a_new_bucket_is_removed_again(sb):
    sb.install_library("version: '1'\nagents:\n  extra: {}\n", bucket="_extra_bucket")
    path = sb.plugin_root / "_extra_bucket"
    assert (path / "sponsio.yaml").is_file()
    sb.close()
    assert not path.exists()


def test_each_sandbox_has_its_own_session(layout, daemon, sb):
    other = SystemSandbox(layout, vector_id="VX", privileges=Privileges())
    try:
        assert other.session_id != sb.session_id
        assert sb.session_id.startswith("t2-VX-")
        sb.check(bash("ls"))
        first = sb.session_id
        assert sb.new_session() != first
        sb.check(bash("ls"))
        wal = " ".join(p.name for p in (layout.fs(layout.state) / "wal").iterdir())
        assert first in wal and sb.session_id in wal
        assert other.session_id not in wal
    finally:
        other.close()


def test_no_daemon_is_a_fail_closed_deny(layout):
    box = SystemSandbox(layout, vector_id="VX", privileges=Privileges())
    try:
        v = box.check(bash("ls"))
        assert v.denied and v.fail_closed, v
    finally:
        box.close()


def _proc(code: int, out: str = "", err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, out, err)


def test_verdicts_read_the_host_protocol():
    deny = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "no",
            }
        }
    )
    assert Verdict.parse(_proc(0)).allowed
    rule = Verdict.parse(_proc(0, deny))
    assert rule.denied and not rule.fail_closed and rule.reason == "no"
    assert Verdict.parse(_proc(2, deny)).fail_closed
    assert Verdict.parse(_proc(2, "", "broken")).fail_closed
    post = Verdict.parse(_proc(0, json.dumps({"decision": "block", "reason": "r"})))
    assert post.denied and post.reason == "r"
    crashed = Verdict.parse(_proc(1, "", "Traceback"))
    assert crashed.decision == "error" and not crashed.allowed and not crashed.denied


# ---------------------------------------------------------------------------
# Permission assertions
# ---------------------------------------------------------------------------


def test_write_and_read_assertions(sb, root):
    locked = root / "locked"
    locked.mkdir()
    (locked / "f").write_text("x")
    (locked / "f").chmod(0o400)
    locked.chmod(0o500)
    try:
        sb.assert_not_writable(locked)
        sb.assert_not_writable(locked / "f")
        sb.assert_not_writable(locked / "new")
    finally:
        locked.chmod(0o700)

    hidden = root / "hidden"
    hidden.write_text("x")
    hidden.chmod(0o000)
    sb.assert_not_readable(hidden)

    open_file = root / "open"
    open_file.write_text("x")
    with pytest.raises(AssertionError, match=r"\[VX\] .*open is writable"):
        sb.assert_not_writable(open_file)
    with pytest.raises(AssertionError, match=r"\[VX\] .*open is readable"):
        sb.assert_not_readable(open_file)
    assert open_file.read_text() == "x"
    with pytest.raises(AssertionError, match=r"\[VX\] .*ENOENT"):
        sb.assert_not_readable(root / "missing")


def test_owner_assertion(sb, root):
    f = root / "mine"
    f.write_text("x")
    with pytest.raises(AssertionError, match=r"\[VX\] .*mine is owned by"):
        sb.assert_owned_by(f)
    sb.assert_owned_by(f, owners=(sb.agent_user,))


def test_admin_assertion_passes_when_refused(layout, root):
    with _daemon(layout, admin_policy=lambda _peer: False):
        box = SystemSandbox(layout, vector_id="VX", privileges=Privileges())
        try:
            box.assert_admin_refused()
        finally:
            box.close()


def test_admin_assertion_fails_when_accepted(sb):
    with pytest.raises(AssertionError, match=r"\[VX\] admin socket .* accepted"):
        sb.assert_admin_refused()


# ---------------------------------------------------------------------------
# Preparation, audit, execute
# ---------------------------------------------------------------------------


def test_mode_is_set_back(sb, layout):
    from sponsio.daemon.config import load

    state = layout.fs(layout.state)
    before = load(state).mode_for("claude-code")
    other = "observe" if before == "enforce" else "enforce"
    sb.set_mode(other)
    assert load(state).mode_for("claude-code") == other
    sb.close()
    assert load(state).mode_for("claude-code") == before


def test_audit_events_start_at_the_sandbox(layout, daemon):
    first = SystemSandbox(layout, vector_id="VX", privileges=Privileges())
    first.admin("rules.status")
    first.close()
    box = SystemSandbox(layout, vector_id="VX", privileges=Privileges())
    try:
        assert box.audit_events("admin_call") == []
        box.admin("rules.status")
        calls = box.audit_events("admin_call")
        assert len(calls) == 1
    finally:
        box.close()


def test_execute_runs_in_the_agent_home(sb, root):
    proc = sb.execute("touch made", touches=["made"])
    assert proc.returncode == 0, proc.stderr
    assert (sb.home / "made").is_file()
    with pytest.raises(AssertionError, match=r"\[VX\] .* outside"):
        sb.execute("true", touches=[str(root / "elsewhere")])


# ---------------------------------------------------------------------------
# Smoke on a real system install (SPONSIO_TEST_TIER=2, CI job tier2)
# ---------------------------------------------------------------------------


@pytest.fixture
def system_sandbox():
    box = open_system_sandbox("smoke")
    yield box
    box.close()


def test_smoke_the_agent_cannot_write_the_state_dir(system_sandbox):
    sb = system_sandbox
    sb.assert_not_writable(sb.layout.state)
    sb.assert_not_readable(sb.layout.state)
    sb.assert_not_writable(sb.library)
    sb.assert_not_writable(sb.layout.shim)


def test_smoke_the_admin_socket_refuses_the_agent(system_sandbox):
    system_sandbox.assert_admin_refused()


def test_smoke_install_files_belong_to_root_or_the_service(system_sandbox):
    sb = system_sandbox
    for path in (sb.layout.shim, sb.layout.claude_settings, sb.layout.state):
        sb.assert_owned_by(path)
    sb.assert_owned_by(sb.library, owners=(SERVICE_USER,))


def test_smoke_ls_is_allowed_and_a_prepared_rule_denies(system_sandbox):
    sb = system_sandbox
    assert os.geteuid() != 0
    assert sb.check(bash("ls")).allowed
    sb.add_contracts([MARKER_CONTRACT])
    v = sb.check(bash(f"echo {MARKER}"))
    assert v.denied and not v.fail_closed, v
    assert sb.check(bash("ls")).allowed
    assert sb.audit_events("admin_call")
    sb.close()

    after = open_system_sandbox("smoke")
    try:
        assert after.check(bash(f"echo {MARKER}")).allowed
    finally:
        after.close()

"""System install (P1.1, P1.2, P1.6) into a temporary root.

Nothing here runs as root or touches the real system: every path hangs
off a temporary ``root``, ownership changes are off, and system commands
(dscl, useradd, launchctl, systemctl, pip) are recorded, not run. The
real install is exercised by the Tier 2 CI job.
"""

from __future__ import annotations

import json
import plistlib
import shlex
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from sponsio.system_install import (
    SERVICE_USER,
    Executor,
    InstallOptions,
    Layout,
    _root_owned_chain,
    check_interpreter,
    claude_managed_settings,
    install,
    uninstall,
)


class Recorder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    def ran(self, *prefix: str) -> bool:
        return any(c[: len(prefix)] == list(prefix) for c in self.calls)


@pytest.fixture(params=["darwin", "linux"])
def env(request, tmp_path):
    layout = Layout(platform=request.param, root=tmp_path / "root")
    runner = Recorder()
    ex = Executor(layout, chown=False, runner=runner, echo=lambda _l: None)
    return layout, ex, runner


def _mode(layout: Layout, path: Path) -> int:
    return stat.S_IMODE(layout.fs(path).stat().st_mode)


def test_install_lays_out_every_piece(env):
    layout, ex, runner = env
    manifest = install(ex, InstallOptions(hosts=("claude-code", "cursor")))

    # The service user, the engine and the service.
    if layout.darwin:
        assert runner.ran("dscl", ".", "-create", f"/Users/{SERVICE_USER}")
        assert runner.ran("launchctl", "bootstrap", "system")
    else:
        assert runner.ran("useradd", "--system")
        assert runner.ran("systemctl", "enable", "sponsio.service")
    assert any(c[1:3] == ["-m", "venv"] for c in runner.calls)
    assert any(
        c[:4] == [str(layout.python), "-m", "pip", "install"] for c in runner.calls
    )

    # Shims, root-owned paths; private state; open socket dir.
    assert layout.fs(layout.shim).read_text().startswith('"""Sponsio hook shim')
    assert layout.fs(layout.mcp_shim).exists()
    assert _mode(layout, layout.state) == 0o700
    assert _mode(layout, layout.plugins) == 0o700
    assert _mode(layout, layout.run) == 0o755
    assert _mode(layout, layout.state / "daemon.yaml") == 0o600
    cfg = layout.fs(layout.state / "daemon.yaml").read_text()
    assert f"plugin_root: {layout.plugins}" in cfg

    # Bundled libraries for each wired host (no user libraries to import).
    for bucket in ("_host_claude_code", "_host_cursor", "_host_cursor_subagent"):
        assert layout.fs(layout.plugins / bucket / "sponsio.yaml").exists()
    assert manifest["hosts"] == ["claude-code", "cursor"]
    assert json.loads(layout.fs(layout.manifest).read_text())["state"] == str(
        layout.state
    )


def test_claude_code_gets_managed_settings_that_lock_it_down(env):
    layout, ex, _runner = env
    install(ex, InstallOptions())
    settings = json.loads(layout.fs(layout.claude_settings).read_text())
    assert layout.claude_settings.parent.name == "managed-settings.d"
    assert settings["allowManagedHooksOnly"] is True
    assert settings["requiredMinimumVersion"] == "2.1.295"
    (pre,) = settings["hooks"]["PreToolUse"][0]["hooks"]
    assert pre["onFailure"] == "block"
    cmd = pre["command"]
    assert str(layout.shim) in cmd and str(layout.python) in cmd
    assert f"--socket {shlex.quote(str(layout.socket))}" in cmd
    assert cmd.endswith("--no-start")
    deny = settings["permissions"]["deny"]
    assert f"Edit(/{layout.state}/**)" in deny and f"Read(/{layout.state}/**)" in deny
    assert f"Edit(/{layout.claude_managed_dir}/**)" in deny
    assert not any(d.startswith("Write(") for d in deny)
    sandbox = settings["sandbox"]
    assert sandbox["enabled"] and sandbox["failIfUnavailable"]
    assert sandbox["allowUnsandboxedCommands"] is False
    assert str(layout.state) in sandbox["filesystem"]["denyWrite"]


def test_allow_user_hooks_leaves_them_loading():
    s = claude_managed_settings(Layout("linux"), allow_user_hooks=True)
    assert s["allowManagedHooksOnly"] is False


def test_cursor_system_hooks_keep_what_is_not_ours(env):
    layout, ex, _runner = env
    theirs = {"command": "/usr/local/bin/corp-audit"}
    target = layout.fs(layout.cursor_hooks)
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"version": 1, "hooks": {"preToolUse": [theirs]}}))
    install(ex, InstallOptions(hosts=("cursor",)))
    hooks = json.loads(target.read_text())["hooks"]
    assert theirs in hooks["preToolUse"]
    ours = [e for e in hooks["preToolUse"] if e is not theirs and e != theirs]
    assert len(ours) == 1 and ours[0]["failClosed"] is True
    assert "--no-start" in ours[0]["command"]

    uninstall(ex)
    assert json.loads(target.read_text())["hooks"] == {"preToolUse": [theirs]}


def test_user_libraries_are_imported_once(env, tmp_path):
    layout, ex, _runner = env
    home = tmp_path / "home"
    lib = home / ".sponsio" / "plugins" / "_host_claude_code" / "sponsio.yaml"
    lib.parent.mkdir(parents=True)
    lib.write_text(
        'version: "1"\nagents:\n  _host_claude_code:\n    contracts:\n'
        "      - desc: mine\n        G: {pattern: rate_limit, args: [Bash, 3]}\n"
    )
    (home / ".sponsio" / "plugins" / "github").mkdir()
    (home / ".sponsio" / "plugins" / "github" / "sponsio.yaml").write_text(
        'version: "1"\nagents:\n  github:\n    contracts: []\n'
    )
    manifest = install(ex, InstallOptions(migrate_from=home))
    copied = layout.fs(layout.plugins / "_host_claude_code" / "sponsio.yaml")
    assert copied.read_text() == lib.read_text()
    assert layout.fs(layout.plugins / "github" / "sponsio.yaml").exists()
    assert any(
        line.startswith("imported _host_claude_code (1 contracts)")
        for line in manifest["imported"]
    )

    # A second install keeps what the rule store has.
    lib.write_text(lib.read_text().replace("mine", "changed"))
    again = install(ex, InstallOptions(migrate_from=home))
    assert "mine" in copied.read_text()
    assert any(line.startswith("kept _host_claude_code") for line in again["imported"])


def test_uninstall_leaves_nothing(env):
    layout, ex, runner = env
    install(ex, InstallOptions(hosts=("claude-code", "cursor")))
    notes = uninstall(ex)
    for path in (
        layout.prefix,
        layout.libexec,
        layout.state,
        layout.logs,
        layout.run,
        layout.service_file,
        layout.claude_settings,
        layout.cursor_hooks,
    ):
        assert not layout.fs(path).exists(), path
    if layout.base:
        assert not layout.fs(layout.base).exists()
    if layout.darwin:
        assert runner.ran("dscl", ".", "-delete", f"/Users/{SERVICE_USER}")
    else:
        assert runner.ran("userdel", SERVICE_USER)
    assert any("sponsio host install claude-code" in n for n in notes)


def test_keep_state_keeps_the_rules_and_the_log(env):
    layout, ex, _runner = env
    install(ex, InstallOptions())
    uninstall(ex, keep_state=True)
    assert layout.fs(layout.state / "daemon.yaml").exists()
    assert not layout.fs(layout.prefix).exists()


def test_a_dry_run_changes_nothing(env):
    layout, ex, runner = env
    ex.dry_run = True
    install(ex, InstallOptions(hosts=("claude-code", "cursor")))
    assert runner.calls == []
    assert not any(layout.root.rglob("*")) if layout.root.exists() else True
    assert any(line.startswith("write ") for line in ex.log)


def test_shared_directories_are_not_repermissioned(env):
    layout, ex, _runner = env
    opt = layout.fs(layout.prefix.parent)
    opt.mkdir(parents=True)
    opt.chmod(0o775)
    install(ex, InstallOptions())
    assert stat.S_IMODE(opt.stat().st_mode) == 0o775


def test_the_service_runs_the_daemon_as_its_own_user(env):
    layout, ex, _runner = env
    install(ex, InstallOptions())
    text = layout.fs(layout.service_file).read_text()
    if layout.darwin:
        plist = plistlib.loads(text.encode())
        assert plist["UserName"] == SERVICE_USER and plist["KeepAlive"] is True
        argv = plist["ProgramArguments"]
    else:
        assert f"User={SERVICE_USER}" in text
        for line in (
            "NoNewPrivileges=yes",
            "ProtectSystem=strict",
            "ProtectHome=read-only",
            "Restart=always",
        ):
            assert line in text
        argv = text.split("ExecStart=")[1].split("\n")[0].split()
    assert argv[:3] == [str(layout.python), "-m", "sponsio.cli"]
    assert str(layout.socket) in argv and str(layout.plugins) in argv


# ---------------------------------------------------------------------------
# The engine's interpreter
# ---------------------------------------------------------------------------


def test_a_user_owned_python_is_refused(tmp_path):
    fake = tmp_path / "python3"
    fake.write_text("")
    assert "not root" in _root_owned_chain(fake)


def test_an_old_or_broken_python_is_refused():
    py = Path("/bin/sh")  # root-owned on macOS and Linux

    def old(argv):
        return subprocess.CompletedProcess(argv, 0, "False\n/usr/lib\n", "")

    def broken(argv):
        return subprocess.CompletedProcess(argv, 1, "", "boom")

    assert "older than Python 3.10" in check_interpreter(py, old)
    assert "does not run" in check_interpreter(py, broken)


@pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX")
def test_a_python_whose_stdlib_is_user_owned_is_refused(tmp_path):
    py = Path("/bin/sh")
    stdlib = tmp_path / "lib"
    stdlib.mkdir()

    def runner(argv):
        return subprocess.CompletedProcess(argv, 0, f"True\n{stdlib}\n", "")

    assert "standard library" in check_interpreter(py, runner)


# ---------------------------------------------------------------------------
# The system shim never starts a daemon of its own
# ---------------------------------------------------------------------------


def test_a_system_shim_with_no_daemon_denies_and_names_the_service(tmp_path):
    from sponsio.shim import shim_path

    payload = json.dumps(
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {}}
    )
    done = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(shim_path()),
            "claude-code",
            "--socket",
            str(tmp_path / "absent.sock"),
            "--no-start",
        ],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 2
    reason = json.loads(done.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "system enforcement daemon is not reachable" in reason
    assert ("launchctl kickstart" in reason) or ("systemctl restart" in reason)

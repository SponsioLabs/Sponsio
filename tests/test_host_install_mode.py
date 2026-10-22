"""The hook command names the right binary and the mode the operator chose.

Two things ``sponsio host install`` wrote were not what it said. The
binary: ``_resolve_binary`` only accepted an already-absolute ``argv[0]``
and otherwise took whatever ``sponsio`` was first on PATH, so a relative
invocation on a machine with a second install wrote the other one into
the hook (Claude Code runs the tool when a hook exits non-zero, so a
broken binary is the guard off). The mode: ``--mode`` wrote
``defaults.mode`` into the host library, which the hook never read; the
installer printed ``observe`` and the hook blocked.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from sponsio.integrations import hosts
from sponsio.integrations.hosts import hook_command, resolve_binary


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(home / ".sponsio" / "plugins"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    # Every host config the installers write must land under the sandbox.
    for spec in hosts.available():
        assert spec.config_path_user.is_relative_to(home), spec.config_path_user
    return home


def test_a_relative_invocation_resolves_to_its_own_binary(tmp_path, monkeypatch):
    script = tmp_path / "venv" / "bin" / "sponsio"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["./venv/bin/sponsio", "host", "install"])
    assert resolve_binary() == str(script.resolve())


def test_a_module_invocation_resolves_to_the_interpreter(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["/x/sponsio/cli/__init__.py", "host"])
    assert resolve_binary() == f"{sys.executable} -m sponsio.cli"


def test_path_is_only_the_last_resort(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["sponsio-that-does-not-exist-here"])
    monkeypatch.setattr("shutil.which", lambda name: "/elsewhere/sponsio")
    assert resolve_binary() == "/elsewhere/sponsio"


def test_an_override_wins():
    assert resolve_binary("/opt/sponsio") == "/opt/sponsio"


def test_observe_rides_on_the_command_and_enforce_adds_nothing():
    base = hook_command("/x/sponsio", "host", "guard", "claude-code", "--stdin")
    assert base == "/x/sponsio host guard claude-code --stdin"
    assert (
        hook_command(
            "/x/sponsio", "host", "guard", "claude-code", "--stdin", mode="observe"
        )
        == "env SPONSIO_GUARD_MODE=observe /x/sponsio host guard claude-code --stdin"
    )


def _claude_commands(path: Path) -> list[str]:
    data = json.loads(path.read_text())
    return [
        h["command"]
        for entries in data["hooks"].values()
        for entry in entries
        for h in entry["hooks"]
    ]


def test_claude_code_install_carries_the_mode_and_a_reinstall_switches_it(
    isolated_home,
):
    from sponsio.integrations.claude_code_install import install

    spec = hosts.get("claude-code")
    r = install(spec, mode="observe", binary="/x/sponsio")
    cmds = _claude_commands(r.config_path)
    assert cmds and all(
        c.startswith("env SPONSIO_GUARD_MODE=observe /x/sponsio") for c in cmds
    )

    r = install(spec, mode="enforce", binary="/x/sponsio")
    cmds = _claude_commands(r.config_path)
    assert cmds == ["/x/sponsio host guard claude-code --stdin"] * len(cmds)


def test_cursor_install_carries_the_mode(isolated_home):
    from sponsio.integrations.cursor_install import install

    r = install(hosts.get("cursor"), mode="observe", binary="/x/sponsio")
    data = json.loads(Path(r.config_path).read_text())
    cmds = [h["command"] for entries in data["hooks"].values() for h in entries]
    assert cmds and all(c.startswith("env SPONSIO_GUARD_MODE=observe ") for c in cmds)


def test_the_hook_honours_the_mode_on_its_command(tmp_path, monkeypatch):
    """What the command carries is what the hook reads: the same violating
    call is refused by default and logged under the observe prefix."""
    import sponsio.guard_stdin as gs
    from sponsio.cli import _refresh_per_host_bundles

    root = tmp_path / "plugins"
    _refresh_per_host_bundles("claude-code", root)
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(root))
    monkeypatch.delenv("SPONSIO_MODE", raising=False)
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /"},
        "host": "claude-code",
    }
    monkeypatch.delenv("SPONSIO_GUARD_MODE", raising=False)
    assert gs.evaluate_event(dict(event, conversation_id="a")).allowed is False
    monkeypatch.setenv("SPONSIO_GUARD_MODE", "observe")
    assert gs.evaluate_event(dict(event, conversation_id="b")).allowed is True


def test_host_install_defaults_to_enforce_without_a_prompt():
    from sponsio.cli.groups.host import _resolve_runtime_mode

    assert (
        _resolve_runtime_mode(None, allow_prompt=False, default="enforce") == "enforce"
    )
    # A project sponsio.yaml keeps the shadow-mode first run.
    assert _resolve_runtime_mode(None, allow_prompt=False) == "observe"


def test_the_installer_no_longer_writes_a_mode_the_hook_ignores(tmp_path):
    from sponsio.cli import _refresh_per_host_bundles

    root = tmp_path / "plugins"
    _refresh_per_host_bundles("claude-code", root)
    text = (root / "_host_claude_code" / "sponsio.yaml").read_text()
    assert "mode:" not in text.split("agents:")[0]

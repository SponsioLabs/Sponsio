"""The hook command is the pinned shim, and the mode the operator chose.

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


def _claude_hooks(path: Path) -> dict[str, list[dict]]:
    data = json.loads(path.read_text())
    return {
        event: [h for entry in entries for h in entry["hooks"]]
        for event, entries in data["hooks"].items()
    }


def _claude_commands(path: Path) -> list[str]:
    return [h["command"] for hooks in _claude_hooks(path).values() for h in hooks]


def test_the_shim_command_is_pinned_end_to_end(isolated_home):
    from sponsio.integrations.hosts import shim_command
    from sponsio.shim import shim_path

    cmd = shim_command("claude-code")
    parts = cmd.split()
    assert parts[:3] == [sys.executable, "-I", "-S"]
    assert parts[3] == str(shim_path()) and parts[4] == "claude-code"
    assert "--socket" in parts and "--plugin-root" in parts and "--state-dir" in parts
    assert "--deadline" in parts and "--mode" not in parts
    sock = parts[parts.index("--socket") + 1]
    assert Path(sock).is_absolute() and sock.startswith(str(isolated_home))
    root = parts[parts.index("--plugin-root") + 1]
    assert root == str(isolated_home / ".sponsio" / "plugins")

    observe = shim_command("cursor", event="beforeReadFile", mode="observe")
    assert "cursor --event beforeReadFile" in observe
    assert observe.endswith("--mode observe")
    assert "SPONSIO_GUARD_MODE" not in observe


def test_claude_code_install_writes_the_shim_with_block_on_failure(isolated_home):
    from sponsio.integrations.claude_code_install import install

    spec = hosts.get("claude-code")
    r = install(spec, mode="observe", binary="/x/sponsio")
    hooks = _claude_hooks(r.config_path)
    assert set(hooks) == {"PreToolUse", "PostToolUse"}
    for event, entries in hooks.items():
        assert len(entries) == 1
        h = entries[0]
        assert "sponsio_shim.py claude-code" in h["command"]
        assert h["command"].endswith("--mode observe --sponsio /x/sponsio")
        assert h["timeout"] == 10
        if event == "PreToolUse":
            assert h["onFailure"] == "block"
        else:
            assert "onFailure" not in h

    # A reinstall switches the mode and replaces, not duplicates.
    r = install(spec, mode="enforce", binary="/x/sponsio")
    cmds = _claude_commands(r.config_path)
    assert len(cmds) == 2 and all(
        c.endswith("--deadline 2 --sponsio /x/sponsio") for c in cmds
    )


def test_a_reinstall_replaces_hooks_of_every_earlier_generation(isolated_home):
    from sponsio.integrations.claude_code_install import install, uninstall

    spec = hosts.get("claude-code")
    settings = spec.config_path_user
    assert settings.is_relative_to(isolated_home), settings
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "*",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "sponsio plugin guard --stdin",
                                }
                            ],
                        },
                        {
                            "matcher": "*",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "env SPONSIO_GUARD_MODE=observe "
                                    "/old/sponsio host guard claude-code --stdin",
                                }
                            ],
                        },
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "mine"}],
                        },
                    ]
                }
            }
        )
    )
    install(spec)
    hooks = _claude_hooks(settings)
    pre = [h["command"] for h in hooks["PreToolUse"]]
    assert pre[0] == "mine"
    assert len(pre) == 2 and "sponsio_shim.py" in pre[1]
    r = uninstall(spec)
    assert r.removed_entries == 2
    assert _claude_commands(settings) == ["mine"]


def test_cursor_install_writes_the_shim_per_event(isolated_home):
    from sponsio.integrations.cursor_install import install

    r = install(hosts.get("cursor"), mode="observe", binary="/x/sponsio")
    data = json.loads(Path(r.config_path).read_text())
    for event, entries in data["hooks"].items():
        assert len(entries) == 1
        cmd = entries[0]["command"]
        assert f"sponsio_shim.py cursor --event {event} " in cmd
        assert cmd.endswith("--mode observe --sponsio /x/sponsio")
        assert "SPONSIO_GUARD_MODE" not in cmd
    assert data["hooks"]["beforeShellExecution"][0]["failClosed"] is True


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


def test_openclaw_install_writes_the_plugin_config(isolated_home, monkeypatch):
    from sponsio.integrations.openclaw_install import PLUGIN_CONFIG, install, uninstall

    oc = isolated_home / ".openclaw"
    oc.mkdir()
    (oc / "openclaw.json").write_text("{}")
    monkeypatch.setenv("OPENCLAW_HOME", str(oc))
    spec = hosts.get("openclaw")
    install(spec, mode="observe", binary="/x/sponsio --flag")
    conf_path = oc / "extensions" / "sponsio-openclaw" / PLUGIN_CONFIG
    assert conf_path.is_relative_to(isolated_home)
    conf = json.loads(conf_path.read_text())
    assert conf["mode"] == "observe"
    assert conf["sponsio"] == ["/x/sponsio", "--flag"]
    assert conf["deadlineMs"] == 2000
    for key in ("socketPath", "pluginRoot", "stateDir"):
        assert Path(conf[key]).is_absolute()
        assert Path(conf[key]).resolve().is_relative_to(isolated_home.resolve())

    # A reinstall rewrites the config even when the extension is current.
    install(spec, mode="enforce")
    conf = json.loads(conf_path.read_text())
    assert conf["mode"] == "enforce" and conf["sponsio"][1:] == ["-m", "sponsio.cli"]

    uninstall(spec)
    assert not conf_path.exists()

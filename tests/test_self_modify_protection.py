"""Integration tests for self-modification protection packs.

Two packs cooperate to defend against agent-mediated edits to
Sponsio's own configuration tree:

* ``sponsio:capability/self-modify`` (loaded by ``_host``) blocks
  the host agent from editing its OWN ``_host/sponsio.yaml`` while
  leaving sibling slots and per-plugin libraries open.  This is
  the privilege-management story: parents can manage children,
  parents cannot rewrite themselves.
* ``sponsio:incident/subagent-escape`` (loaded by ``_host_subagent``)
  denies the entire ``~/.sponsio/`` tree to sub-agents — including
  reads of the parent's rule list (recon precedes attack), and
  including the project-level ``sponsio.yaml`` / ``.sponsiorc``.

Each test renders the relevant Claude Code hook event, runs
``guard_stdin.run_stdin`` against an isolated HOME, and asserts the
expected allow / deny decision.  The split mirrors
``test_claude_code_secret_bypass.py``'s style.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from sponsio.guard_stdin import run_stdin


# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------


def _make_host_lib(home: Path) -> None:
    """``_host`` library that includes capability/self-modify only."""
    lib_dir = home / ".sponsio" / "plugins" / "_host"
    lib_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "sponsio.yaml").write_text(
        textwrap.dedent(
            """
            version: "1"
            agents:
              _host:
                include:
                  - sponsio:capability/self-modify
            """
        ).lstrip()
    )


def _make_subagent_lib(home: Path) -> None:
    """``_host_subagent`` library that includes incident/subagent-escape."""
    lib_dir = home / ".sponsio" / "plugins" / "_host_subagent"
    lib_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "sponsio.yaml").write_text(
        textwrap.dedent(
            """
            version: "1"
            agents:
              _host_subagent:
                include:
                  - sponsio:incident/subagent-escape
            """
        ).lstrip()
    )


def _hook_event(
    tool_name: str,
    file_path: str,
    *,
    is_subagent: bool = False,
) -> str:
    """Render a Claude Code PreToolUse hook event as stdin JSON.

    ``is_subagent=True`` injects an ``agent_id`` field so
    ``derive_plugin_id`` routes the call to the ``_host_subagent``
    library instead of ``_host``.  Mirrors how Claude Code stamps
    Task-spawned sub-agent calls.
    """
    payload: dict = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path},
    }
    if is_subagent:
        # ``agent_id`` presence is what flips the routing.  The exact
        # value doesn't matter — derive_plugin_id only checks for
        # the field's existence to decide subagent-vs-main.
        payload["agent_id"] = "test-subagent"
    return json.dumps(payload)


def _run(stdin: str, capsys) -> tuple[str, int]:
    code = run_stdin(stdin)
    captured = capsys.readouterr()
    return captured.out, code


def _assert_denied(out: str) -> None:
    assert out, "expected a deny payload, got empty stdout (allow)"
    payload = json.loads(out)
    decision = payload["hookSpecificOutput"]["permissionDecision"]
    assert decision == "deny", f"expected deny, got {decision}: {payload}"


def _assert_allowed(out: str, code: int) -> None:
    assert code == 0, f"expected exit 0, got {code}"
    assert out == "", f"expected empty stdout (allow), got {out!r}"


@pytest.fixture
def host_home(tmp_path, monkeypatch):
    """HOME with capability/self-modify wired into _host only."""
    home = tmp_path / "home"
    home.mkdir()
    trace_root = tmp_path / "shield-traces"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(home / ".sponsio" / "plugins"))
    monkeypatch.setenv("SPONSIO_SHIELD_TRACE_ROOT", str(trace_root))
    monkeypatch.setenv("SPONSIO_GUARD_MODE", "enforce")
    _make_host_lib(home)
    yield home


@pytest.fixture
def subagent_home(tmp_path, monkeypatch):
    """HOME with both _host and _host_subagent libraries.

    The sub-agent library carries the incident/subagent-escape pack;
    the main _host library is intentionally minimal so the test
    isolates subagent-only behavior.
    """
    home = tmp_path / "home"
    home.mkdir()
    trace_root = tmp_path / "shield-traces"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(home / ".sponsio" / "plugins"))
    monkeypatch.setenv("SPONSIO_SHIELD_TRACE_ROOT", str(trace_root))
    monkeypatch.setenv("SPONSIO_GUARD_MODE", "enforce")
    # Need both — _host so non-subagent calls have somewhere to land,
    # _host_subagent for the actual rules under test.
    _make_host_lib(home)
    _make_subagent_lib(home)
    yield home


# ---------------------------------------------------------------------------
# §1 — capability/self-modify: host agent can't touch its OWN rule file
# ---------------------------------------------------------------------------


class TestSelfModifyDenies:
    """The host agent attempting to Edit / Write / MultiEdit the
    literal ``~/.sponsio/plugins/_host/sponsio.yaml`` slot must DENY.

    These mirror the actual incident this pack defends against:
    Claude Code in auto mode trying to add an ``customized:`` block
    to disable a rule that just blocked it.
    """

    def test_edit_own_host_yaml_denied(self, host_home, capsys):
        out, _ = _run(
            _hook_event("Edit", str(host_home / ".sponsio/plugins/_host/sponsio.yaml")),
            capsys,
        )
        _assert_denied(out)

    def test_write_own_host_yaml_denied(self, host_home, capsys):
        out, _ = _run(
            _hook_event(
                "Write", str(host_home / ".sponsio/plugins/_host/sponsio.yaml")
            ),
            capsys,
        )
        _assert_denied(out)

    def test_multiedit_own_host_yaml_denied(self, host_home, capsys):
        out, _ = _run(
            _hook_event(
                "MultiEdit", str(host_home / ".sponsio/plugins/_host/sponsio.yaml")
            ),
            capsys,
        )
        _assert_denied(out)


class TestSelfModifyAllows:
    """False-positive guard: the host SHOULD be able to manage
    children (sub-agent libraries, per-plugin libraries) and the
    project-level ``sponsio.yaml`` it just wrote with ``onboard``.
    Only its OWN file is locked.
    """

    def test_edit_subagent_lib_allowed(self, host_home, capsys):
        """Parent managing child policy is the legitimate workflow.

        The shared ``capability/self-modify`` pack only locks the
        running bucket's OWN file. ``_host`` (legacy) editing
        ``_host_subagent/`` from the same agent context is the
        parent-tightens-child workflow Sponsio explicitly preserves;
        same for the per-host buckets, where each starter only locks
        its own ``_host_<host>/sponsio.yaml`` and leaves siblings
        editable.
        """
        out, code = _run(
            _hook_event(
                "Edit",
                str(host_home / ".sponsio/plugins/_host_subagent/sponsio.yaml"),
            ),
            capsys,
        )
        _assert_allowed(out, code)

    def test_edit_per_plugin_lib_denied(self, host_home, capsys):
        """Per-plugin bundles (`<plugin-id>/sponsio.yaml`) are user-only.

        The legitimate add/update path is `sponsio plugin install`,
        `sponsio plugin scan --apply`, or hand-editing in the user's
        text editor — none of which go through the agent's Edit/Write
        tool surface. The IDE coding agent reaching in directly to
        rewrite a bundle that's about to fire against it is
        privilege escalation, same shape as the host-bucket case.
        """
        out, _ = _run(
            _hook_event(
                "Edit",
                str(host_home / ".sponsio/plugins/my-plugin/sponsio.yaml"),
            ),
            capsys,
        )
        _assert_denied(out)

    def test_write_per_plugin_lib_denied(self, host_home, capsys):
        out, _ = _run(
            _hook_event(
                "Write",
                str(host_home / ".sponsio/plugins/github/sponsio.yaml"),
            ),
            capsys,
        )
        _assert_denied(out)

    def test_multiedit_per_plugin_lib_denied(self, host_home, capsys):
        out, _ = _run(
            _hook_event(
                "MultiEdit",
                str(host_home / ".sponsio/plugins/filesystem/sponsio.yaml"),
            ),
            capsys,
        )
        _assert_denied(out)

    def test_edit_project_sponsio_yaml_allowed(self, host_home, capsys, tmp_path):
        """The project-level yaml IS supposed to evolve via onboard / scan."""
        project_yaml = tmp_path / "project" / "sponsio.yaml"
        out, code = _run(_hook_event("Edit", str(project_yaml)), capsys)
        _assert_allowed(out, code)

    def test_read_own_host_yaml_allowed(self, host_home, capsys):
        """capability/self-modify is write-only — reads stay open.

        (The subagent-escape pack adds the read-deny for its OWN
        threat model; main agents have a legitimate need to read
        their own rule list to debug guard behavior.)
        """
        out, code = _run(
            _hook_event("Read", str(host_home / ".sponsio/plugins/_host/sponsio.yaml")),
            capsys,
        )
        _assert_allowed(out, code)


# ---------------------------------------------------------------------------
# §2 — incident/subagent-escape: sub-agents can't reach UP
# ---------------------------------------------------------------------------


class TestSubagentEscapeDenies:
    """A sub-agent (agent_id present in payload) attempting to touch
    any file under ``~/.sponsio/`` — or the project's
    ``sponsio.yaml`` / ``.sponsiorc`` — must DENY.
    """

    def test_subagent_edit_parent_host_yaml_denied(self, subagent_home, capsys):
        """Privilege escalation: child rewriting parent's rule list."""
        out, _ = _run(
            _hook_event(
                "Edit",
                str(subagent_home / ".sponsio/plugins/_host/sponsio.yaml"),
                is_subagent=True,
            ),
            capsys,
        )
        _assert_denied(out)

    def test_subagent_edit_own_lib_denied(self, subagent_home, capsys):
        """Self-modify: child rewriting its OWN rule list."""
        out, _ = _run(
            _hook_event(
                "Edit",
                str(subagent_home / ".sponsio/plugins/_host_subagent/sponsio.yaml"),
                is_subagent=True,
            ),
            capsys,
        )
        _assert_denied(out)

    def test_subagent_write_per_plugin_lib_denied(self, subagent_home, capsys):
        """Lateral: child rewriting a per-plugin library a sibling uses."""
        out, _ = _run(
            _hook_event(
                "Write",
                str(subagent_home / ".sponsio/plugins/my-plugin/sponsio.yaml"),
                is_subagent=True,
            ),
            capsys,
        )
        _assert_denied(out)

    def test_subagent_edit_project_sponsio_yaml_denied(
        self, subagent_home, capsys, tmp_path
    ):
        """Project-level: child rewriting the contracts the user just
        carefully authored via ``sponsio onboard``."""
        out, _ = _run(
            _hook_event(
                "Edit", str(tmp_path / "project" / "sponsio.yaml"), is_subagent=True
            ),
            capsys,
        )
        _assert_denied(out)

    def test_subagent_edit_sponsiorc_denied(self, subagent_home, capsys, tmp_path):
        out, _ = _run(
            _hook_event(
                "Edit", str(tmp_path / "project" / ".sponsiorc"), is_subagent=True
            ),
            capsys,
        )
        _assert_denied(out)

    def test_subagent_read_parent_host_yaml_denied(self, subagent_home, capsys):
        """Recon defence: subagents can't even READ the parent's rules."""
        out, _ = _run(
            _hook_event(
                "Read",
                str(subagent_home / ".sponsio/plugins/_host/sponsio.yaml"),
                is_subagent=True,
            ),
            capsys,
        )
        _assert_denied(out)

    def test_subagent_read_own_lib_denied(self, subagent_home, capsys):
        out, _ = _run(
            _hook_event(
                "Read",
                str(subagent_home / ".sponsio/plugins/_host_subagent/sponsio.yaml"),
                is_subagent=True,
            ),
            capsys,
        )
        _assert_denied(out)


class TestSubagentEscapeAllows:
    """False-positive guards: subagents can still do legitimate work.

    The pack denies sponsio config paths, not the entire filesystem.
    Reading a per-plugin library (might carry domain context) and
    editing arbitrary project files are both allowed — only sponsio
    configuration is closed off.
    """

    def test_subagent_read_per_plugin_lib_allowed(self, subagent_home, capsys):
        """Per-plugin libraries can carry tool-domain context the
        subagent legitimately needs.  Only the host rule lists
        (``_host`` / ``_host_subagent`` / ``_host_openclaw``) are
        recon-denied."""
        out, code = _run(
            _hook_event(
                "Read",
                str(subagent_home / ".sponsio/plugins/my-plugin/sponsio.yaml"),
                is_subagent=True,
            ),
            capsys,
        )
        _assert_allowed(out, code)

    def test_subagent_edit_unrelated_file_allowed(
        self, subagent_home, capsys, tmp_path
    ):
        out, code = _run(
            _hook_event(
                "Edit", str(tmp_path / "src" / "unrelated.py"), is_subagent=True
            ),
            capsys,
        )
        _assert_allowed(out, code)


# ---------------------------------------------------------------------------
# §4 / §5 — interpreter bypass (the "python file I/O" workaround)
#
# Observed in the wild during the cursor-live demo: when §1–§3 deny
# Edit/Write of the host bucket, the agent recovers by writing a
# small Python helper in the workspace whose content opens the host
# bucket path in write mode, then runs ``python merge.py``.  The
# ``Write`` of the helper is NOT in the host bucket so §1–§3 don't
# fire; the ``Bash`` command line doesn't carry the host bucket
# path so the bash-redirect rules (in the per-host bucket starters)
# also don't fire.  §4 catches the script-creation step (content
# mentions the path AND uses an open-for-write idiom); §5 catches
# the inline interpreter form (`python -c "...sponsio/plugins..."`)
# and the path-as-arg form (`python merge.py ~/.sponsio/...yaml`).
# ---------------------------------------------------------------------------


def _hook_event_with_content(tool_name: str, file_path: str, content: str) -> str:
    """Hook event for Write/Edit with content / new_string.

    Mirrors how Claude Code stamps the payload: ``tool_input`` carries
    the same parameter names the underlying tool exposes (``content``
    for Write, ``new_string`` for Edit, ``edits`` for MultiEdit).
    """
    if tool_name == "Write":
        tool_input = {"file_path": file_path, "content": content}
    elif tool_name == "Edit":
        tool_input = {
            "file_path": file_path,
            "old_string": "",
            "new_string": content,
        }
    else:
        raise ValueError(f"unsupported tool for content event: {tool_name}")
    return json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
    )


def _hook_event_bash(command: str) -> str:
    return json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
    )


class TestSelfModifyInterpreterBypassDenies:
    """The bypass family: §4 (script creation) + §5 (interpreter
    invocation) must DENY each shape the live agent has been observed
    to produce.
    """

    # ---- §4: Write a script that targets host bucket --------------------

    def test_write_python_script_targeting_host_bucket_denied(self, host_home, capsys):
        """The exact shape the cursor-live demo produced: a `.py` file
        whose content references the host bucket path AND opens it
        for write."""
        content = (
            "from pathlib import Path\n"
            "HOST = Path.home() / '.sponsio/plugins/_host_cursor/sponsio.yaml'\n"
            "HOST.write_text('agents:\\n  _host_cursor:\\n    contracts: []\\n')\n"
        )
        out, _ = _run(
            _hook_event_with_content(
                "Write",
                str(host_home / "merge_host_cursor_sponsio.py"),
                content,
            ),
            capsys,
        )
        _assert_denied(out)

    def test_write_python_script_using_open_w_denied(self, host_home, capsys):
        """Variant using the lower-level ``open(..., 'w')`` form."""
        content = (
            "with open('/Users/x/.sponsio/plugins/_host_cursor/sponsio.yaml', 'w') as f:\n"
            "    f.write('overwritten')\n"
        )
        out, _ = _run(
            _hook_event_with_content("Write", str(host_home / "patch.py"), content),
            capsys,
        )
        _assert_denied(out)

    def test_write_node_script_using_fs_writefilesync_denied(self, host_home, capsys):
        """The Node.js variant — same shape, different runtime."""
        content = (
            "const fs = require('fs');\n"
            "fs.writeFileSync('/Users/x/.sponsio/plugins/_host_cursor/sponsio.yaml', '...');\n"
        )
        out, _ = _run(
            _hook_event_with_content("Write", str(host_home / "patch.mjs"), content),
            capsys,
        )
        _assert_denied(out)

    def test_edit_inserting_host_bucket_write_denied(self, host_home, capsys):
        """``Edit.new_string`` carrying the same shape as a Write."""
        new_string = (
            "from pathlib import Path\n"
            "Path.home() / '.sponsio/plugins/_host_cursor/sponsio.yaml'\n"
            "f.write(yaml.dump(doc))"
        )
        out, _ = _run(
            _hook_event_with_content(
                "Edit", str(host_home / "tools" / "patch.py"), new_string
            ),
            capsys,
        )
        _assert_denied(out)

    # ---- §4 false-positive guards --------------------------------------

    def test_write_docs_mentioning_path_no_write_shape_allowed(self, host_home, capsys):
        """Docs / README mentioning the host bucket path but not
        carrying any write-shape code must stay editable — the rule is
        narrow on purpose."""
        content = (
            "# How Sponsio's host bucket works\n\n"
            "The runtime library lives at "
            "`~/.sponsio/plugins/_host_cursor/sponsio.yaml`. "
            "Edit it via `sponsio plugin append` only.\n"
        )
        out, code = _run(
            _hook_event_with_content("Write", str(host_home / "README.md"), content),
            capsys,
        )
        _assert_allowed(out, code)

    def test_write_python_with_write_shape_unrelated_path_allowed(
        self, host_home, capsys
    ):
        """A script that uses ``write_text`` against an unrelated path
        is fine — only the combination with a host-bucket path is bad."""
        content = (
            "from pathlib import Path\nPath('/tmp/output.txt').write_text('hello')\n"
        )
        out, code = _run(
            _hook_event_with_content(
                "Write", str(host_home / "src" / "tool.py"), content
            ),
            capsys,
        )
        _assert_allowed(out, code)

    # ---- §5: Bash interpreter targeting host bucket --------------------

    def test_bash_python_dash_c_targeting_host_bucket_denied(self, host_home, capsys):
        """Inline ``python -c "..."`` whose script body references
        the host bucket path."""
        cmd = (
            'python3 -c "'
            "import yaml; "
            "open('/Users/x/.sponsio/plugins/_host_cursor/sponsio.yaml', 'w').write('...')\""
        )
        out, _ = _run(_hook_event_bash(cmd), capsys)
        _assert_denied(out)

    def test_bash_python_script_with_path_arg_denied(self, host_home, capsys):
        """``python merge.py ~/.sponsio/plugins/.../sponsio.yaml`` —
        path passed as a positional arg.  Even when the script file
        name is benign, the trailing path on the command line is a
        clear signal."""
        cmd = "python3 merge.py ~/.sponsio/plugins/_host_cursor/sponsio.yaml"
        out, _ = _run(_hook_event_bash(cmd), capsys)
        _assert_denied(out)

    def test_bash_node_eval_targeting_host_bucket_denied(self, host_home, capsys):
        cmd = (
            "node -e \"require('fs').writeFileSync("
            "'/Users/x/.sponsio/plugins/_host_cursor/sponsio.yaml', '...')\""
        )
        out, _ = _run(_hook_event_bash(cmd), capsys)
        _assert_denied(out)

    # ---- §5 false-positive guards --------------------------------------

    def test_bash_sponsio_validate_on_host_bucket_allowed(self, host_home, capsys):
        """``sponsio validate --config <host-path>`` is a legitimate
        read-only command and must NOT be blocked.  The leading verb
        is ``sponsio``, not an interpreter alias."""
        cmd = "sponsio validate --config ~/.sponsio/plugins/_host_cursor/sponsio.yaml"
        out, code = _run(_hook_event_bash(cmd), capsys)
        _assert_allowed(out, code)

    def test_bash_sponsio_plugin_append_allowed(self, host_home, capsys):
        """The blessed CLI route — never carries the host-bucket
        path on the command line, just the plugin id."""
        cmd = "sponsio plugin append --from .sponsio.staging.yaml --target _host_cursor"
        out, code = _run(_hook_event_bash(cmd), capsys)
        _assert_allowed(out, code)

    def test_bash_python_unrelated_script_allowed(self, host_home, capsys):
        """``python anything.py`` with no host-bucket path mention is
        ordinary scripting — must not be blocked."""
        cmd = "python3 src/build_release.py --version 1.2.3"
        out, code = _run(_hook_event_bash(cmd), capsys)
        _assert_allowed(out, code)

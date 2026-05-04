"""Tests for ``sponsio init`` (4-axis wizard) and underlying helpers.

Covers four layers:

* :func:`parse_picks` / :func:`format_picks` — pure string round-trip.
* :func:`plan_commands` — picks → argv vectors, the dispatch table the
  TTY path and the IDE-agent-driven ``--apply`` path both share.
* :func:`detect_environment` — runtime + framework + IDE-binary probe.
* CLI invocation via :class:`click.testing.CliRunner` — ``--plan``,
  ``--apply`` (with mocked subprocess), ``--with-example``,
  mutually-exclusive flag handling.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from sponsio.cli import init
from sponsio.init_wizard import (
    InitPicks,
    apply_commands,
    detect_environment,
    format_picks,
    parse_picks,
    plan_commands,
)


# ---------------------------------------------------------------------------
# parse_picks / format_picks — pure round-trip
# ---------------------------------------------------------------------------


class TestParsePicks:
    def test_full_spec_round_trips(self):
        spec = (
            "framework=langgraph;"
            "hosts=claude-code,cursor;"
            "skills=codex;"
            "mode=enforce"
        )
        p = parse_picks(spec)
        assert p.framework == "langgraph"
        assert p.hosts == ["claude-code", "cursor"]
        assert p.skills == ["codex"]
        assert p.mode == "enforce"
        # round-trip stable
        assert parse_picks(format_picks(p)) == p

    def test_empty_string_returns_default_picks(self):
        p = parse_picks("")
        assert p == InitPicks()

    def test_unknown_segments_are_silently_ignored(self):
        # Forward-compat: a future axis added by the IDE agent must
        # not break older CLI versions.
        p = parse_picks("framework=langgraph;quantum=spooky;mode=observe")
        assert p.framework == "langgraph"
        assert p.mode == "observe"

    def test_empty_value_lists_are_explicit(self):
        # ``hosts=`` means "no hosts picked", distinct from "hosts not
        # mentioned" (which also defaults to []).  Round-trip must
        # preserve the empty list.
        p = parse_picks("framework=none;hosts=;skills=;mode=observe")
        assert p.hosts == []
        assert p.skills == []

    def test_whitespace_in_values_stripped(self):
        p = parse_picks("hosts= claude-code , cursor ;mode=observe")
        assert p.hosts == ["claude-code", "cursor"]


# ---------------------------------------------------------------------------
# plan_commands — picks → argv vectors
# ---------------------------------------------------------------------------


class TestPlanCommands:
    def test_axis1_python_emits_sponsio_onboard(self):
        cmds = plan_commands(
            InitPicks(framework="langgraph", mode="observe"),
            ts_project=False,
        )
        assert cmds == [
            ["sponsio", "onboard", ".", "--mode", "observe", "--force"],
        ]

    def test_axis1_ts_emits_npx_sponsio_onboard(self):
        cmds = plan_commands(
            InitPicks(framework="langgraph", mode="observe"),
            ts_project=True,
        )
        assert cmds == [
            ["npx", "sponsio", "onboard", ".", "--mode", "observe", "--force"],
        ]

    def test_axis1_none_skips_onboard(self):
        # User picks "none" (bare loop / I'll wire it myself) — no
        # framework wrap to install.
        cmds = plan_commands(InitPicks(framework="none", mode="observe"))
        assert cmds == []

    def test_axis2_emits_host_install_with_mode(self):
        cmds = plan_commands(
            InitPicks(
                framework="none",
                hosts=["claude-code", "cursor"],
                mode="enforce",
            )
        )
        assert cmds == [
            [
                "sponsio",
                "host",
                "install",
                "claude-code",
                "cursor",
                "--mode",
                "enforce",
            ],
        ]

    def test_axis2_filters_unknown_host_names(self):
        cmds = plan_commands(
            InitPicks(
                framework="none",
                hosts=["claude-code", "definitely-not-a-host"],
                mode="observe",
            )
        )
        # Typo silently dropped.  Ordered axes still emit a command
        # for the surviving picks.
        assert cmds == [
            ["sponsio", "host", "install", "claude-code", "--mode", "observe"]
        ]

    def test_axis3_only_runs_for_skills_not_in_axis2(self):
        # claude-code in BOTH axis 2 and 3: axis 2's --with-skill
        # default already covers it, so axis 3 doesn't double-drop.
        cmds = plan_commands(
            InitPicks(
                framework="none",
                hosts=["claude-code"],
                skills=["claude-code", "codex"],
                mode="observe",
            )
        )
        # Expect host install + ONE skill install (codex), not two.
        assert cmds == [
            ["sponsio", "host", "install", "claude-code", "--mode", "observe"],
            ["sponsio", "skill", "install", "--tool", "codex"],
        ]

    def test_full_4axis_combo(self):
        cmds = plan_commands(
            InitPicks(
                framework="langgraph",
                hosts=["claude-code", "cursor"],
                skills=["codex"],
                mode="enforce",
            )
        )
        assert cmds == [
            ["sponsio", "onboard", ".", "--mode", "enforce", "--force"],
            [
                "sponsio",
                "host",
                "install",
                "claude-code",
                "cursor",
                "--mode",
                "enforce",
            ],
            ["sponsio", "skill", "install", "--tool", "codex"],
        ]


# ---------------------------------------------------------------------------
# detect_environment — runtime + framework + IDE binary probe
# ---------------------------------------------------------------------------


class TestDetectEnvironment:
    def test_python_project_with_langgraph_import(self, tmp_path: Path):
        (tmp_path / "agent.py").write_text(
            "from langgraph.prebuilt import create_react_agent\n"
        )
        env = detect_environment(tmp_path)
        assert env.runtime == "python"
        assert env.framework == "langgraph"

    def test_ts_only_project(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name":"x"}\n')
        env = detect_environment(tmp_path)
        assert env.runtime == "ts"

    def test_dual_runtime_when_both_signals_present(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name":"x"}\n')
        (tmp_path / "pyproject.toml").write_text("[project]\nname='y'\n")
        env = detect_environment(tmp_path)
        assert env.runtime == "both"

    def test_ides_installed_filtered_to_real_binaries(
        self, tmp_path: Path, monkeypatch
    ):
        # Stub `shutil.which` so the test doesn't depend on what the
        # CI box has installed.
        installed = {"claude": True, "cursor": False, "openclaw": False}

        def fake_which(name):
            return f"/usr/local/bin/{name}" if installed.get(name) else None

        monkeypatch.setattr("sponsio.init_wizard.shutil.which", fake_which)
        env = detect_environment(tmp_path)
        assert env.ides_installed == ["claude-code"]


# ---------------------------------------------------------------------------
# apply_commands — runs subprocess + stops on non-zero exit
# ---------------------------------------------------------------------------


class TestApplyCommands:
    def test_apply_runs_each_command_in_order(self):
        calls: list[list[str]] = []

        class FakeResult:
            returncode = 0

        def runner(cmd, **_):
            calls.append(cmd)
            return FakeResult()

        rc = apply_commands(
            [["echo", "a"], ["echo", "b"]],
            runner=runner,
        )
        assert rc == 0
        assert calls == [["echo", "a"], ["echo", "b"]]

    def test_apply_stops_at_first_nonzero_exit(self):
        calls: list[list[str]] = []

        class FakeResult:
            def __init__(self, rc):
                self.returncode = rc

        def runner(cmd, **_):
            calls.append(cmd)
            return FakeResult(0 if cmd == ["echo", "first"] else 7)

        rc = apply_commands(
            [["echo", "first"], ["echo", "second"], ["echo", "third"]],
            runner=runner,
        )
        # First succeeded, second returned 7, third never ran.
        assert rc == 7
        assert calls == [["echo", "first"], ["echo", "second"]]


# ---------------------------------------------------------------------------
# CLI surface — `sponsio init --plan` / `--apply`
# ---------------------------------------------------------------------------


class TestCliPlan:
    def test_plan_prints_would_run_lines(self, tmp_path: Path):
        # `--plan` is read-only; no subprocess + no prompts.
        (tmp_path / "agent.py").write_text(
            "from langgraph.prebuilt import create_react_agent\n"
        )
        runner = CliRunner()
        result = runner.invoke(
            init,
            [
                str(tmp_path),
                "--plan",
                "framework=langgraph;hosts=claude-code;mode=observe",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "would run: sponsio onboard ." in result.output
        assert (
            "would run: sponsio host install claude-code --mode observe"
            in result.output
        )

    def test_plan_with_empty_picks_says_so(self, tmp_path: Path):
        runner = CliRunner()
        result = runner.invoke(
            init,
            [str(tmp_path), "--plan", "framework=none;mode=observe"],
        )
        assert result.exit_code == 0, result.output
        assert "no commands" in result.output


class TestCliApply:
    def test_apply_runs_planned_commands_via_subprocess(
        self, tmp_path: Path, monkeypatch
    ):
        # Stub subprocess.run so apply doesn't actually execute
        # `sponsio onboard` / `sponsio host install` against the
        # test machine's real plugin tree.
        called: list[list[str]] = []

        class FakeResult:
            returncode = 0

        def fake_run(cmd, **_):
            called.append(cmd)
            return FakeResult()

        monkeypatch.setattr("sponsio.init_wizard.subprocess.run", fake_run)

        # Stub the demo offer's tty check to bypass the post-install
        # confirm prompt.
        monkeypatch.setattr(
            "sponsio.init_wizard.sys.stdin.isatty", lambda: False
        )

        (tmp_path / "agent.py").write_text(
            "from langgraph.prebuilt import create_react_agent\n"
        )
        runner = CliRunner()
        result = runner.invoke(
            init,
            [
                str(tmp_path),
                "--apply",
                "framework=langgraph;mode=observe",
            ],
        )
        assert result.exit_code == 0, result.output
        assert called == [
            ["sponsio", "onboard", ".", "--mode", "observe", "--force"]
        ]


class TestCliMutualExclusion:
    def test_plan_and_apply_are_mutually_exclusive(self, tmp_path: Path):
        runner = CliRunner()
        result = runner.invoke(
            init,
            [
                str(tmp_path),
                "--plan",
                "framework=langgraph;mode=observe",
                "--apply",
                "framework=langgraph;mode=observe",
            ],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output

    def test_with_example_conflicts_with_plan(self, tmp_path: Path):
        runner = CliRunner()
        result = runner.invoke(
            init,
            [
                str(tmp_path),
                "--with-example",
                "--plan",
                "framework=langgraph;mode=observe",
            ],
        )
        assert result.exit_code != 0
        assert "incompatible with" in result.output

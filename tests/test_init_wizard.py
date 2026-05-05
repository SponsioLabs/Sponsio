"""Tests for ``sponsio init`` (4-axis wizard) and underlying helpers.

Covers four layers:

* :func:`parse_picks` / :func:`format_picks` — pure string round-trip.
* :func:`plan_commands` — picks → argv vectors, the dispatch table the
  TTY path and the IDE-agent-driven ``--apply`` path both share.
* :func:`detect_environment` — runtime + framework + IDE-binary probe.
* CLI invocation via :class:`click.testing.CliRunner` — ``--plan``,
  ``--apply`` (with mocked subprocess), and mutually-exclusive flag
  handling.
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
    def test_native_ides_form_round_trips(self):
        spec = "framework=langgraph;ides=claude-code:full,cursor:skill;mode=enforce"
        p = parse_picks(spec)
        assert p.framework == "langgraph"
        assert p.ide_levels == {"claude-code": "full", "cursor": "skill"}
        assert p.hosts == ["claude-code"]  # derived from full
        assert p.skills == ["cursor"]  # derived from skill
        assert p.mode == "enforce"
        # round-trip stable
        assert parse_picks(format_picks(p)) == p

    def test_legacy_hosts_skills_form_compat(self):
        # The IDE agent wizard prompt v2 used hosts=/skills=; older
        # callers must keep working without rewriting their picks.
        p = parse_picks(
            "framework=none;hosts=claude-code,cursor;skills=codex;mode=observe"
        )
        assert p.ide_levels == {
            "claude-code": "full",
            "cursor": "full",
            "codex": "skill",
        }
        assert p.hosts == ["claude-code", "cursor"]
        assert p.skills == ["codex"]

    def test_legacy_full_supersedes_skill_no_demotion(self):
        # ``hosts=X;skills=X`` shouldn't demote X to skill-only.  Full
        # implies skill, so the more privileged pick wins.
        p = parse_picks("hosts=claude-code;skills=claude-code;mode=observe")
        assert p.ide_levels == {"claude-code": "full"}

    def test_empty_string_returns_default_picks(self):
        p = parse_picks("")
        assert p == InitPicks()

    def test_unknown_segments_are_silently_ignored(self):
        # Forward-compat: a future axis added by the IDE agent must
        # not break older CLI versions.
        p = parse_picks("framework=langgraph;quantum=spooky;mode=observe")
        assert p.framework == "langgraph"
        assert p.mode == "observe"

    def test_invalid_level_dropped_silently(self):
        # ``ides=X:nonsense`` ignored; X stays out of ide_levels.
        p = parse_picks("ides=claude-code:nonsense;mode=observe")
        assert p.ide_levels == {}

    def test_level_none_omitted_from_ide_levels(self):
        # ``ides=X:none`` is a deliberate "leave alone" pick.  Storing
        # it in ide_levels would falsely include X in plan_commands.
        p = parse_picks("ides=claude-code:none;mode=observe")
        assert p.ide_levels == {}

    def test_whitespace_in_values_stripped(self):
        p = parse_picks("ides= claude-code:full , cursor:skill ;mode=observe")
        assert p.ide_levels == {"claude-code": "full", "cursor": "skill"}


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

    def test_axis1_ts_observe_installs_then_runs_onboard(self):
        # ``npx sponsio onboard`` alone errors with "404 sponsio not
        # found on registry" because the npm package name is
        # ``@sponsio/sdk`` (whose bin is named ``sponsio``).
        # Plan must install the scoped pkg first so the binary lands
        # in node_modules/.bin where npx can resolve it locally.
        # ``observe`` is the scanner's default mode — no flip needed.
        cmds = plan_commands(
            InitPicks(framework="langgraph", mode="observe"),
            ts_project=True,
        )
        assert cmds == [
            ["npm", "install", "--save-dev", "@sponsio/sdk"],
            ["npx", "sponsio", "onboard", ".", "--force"],
        ]

    def test_axis1_ts_enforce_appends_mode_flip(self):
        # ``@sponsio/sdk@0.1.0-alpha.3`` doesn't accept ``--mode``
        # on the ``onboard`` subcommand.  Plan writes the yaml at
        # the scanner's default ``observe`` mode then flips to
        # enforce via the standalone ``sponsio mode`` subcommand
        # (which IS supported on that version).  This pattern
        # works on both old and new scanner versions.
        cmds = plan_commands(
            InitPicks(framework="langgraph", mode="enforce"),
            ts_project=True,
        )
        assert cmds == [
            ["npm", "install", "--save-dev", "@sponsio/sdk"],
            ["npx", "sponsio", "onboard", ".", "--force"],
            ["npx", "sponsio", "mode", "enforce"],
        ]

    def test_axis1_none_still_runs_onboard_for_bare_loop_scaffold(self):
        # ``framework=none`` is a real pick (bare function-calling
        # loop, generic ``import sponsio`` wiring).  ``sponsio
        # onboard`` handles it natively — writes a generic yaml +
        # emits the ``guard.guard_before/after`` snippet — so plan
        # MUST emit the onboard command.  Skipping it would leave
        # bare-loop users with no scaffold, which is the opposite
        # of what they came to the wizard for.
        cmds = plan_commands(InitPicks(framework="none", mode="observe"))
        assert cmds == [
            ["sponsio", "onboard", ".", "--mode", "observe", "--force"],
        ]

    def test_empty_framework_string_skips_onboard(self):
        # The truly-empty case (no framework axis answered at all)
        # is the one path that omits onboard.
        cmds = plan_commands(InitPicks(framework="", mode="observe"))
        assert cmds == []

    def test_full_level_emits_host_install(self):
        cmds = plan_commands(
            InitPicks(
                framework="",
                ide_levels={"claude-code": "full", "cursor": "full"},
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

    def test_skill_level_emits_skill_install_only(self):
        # ``skill`` IDEs do NOT go through ``host install`` — that
        # would also install hooks the user explicitly declined.
        cmds = plan_commands(
            InitPicks(
                framework="",
                ide_levels={"cursor": "skill", "codex": "skill"},
                mode="observe",
            )
        )
        assert cmds == [
            ["sponsio", "skill", "install", "--tool", "cursor", "--force"],
            ["sponsio", "skill", "install", "--tool", "codex", "--force"],
        ]

    def test_full_and_skill_mixed_per_ide(self):
        # claude-code → full (host hooks), cursor → skill only
        # (no hooks).  No double-install of the skill into
        # claude-code: ``host install --with-skill`` covers it.
        cmds = plan_commands(
            InitPicks(
                framework="",
                ide_levels={"claude-code": "full", "cursor": "skill"},
                mode="observe",
            )
        )
        assert cmds == [
            ["sponsio", "host", "install", "claude-code", "--mode", "observe"],
            ["sponsio", "skill", "install", "--tool", "cursor", "--force"],
        ]

    def test_unknown_ide_filtered_silently(self):
        cmds = plan_commands(
            InitPicks(
                framework="",
                ide_levels={"claude-code": "full", "definitely-not-a-host": "full"},
                mode="observe",
            )
        )
        assert cmds == [
            ["sponsio", "host", "install", "claude-code", "--mode", "observe"]
        ]

    def test_full_3axis_combo(self):
        cmds = plan_commands(
            InitPicks(
                framework="langgraph",
                ide_levels={
                    "claude-code": "full",
                    "cursor": "full",
                    "codex": "skill",
                },
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
            ["sponsio", "skill", "install", "--tool", "codex", "--force"],
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
                "framework=langgraph;ides=claude-code:full;mode=observe",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "would run: sponsio onboard ." in result.output
        assert (
            "would run: sponsio host install claude-code --mode observe"
            in result.output
        )

    def test_plan_accepts_legacy_hosts_skills_form(self, tmp_path: Path):
        # IDE agent prompts written against the v2 `hosts=`/`skills=`
        # contract must keep working without rewrites.
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
        assert (
            "would run: sponsio host install claude-code --mode observe"
            in result.output
        )

    def test_plan_with_empty_picks_says_so(self, tmp_path: Path):
        # ``framework=`` (empty) AND no IDE picks → no commands.
        # ``framework=none`` is no longer "empty" — it's a real
        # bare-loop integrate pick that still emits onboard.
        runner = CliRunner()
        result = runner.invoke(
            init,
            [str(tmp_path), "--plan", "framework=;mode=observe"],
        )
        assert result.exit_code == 0, result.output
        assert "Nothing to do" in result.output


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
        monkeypatch.setattr("sponsio.init_wizard.sys.stdin.isatty", lambda: False)

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
        assert called == [["sponsio", "onboard", ".", "--mode", "observe", "--force"]]


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

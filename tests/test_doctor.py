"""Unit tests for ``sponsio doctor``.

Each check is tested in isolation so one failure doesn't mask another.
We deliberately do NOT patch ``sys.modules`` to hide Sponsio itself —
those tests would run in a broken state and say nothing about the
command's correctness.  Instead the ``check_sponsio_import`` case is
covered by the success path (it's expected to pass in CI).
"""

from __future__ import annotations

import sys

import pytest

from sponsio.doctor import (
    CheckResult,
    check_guard_smoke,
    check_llm_credentials,
    check_llm_ping,
    check_mode,
    check_optional_sdks,
    check_project_scan,
    check_python,
    check_sponsio_import,
    check_sponsio_yaml,
    report_to_dict,
    run_doctor,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Reset the env vars that the doctor inspects so tests are deterministic.

    Each env-facing test re-sets the ones it needs.  Without this,
    a CI runner that has ``OPENAI_API_KEY`` would flip the expected
    branch in ``test_check_llm_credentials_warns_when_empty``.
    """
    for var in (
        "SPONSIO_MODE",
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


class TestCheckPython:
    def test_current_python_reports_ok_or_warn(self):
        r = check_python()
        assert r.status in ("ok", "warn")
        assert f"{sys.version_info.major}.{sys.version_info.minor}" in r.detail


class TestCheckSponsioImport:
    def test_succeeds_in_normal_environment(self):
        r = check_sponsio_import()
        assert r.status == "ok"
        # Always report a path so users can spot a shadowed install
        assert "/" in r.detail or r.detail


class TestCheckOptionalSDKs:
    def test_returns_result_even_when_none_present(self, monkeypatch):
        """A machine with zero optional SDKs still gets a report, not a
        traceback — ``find_spec`` returning ``None`` for every module is
        the ``skip`` case."""
        monkeypatch.setattr("importlib.util.find_spec", lambda _mod: None)
        r = check_optional_sdks()
        assert r.status == "skip"
        assert "none installed" in r.detail


class TestCheckLLMCredentials:
    def test_warns_when_empty(self):
        r = check_llm_credentials()
        assert r.status == "warn"
        assert "no LLM env" in r.detail

    def test_detects_gemini(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "x")
        r = check_llm_credentials()
        assert r.status == "ok"
        assert "Gemini" in r.detail

    def test_detects_anthropic(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        r = check_llm_credentials()
        assert r.status == "ok"
        assert "Anthropic" in r.detail

    def test_openai_base_url_takes_precedence(self, monkeypatch):
        """``OPENAI_BASE_URL`` overrides everything — it means the user
        has explicitly pointed at an OpenAI-compatible endpoint
        (Ollama, OpenRouter, vLLM) and that should be what we report."""
        monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setenv("GEMINI_API_KEY", "x")
        r = check_llm_credentials()
        assert r.status == "ok"
        assert "OPENAI_BASE_URL" in r.detail


class TestCheckMode:
    def test_unset_is_ok_observe_default(self):
        """Unset → observe (the safe default).  This used to ``warn``;
        the new policy is "shadow is fine, enforcement is what
        deserves a confirmation"."""
        r = check_mode()
        assert r.status == "ok"
        assert "observe" in r.detail

    def test_enforce_warns_now(self, monkeypatch):
        """``enforce`` is the dangerous mode — surfaced as ``warn``
        so the user actively confirms they ran ``sponsio eval`` first
        and the FPR is acceptable."""
        monkeypatch.setenv("SPONSIO_MODE", "enforce")
        r = check_mode()
        assert r.status == "warn"
        assert "BLOCK" in r.detail

    def test_observe_is_ok(self, monkeypatch):
        monkeypatch.setenv("SPONSIO_MODE", "observe")
        assert check_mode().status == "ok"

    def test_invalid_value_fails(self, monkeypatch):
        monkeypatch.setenv("SPONSIO_MODE", "yolo")
        r = check_mode()
        assert r.status == "fail"


class TestCheckProjectScan:
    def test_missing_path_is_skip(self, tmp_path):
        r = check_project_scan(tmp_path / "does_not_exist")
        assert r.status == "skip"

    def test_empty_project_is_skip(self, tmp_path):
        r = check_project_scan(tmp_path)
        assert r.status == "skip"
        assert "no .py files" in r.detail

    def test_scans_a_real_project(self, tmp_path):
        """Drop a toy agent file in a temp dir and confirm the scanner
        finds at least one contract (the tool looks destructive, which
        triggers ``destructive_idempotent``)."""
        (tmp_path / "agent.py").write_text(
            '''
from langchain_core.tools import tool

@tool
def delete_user(user_id: str) -> str:
    """Permanently remove a user account."""
    return "deleted"
'''
        )
        r = check_project_scan(tmp_path)
        assert r.status == "ok"
        assert "contract" in r.detail


class TestCheckGuardSmoke:
    def test_end_to_end_passes(self):
        """The happy path: import sponsio, build a guard with a NL
        contract, run a before+after cycle, verify the auto-tag event
        landed.  If any of those steps fail this check is the first
        thing to flip — it's the single highest-signal check in doctor.
        """
        r = check_guard_smoke()
        assert r.status == "ok", r.detail
        assert "auto-tag" in r.detail or "contains" in r.detail


# ---------------------------------------------------------------------------
# Full runner
# ---------------------------------------------------------------------------


class TestRunDoctor:
    def test_clean_environment_exits_zero(self, tmp_path, monkeypatch):
        """Every check either ``ok`` or ``warn`` → exit 0.  Warnings
        are advisory — they should *not* flip the exit code, so CI
        pre-flight gates can depend on ``doctor`` as a liveness probe
        without getting spurious failures from missing SDKs."""
        monkeypatch.setenv("SPONSIO_MODE", "observe")
        results, code = run_doctor(tmp_path)
        assert code == 0
        statuses = {r.name: r.status for r in results}
        assert statuses["Runtime mode"] == "ok"

    def test_invalid_mode_flips_exit_code(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SPONSIO_MODE", "whoops")
        results, code = run_doctor(tmp_path)
        assert code == 1
        modes = [r for r in results if r.name == "Runtime mode"]
        assert modes and modes[0].status == "fail"

    def test_every_check_returns_a_result_even_on_defect(self, tmp_path, monkeypatch):
        """Defensive: a raising check should surface as ``fail`` —
        never leak a traceback."""

        def _boom() -> CheckResult:
            raise RuntimeError("boom")

        monkeypatch.setattr("sponsio.doctor.check_python", _boom)
        results, code = run_doctor(tmp_path)
        assert code == 1
        # Exactly one ``fail`` from the patched check, all others
        # should keep running so we still get a useful report
        fails = [r for r in results if r.status == "fail"]
        assert len(fails) == 1
        assert "uncaught" in fails[0].detail


# ---------------------------------------------------------------------------
# YAML config check
# ---------------------------------------------------------------------------


class TestCheckSponsioYaml:
    def test_no_yaml_present_is_skip(self, tmp_path):
        """Optional file — absence shouldn't be a failure or even
        a warning, just a quiet ``skip`` with a hint."""
        r = check_sponsio_yaml(tmp_path)
        assert r.status == "skip"
        assert "no sponsio.yaml" in r.detail

    def test_directory_path_finds_yaml(self, tmp_path):
        (tmp_path / "sponsio.yaml").write_text(
            'version: 1\nagents:\n  bot:\n    contracts:\n      - E: "tool `x` at most 0 times"\n'
        )
        r = check_sponsio_yaml(tmp_path)
        assert r.status == "ok"
        assert "sponsio.yaml" in r.detail
        assert "1 agent(s)" in r.detail

    def test_file_path_used_directly(self, tmp_path):
        """``sponsio doctor sponsio.yaml`` should DTRT — point the
        checker at the file directly."""
        f = tmp_path / "sponsio.yaml"
        f.write_text("version: 1\n")
        r = check_sponsio_yaml(f)
        assert r.status == "ok"

    def test_invalid_yaml_fails(self, tmp_path):
        """Schema errors must surface with the file name so users
        know where to look."""
        (tmp_path / "sponsio.yaml").write_text(
            "version: 1\nextractor: 'not a mapping'\n"
        )
        r = check_sponsio_yaml(tmp_path)
        assert r.status == "fail"
        assert "sponsio.yaml" in r.detail

    def test_unset_env_var_is_warned(self, tmp_path, monkeypatch):
        """The loader silently expands missing ``${VAR}`` to ``""``
        — doctor's job is to surface that BEFORE the user runs
        scan and gets a confusing 401."""
        monkeypatch.delenv("MISSING_DOCTOR_KEY", raising=False)
        (tmp_path / "sponsio.yaml").write_text(
            "version: 1\nextractor:\n  provider: openai\n  api_key: ${MISSING_DOCTOR_KEY}\n"
        )
        r = check_sponsio_yaml(tmp_path)
        assert r.status == "warn"
        assert "MISSING_DOCTOR_KEY" in r.detail

    def test_env_var_with_default_is_not_warned(self, tmp_path, monkeypatch):
        """``${VAR:-default}`` is the user's explicit "ok if unset"
        signal; doctor must respect that and stay quiet."""
        monkeypatch.delenv("MAYBE_DOCTOR_KEY", raising=False)
        (tmp_path / "sponsio.yaml").write_text(
            "version: 1\nextractor:\n  provider: openai\n  api_key: ${MAYBE_DOCTOR_KEY:-fallback-key}\n"
        )
        r = check_sponsio_yaml(tmp_path)
        assert r.status == "ok"

    def test_present_env_var_is_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRESENT_DOCTOR_KEY", "sk-doctor")
        (tmp_path / "sponsio.yaml").write_text(
            "version: 1\nextractor:\n  provider: openai\n  api_key: ${PRESENT_DOCTOR_KEY}\n"
        )
        r = check_sponsio_yaml(tmp_path)
        assert r.status == "ok"
        assert "extractor=openai" in r.detail

    def test_env_var_in_comment_is_ignored(self, tmp_path, monkeypatch):
        """``sponsio onboard`` writes example commented-out hints like
        ``# api_key: ${GOOGLE_API_KEY}`` for users who later want to
        opt in.  Those references aren't actually consumed by the
        loader, so doctor must not flag them as missing."""
        monkeypatch.delenv("UNSET_HINT_VAR", raising=False)
        (tmp_path / "sponsio.yaml").write_text(
            "version: 1\n"
            "# extractor:\n"
            "#   provider: openai\n"
            "#   api_key: ${UNSET_HINT_VAR}\n"
        )
        r = check_sponsio_yaml(tmp_path)
        assert r.status == "ok", r.detail
        assert "UNSET_HINT_VAR" not in r.detail


# ---------------------------------------------------------------------------
# LLM ping (network — patched out so tests stay offline)
# ---------------------------------------------------------------------------


class TestCheckLlmPing:
    def test_success_reports_provider_and_latency(self, monkeypatch):
        """Happy path: build the extractor, round-trip, format the
        success line.  Real model isn't called — we patch
        ``UnifiedExtractor`` so the test stays offline + fast."""
        from sponsio.doctor import check_llm_ping  # noqa: F401  (re-import for clarity)

        class _FakeExt:
            _provider = "gemini"
            _model = "gemini-2.0-flash"

            def __init__(self, **_kwargs):
                pass

            def extract_from_nl(self, _nl):
                return []

        monkeypatch.setattr(
            "sponsio.generation.llm_extraction.UnifiedExtractor", _FakeExt
        )

        r = check_llm_ping()
        assert r.status == "ok"
        assert "gemini" in r.detail
        assert "ms" in r.detail

    def test_failure_surfaces_error_type(self, monkeypatch):
        """The whole point of ``--llm`` is "tell me the actual error
        before I try sponsio scan and waste 30s of cold start" — so
        the failure path must include the error type and message."""

        class _FakeExt:
            def __init__(self, **_kwargs):
                pass

            def extract_from_nl(self, _nl):
                raise PermissionError("invalid api key")

        monkeypatch.setattr(
            "sponsio.generation.llm_extraction.UnifiedExtractor", _FakeExt
        )

        r = check_llm_ping()
        assert r.status == "fail"
        assert "PermissionError" in r.detail
        assert "invalid api key" in r.detail

    def test_uses_extractor_section_credentials(self, monkeypatch):
        """A YAML-configured project must get its YAML credentials
        through to the extractor — otherwise ``--llm`` would silently
        ping the env-var-detected provider instead, giving a bogus
        green light."""
        from sponsio.config import ExtractorSection

        captured: dict = {}

        class _FakeExt:
            _provider = "anthropic"
            _model = "claude-3-5-sonnet-20241022"

            def __init__(self, **kwargs):
                captured.update(kwargs)

            def extract_from_nl(self, _nl):
                return []

        monkeypatch.setattr(
            "sponsio.generation.llm_extraction.UnifiedExtractor", _FakeExt
        )

        section = ExtractorSection(
            provider="anthropic",
            model="claude-3-5-sonnet-20241022",
            api_key="sk-ant-from-yaml",
            base_url=None,
        )
        r = check_llm_ping(section)
        assert r.status == "ok"
        assert captured["provider"] == "anthropic"
        assert captured["api_key"] == "sk-ant-from-yaml"


# ---------------------------------------------------------------------------
# --llm flag wiring
# ---------------------------------------------------------------------------


class TestRunDoctorWithLlm:
    def test_llm_check_only_runs_when_opted_in(self, tmp_path, monkeypatch):
        """Default doctor is offline — the ping check must NOT
        appear when ``with_llm=False`` (we go to lengths to keep
        ``sponsio doctor`` runnable on a plane)."""
        monkeypatch.setenv("SPONSIO_MODE", "observe")
        results, _ = run_doctor(tmp_path, with_llm=False)
        assert all(r.name != "LLM ping" for r in results)

    def test_llm_check_uses_yaml_extractor_when_present(self, tmp_path, monkeypatch):
        """End-to-end: ``--llm`` against a project with a
        ``sponsio.yaml`` should pick up the YAML extractor section
        and pass its credentials to the patched extractor."""
        monkeypatch.setenv("SPONSIO_MODE", "observe")
        monkeypatch.setenv("DOCTOR_PROJ_KEY", "sk-from-env")
        (tmp_path / "sponsio.yaml").write_text(
            "version: 1\n"
            "extractor:\n"
            "  provider: openai\n"
            "  model: gpt-4o-mini\n"
            "  api_key: ${DOCTOR_PROJ_KEY}\n"
        )

        captured: dict = {}

        class _FakeExt:
            _provider = "openai"
            _model = "gpt-4o-mini"

            def __init__(self, **kwargs):
                captured.update(kwargs)

            def extract_from_nl(self, _nl):
                return []

        monkeypatch.setattr(
            "sponsio.generation.llm_extraction.UnifiedExtractor", _FakeExt
        )

        results, code = run_doctor(tmp_path, with_llm=True)
        ping = next((r for r in results if r.name == "LLM ping"), None)
        assert ping is not None
        assert ping.status == "ok", ping.detail
        assert captured["provider"] == "openai"
        assert captured["api_key"] == "sk-from-env"
        # No ping failure → no exit-code regression
        assert code == 0


# ---------------------------------------------------------------------------
# JSON output (machine-consumable)
# ---------------------------------------------------------------------------


class TestReportToDict:
    def test_round_trip_shape(self, tmp_path, monkeypatch):
        """Lock the schema: callers depend on these top-level keys
        being there even when there are zero failures.  Schema
        version is included so future breaking changes are explicit."""
        monkeypatch.setenv("SPONSIO_MODE", "observe")
        results, code = run_doctor(tmp_path)
        payload = report_to_dict(results, code)

        assert set(payload.keys()) >= {
            "schema_version",
            "exit_code",
            "summary",
            "next_step",
            "checks",
        }
        assert payload["schema_version"] == 1
        assert payload["exit_code"] == code
        assert set(payload["summary"].keys()) == {"ok", "warn", "fail", "skip"}
        # Counts must reconcile with the per-check details
        assert sum(payload["summary"].values()) == len(payload["checks"])

    def test_each_check_has_required_fields(self, tmp_path, monkeypatch):
        """IDE plugins iterate ``checks`` and depend on shape; one
        missing field is a silent rendering bug."""
        monkeypatch.setenv("SPONSIO_MODE", "observe")
        results, code = run_doctor(tmp_path)
        payload = report_to_dict(results, code)

        for c in payload["checks"]:
            assert {"name", "status", "detail"}.issubset(c.keys())
            assert c["status"] in {"ok", "warn", "fail", "skip"}
            assert isinstance(c["name"], str) and c["name"]

    def test_failure_reflected_in_summary_and_exit(self, tmp_path, monkeypatch):
        """Invalid SPONSIO_MODE → ``Runtime mode`` fails → summary
        count and top-level exit_code must agree."""
        monkeypatch.setenv("SPONSIO_MODE", "yolo")
        results, code = run_doctor(tmp_path)
        payload = report_to_dict(results, code)

        assert code == 1
        assert payload["exit_code"] == 1
        assert payload["summary"]["fail"] >= 1


class TestCliJsonFlag:
    def test_json_flag_emits_only_parseable_json(self, tmp_path, monkeypatch):
        """JSON consumers must be able to ``json.loads(stdout)``
        without stripping any banner — that's the whole point.
        Anything before/after the document would break ``jq`` and
        every wrapper."""
        import json as _json

        from sponsio.cli import doctor

        monkeypatch.setenv("SPONSIO_MODE", "observe")
        runner = CliRunner()
        result = runner.invoke(doctor, [str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        # The whole stdout must be one JSON document — anything else
        # means our human-readable banner leaked through.
        parsed = _json.loads(result.output)
        assert parsed["schema_version"] == 1
        assert "checks" in parsed

    def test_json_flag_reflects_exit_code_in_payload(self, tmp_path, monkeypatch):
        """Useful for dashboards that want to display health WITHOUT
        consuming process exit (e.g. when invoked via subprocess
        with check=False).  Payload must agree with the exit."""
        import json as _json

        from sponsio.cli import doctor

        monkeypatch.setenv("SPONSIO_MODE", "yolo")  # invalid → fail
        runner = CliRunner()
        result = runner.invoke(doctor, [str(tmp_path), "--json"])
        assert result.exit_code == 1
        parsed = _json.loads(result.output)
        assert parsed["exit_code"] == 1
        assert parsed["summary"]["fail"] >= 1


@pytest.fixture(autouse=False)
def _fixture_dummy():
    pass


# Need CliRunner imported — added at top of CLI test classes
from click.testing import CliRunner  # noqa: E402


# ---------------------------------------------------------------------------
# Agent Skill check — probes ~/.cursor/skills etc. for the installed
# skill and surfaces drift / breakage as warn / fail.
# ---------------------------------------------------------------------------


class TestCheckSkillInstalled:
    """These tests monkeypatch ``_SKILL_TOOL_DIRS`` so we never touch
    the developer's real ``~/.cursor`` / ``~/.claude`` on CI."""

    def _set_fake_dirs(self, monkeypatch, tmp_path, *, with_cursor=True):
        from sponsio import cli as cli_mod

        dirs = {
            "cursor": tmp_path / "fake_cursor" if with_cursor else tmp_path / "no_c",
            "claude": tmp_path / "fake_claude",
            "codex": tmp_path / "fake_codex",
        }
        for name, path in dirs.items():
            monkeypatch.setitem(cli_mod._SKILL_TOOL_DIRS, name, path)
        return dirs

    def _install_into(self, dest):
        from sponsio.cli import cli as root_cli

        # --force because the dest dir may pre-exist in fixtures.
        result = CliRunner().invoke(
            root_cli, ["skill", "install", "--dest", str(dest), "--force"]
        )
        assert result.exit_code == 0, result.output

    def test_skip_when_nothing_installed_anywhere(self, tmp_path, monkeypatch):
        """No skills dir on disk → ``skip`` with a hint about ``sponsio
        skill install``.  Not a failure (feature is opt-in)."""
        from sponsio.doctor import check_skill_installed

        self._set_fake_dirs(monkeypatch, tmp_path)
        r = check_skill_installed()
        assert r.status == "skip"
        assert "sponsio skill install" in r.detail

    def test_ok_when_copy_install_is_in_sync(self, tmp_path, monkeypatch):
        """Fresh copy install → ``ok`` and call out ``copy`` mode so
        users know they'll need to re-install after ``pip install -U``."""
        from sponsio.doctor import check_skill_installed

        dirs = self._set_fake_dirs(monkeypatch, tmp_path)
        self._install_into(dirs["cursor"])

        r = check_skill_installed()
        assert r.status == "ok", r.detail
        assert "cursor" in r.detail
        assert "copy" in r.detail

    def test_warn_when_installed_copy_drifts_from_packaged(self, tmp_path, monkeypatch):
        """If the installed SKILL.md no longer matches the packaged
        one (classic ``pip install -U`` footgun), the check must
        warn — loudly enough that users know to re-install."""
        from sponsio.doctor import check_skill_installed

        dirs = self._set_fake_dirs(monkeypatch, tmp_path)
        self._install_into(dirs["cursor"])

        # Mutate the installed SKILL.md to simulate an outdated copy.
        skill_md = dirs["cursor"] / "sponsio" / "SKILL.md"
        skill_md.write_text(skill_md.read_text() + "\n# stale marker\n")

        r = check_skill_installed()
        assert r.status == "warn", r.detail
        assert "stale" in r.detail.lower()
        assert "sponsio skill install" in r.detail

    def test_fail_when_frontmatter_broken(self, tmp_path, monkeypatch):
        """Broken SKILL.md (no frontmatter) → ``fail``.  Users think
        their agent has Sponsio's skill but the dispatcher rejects
        frontmatter-less files."""
        from sponsio.doctor import check_skill_installed

        dirs = self._set_fake_dirs(monkeypatch, tmp_path)
        self._install_into(dirs["cursor"])

        skill_md = dirs["cursor"] / "sponsio" / "SKILL.md"
        skill_md.write_text("no frontmatter here — just prose body")

        r = check_skill_installed()
        assert r.status == "fail", r.detail
        assert "broken" in r.detail.lower()
        assert "--force" in r.detail

    def test_multiple_healthy_tools_are_listed_together(self, tmp_path, monkeypatch):
        from sponsio.doctor import check_skill_installed

        dirs = self._set_fake_dirs(monkeypatch, tmp_path)
        self._install_into(dirs["cursor"])
        self._install_into(dirs["claude"])

        r = check_skill_installed()
        assert r.status == "ok", r.detail
        assert "cursor" in r.detail
        assert "claude" in r.detail

    def test_doctor_integration_includes_skill_check(self, tmp_path, monkeypatch):
        """End-to-end: the report from ``run_doctor`` must include an
        ``Agent Skill`` entry; otherwise silently losing this check
        wouldn't fail any test."""

        self._set_fake_dirs(monkeypatch, tmp_path)
        results, _code = run_doctor(tmp_path)
        names = [r.name for r in results]
        assert "Agent Skill" in names, names

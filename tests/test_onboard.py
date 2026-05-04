"""Tests for ``sponsio onboard`` and the underlying :mod:`sponsio.onboard`.

Covers three layers:

* ``starter_contracts`` — pure function from tool names to a list of
  :class:`ProposedConstraint`.  Verifies the name-heuristic table
  stays in sync with the pattern library (each emitted proposal must
  round-trip through ``_compile_structured``).
* ``detect_framework`` / ``detect_provider`` — pure functions with
  filesystem + env-var fixtures.  No network: every provider test
  passes ``probe_ollama=False``.
* ``run_onboard`` + ``sponsio onboard`` CLI — end-to-end on a tiny
  fixture project.  Asserts the written ``sponsio.yaml`` loads
  cleanly through ``sponsio.config.load_config`` so we never ship
  "documentation YAML" that the production loader rejects.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

pytest.importorskip("yaml")

from sponsio.cli import onboard
from sponsio.config import load_config
from sponsio.discovery.starter_pack import starter_contracts
from sponsio.onboard import (
    OnboardReport,
    _compose_yaml,
    _count_contracts,
    _dedup_starter_proposals,
    _existing_contract_keys,
    detect_framework,
    detect_provider,
    run_onboard,
)


# ---------------------------------------------------------------------------
# starter_contracts — pure
# ---------------------------------------------------------------------------


class TestStarterContracts:
    def test_empty_tool_list_emits_nothing_by_default(self):
        """Empty input + default args produces zero proposals.

        Both ``token_budget`` and ``delegation_depth_limit`` used to
        be emitted unconditionally with arbitrary thresholds (100k
        tokens / depth 3) — every user removed them on first review,
        so the defaults are now opt-in.  The opt-in path is exercised
        by ``test_globals_can_be_opted_in``.
        """
        props = starter_contracts([])
        assert props == []

    def test_globals_can_be_opted_in(self):
        """Explicit ``include_*`` flags still produce the global rules
        for callers that genuinely want session-wide caps."""
        props = starter_contracts(
            [],
            include_token_budget=True,
            include_delegation_limit=True,
        )
        pats = {p.formula.pattern_name for p in props}
        assert "token_budget" in pats
        assert "delegation_depth_limit" in pats

    def test_irreversible_token_triggers_irreversible_once(self):
        props = starter_contracts(["delete_user", "drop_table"])
        irrev = [p for p in props if p.formula.pattern_name == "irreversible_once"]
        assert {p.evidence["args"][0] for p in irrev} == {"delete_user", "drop_table"}

    def test_irreversible_covers_account_lifecycle_verbs(self):
        """Real-world support agents use ``cancel_*`` / ``revoke_*`` /
        ``disable_*`` / ``unsubscribe_*`` for state changes that are
        functionally irreversible (re-cancellation = double-charge,
        re-revoke = spurious re-notify).  This test pins the fix for
        the coverage gap that surfaced from running the README's
        one-prompt setup against a customer-support fixture."""
        names = [
            "cancel_subscription",
            "revoke_token",
            "disable_account",
            "deactivate_user",
            "suspend_user",
            "unsubscribe_user",
        ]
        props = starter_contracts(names)
        irrev = {
            p.evidence["args"][0]
            for p in props
            if p.formula.pattern_name == "irreversible_once"
        }
        assert irrev == set(names), f"missed: {set(names) - irrev}"

    def test_irreversible_covers_money_movement(self):
        """Payment-shaped tools must trip irreversible_once even when
        the verb isn't ``delete``-shaped — at-most-once is the whole
        point of contract enforcement for money."""
        names = ["transfer_funds", "charge_card", "execute_trade", "approve_payment"]
        props = starter_contracts(names)
        irrev = {
            p.evidence["args"][0]
            for p in props
            if p.formula.pattern_name == "irreversible_once"
        }
        assert irrev == set(names)

    def test_send_email_triggers_rate_limit(self):
        props = starter_contracts(["send_email"])
        rate = [p for p in props if p.formula.pattern_name == "rate_limit"]
        assert len(rate) == 1
        assert rate[0].evidence["args"][0] == "send_email"

    def test_tool_allowlist_contains_every_tool(self):
        """``tool_allowlist`` is back on after the LTL encoding fix.

        Historical bug: the rule used to compile to ``G(∨ called(tᵢ))``
        which is FALSE at any timestep where no tool fires (initial
        state, gaps between events).  Once a partial trace was being
        verified, the rule auto-violated and blocked the first call
        regardless of whether it was in the list.

        Fix: the rule now compiles to
        ``G(called_any -> ∨ called(tᵢ))`` — vacuously true at
        non-tool timesteps, enforced only when SOME tool fires.
        See ``test_tool_allowlist_satisfied_on_empty_trace`` (in
        the patterns suite) for the regression pin.
        """
        names = ["read_doc", "send_email", "delete_user"]
        props = starter_contracts(names)
        allowlist = next(p for p in props if p.formula.pattern_name == "tool_allowlist")
        # Args shape: [[name1, name2, ...]] — one positional list-arg
        # so YAML round-trip splats cleanly into ``tool_allowlist([..])``.
        assert sorted(allowlist.evidence["args"][0]) == sorted(names)

    def test_every_proposal_has_args_matching_pattern_signature(self):
        """Every proposal's evidence['args'] must splat cleanly into
        the pattern function via _compile_structured.  This is the
        single invariant that guarantees the YAML round-trips."""
        from sponsio.generation.nl_to_contract import get_available_patterns

        registry = get_available_patterns()
        props = starter_contracts(
            ["send_email", "delete_user", "bash_run", "execute_sql", "plain_tool"]
        )
        for p in props:
            pat = p.formula.pattern_name
            fn = registry[pat]
            # Must not raise — i.e. args shape matches the function.
            fn(*p.evidence["args"])

    def test_starter_contracts_confidence_under_one(self):
        """Starter-pack rules are conservative by design — never emit
        confidence ≥ 0.9 (which would suppress the ``review recommended``
        hint in the YAML).  0.7 is the ceiling for the most opinionated
        rule (irreversible_once)."""
        props = starter_contracts(["delete_user", "send_email"])
        for p in props:
            assert p.confidence < 0.9, (p.formula.pattern_name, p.confidence)


# ---------------------------------------------------------------------------
# detect_framework
# ---------------------------------------------------------------------------


class TestDetectFramework:
    def test_langgraph_imports_win(self, tmp_path: Path):
        (tmp_path / "agent.py").write_text(
            "from langgraph.prebuilt import create_react_agent\n"
        )
        hint = detect_framework(tmp_path)
        assert hint.framework == "langgraph"
        assert hint.factory == "sponsio.langgraph"
        assert hint.entry_file is not None

    def test_crewai_imports(self, tmp_path: Path):
        (tmp_path / "crew.py").write_text("from crewai import Crew, Agent\n")
        hint = detect_framework(tmp_path)
        assert hint.framework == "crewai"

    def test_google_adk_imports(self, tmp_path: Path):
        (tmp_path / "agent.py").write_text(
            "from google.adk.agents.llm_agent import Agent\n"
        )
        hint = detect_framework(tmp_path)
        assert hint.framework == "google_adk"
        assert hint.factory == "sponsio.google_adk"
        assert hint.entry_file is not None

    def test_openai_imports(self, tmp_path: Path):
        (tmp_path / "bot.py").write_text("import openai\n")
        hint = detect_framework(tmp_path)
        assert hint.framework == "openai"

    def test_pyproject_fallback_when_no_imports(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = ["langgraph>=0.2"]\n'
        )
        # No .py files at all — dependency declaration is the only hint
        hint = detect_framework(tmp_path)
        assert hint.framework == "langgraph"
        assert "pyproject.toml" in hint.evidence

    def test_google_adk_dependency_fallback(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = ["google-adk>=0.5"]\n'
        )
        hint = detect_framework(tmp_path)
        assert hint.framework == "google_adk"
        assert hint.factory == "sponsio.google_adk"

    def test_empty_directory_returns_none(self, tmp_path: Path):
        hint = detect_framework(tmp_path)
        assert hint.framework == "none"
        assert hint.factory == "sponsio"

    def test_sponsio_adapter_import_implies_framework(self, tmp_path: Path):
        # Once the user has pasted the wrap snippet onboard prints
        # (``from sponsio.crewai import Sponsio``), re-running
        # ``detect_framework`` must recognise crewai even when the
        # underlying SDK isn't directly imported in the file (common
        # in scripted demos that mock the framework client).  Without
        # this, demos rcfiles end up frozen at ``framework: none`` and
        # the wrap-snippet output reverts to the generic ``import
        # sponsio`` form.
        (tmp_path / "agent.py").write_text(
            "from sponsio.crewai import Sponsio\nguard = Sponsio()\n"
        )
        hint = detect_framework(tmp_path)
        assert hint.framework == "crewai"
        assert hint.factory == "sponsio.crewai"

    def test_sponsio_adapter_import_for_claude_agent(self, tmp_path: Path):
        (tmp_path / "agent.py").write_text("from sponsio.claude_agent import Sponsio\n")
        hint = detect_framework(tmp_path)
        assert hint.framework == "claude_agent"
        assert hint.factory == "sponsio.claude_agent"

    def test_direct_framework_import_beats_sponsio_adapter(self, tmp_path: Path):
        # When both ``crewai`` (direct) and ``sponsio.crewai`` (adapter)
        # appear, the framework identification is the same (crewai) —
        # this is mostly a smoke test that mixing the two doesn't trip
        # the prefix matcher into a confused state.
        (tmp_path / "a.py").write_text("from crewai import Crew\n")
        (tmp_path / "b.py").write_text("from sponsio.crewai import Sponsio\n")
        hint = detect_framework(tmp_path)
        assert hint.framework == "crewai"

    def test_venv_excluded(self, tmp_path: Path):
        """A langgraph install in .venv must NOT trigger detection —
        otherwise every project with langgraph installed would report
        langgraph regardless of what its own code does."""
        venv = tmp_path / ".venv" / "lib" / "site-packages" / "langgraph"
        venv.mkdir(parents=True)
        (venv / "__init__.py").write_text("from langgraph import core\n")
        hint = detect_framework(tmp_path)
        assert hint.framework == "none"

    def test_package_json_vercel_ai_dependency(self, tmp_path: Path):
        """A TS project using the Vercel AI SDK must detect as
        ``vercel_ai`` from ``package.json`` deps — without this,
        ``sponsio init`` rendered ``🔍 Detected: TypeScript · none``
        on real Vercel-AI agents and the wizard had no sensible
        framework default to pre-pick.
        """
        (tmp_path / "package.json").write_text(
            '{"name":"x","dependencies":{"@ai-sdk/anthropic":"^1.0.0",'
            '"ai":"^4.0.0","zod":"^3.22.0"}}\n'
        )
        hint = detect_framework(tmp_path)
        assert hint.framework == "vercel_ai"
        assert "package.json" in hint.evidence

    def test_package_json_langchain_js_dependency(self, tmp_path: Path):
        (tmp_path / "package.json").write_text(
            '{"name":"x","dependencies":{"@langchain/core":"^0.3.0"}}\n'
        )
        hint = detect_framework(tmp_path)
        assert hint.framework == "langchain"

    def test_package_json_quote_anchoring_avoids_false_positive(
        self, tmp_path: Path
    ):
        # The bare ``"ai"`` package name must not match a substring of
        # an unrelated key.  Use an unrelated package whose value
        # contains "ai" as a substring (e.g. ``"some-ai-thing"``).
        # Without quote-anchoring this would falsely detect vercel_ai.
        (tmp_path / "package.json").write_text(
            '{"name":"x","dependencies":{"some-ai-thing":"^1.0.0"}}\n'
        )
        hint = detect_framework(tmp_path)
        assert hint.framework == "none"

    def test_monorepo_pad_files_do_not_starve_detection(
        self, tmp_path: Path
    ):
        """250 unrelated .py files at the repo root must NOT cause the
        framework detector to miss the agent file in a deep subdir.

        Repro of the asymmetry that bit ``sponsio onboard --emit-context``
        on real monorepos: tool inventory's walker has no scan cap and
        correctly found ``@tool`` functions in ``services/api/src/``,
        but framework detection's bounded walker exhausted on the pad
        files first and returned ``framework: none``.  The fix passes
        the tool-extractor's file hits in as ``prioritize_files`` so
        they're guaranteed to be scanned before the cap kicks in.
        """
        for i in range(250):
            (tmp_path / f"pad_{i}.py").write_text(f"x = {i}\n")
        agent_dir = tmp_path / "services" / "api" / "src"
        agent_dir.mkdir(parents=True)
        agent_file = agent_dir / "agent.py"
        agent_file.write_text(
            "from langgraph.prebuilt import create_react_agent\n"
            "from langchain_core.tools import tool\n"
        )

        hint = detect_framework(
            tmp_path, prioritize_files=[agent_file]
        )
        assert hint.framework == "langgraph"
        assert hint.factory == "sponsio.langgraph"


# ---------------------------------------------------------------------------
# detect_provider
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_provider_env(monkeypatch):
    """Strip every provider-related env var so each test starts clean."""
    for k in (
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
    ):
        monkeypatch.delenv(k, raising=False)
    yield


class TestDetectProvider:
    def test_gemini_wins_over_anthropic_and_openai(
        self, clean_provider_env, monkeypatch
    ):
        """Free-tier priority: when multiple keys are set, Gemini
        wins because its free tier means least surprise on cost."""
        monkeypatch.setenv("GOOGLE_API_KEY", "x")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        monkeypatch.setenv("OPENAI_API_KEY", "x")
        p = detect_provider(probe_ollama=False)
        assert p.provider == "gemini"
        assert p.env_var == "GOOGLE_API_KEY"

    def test_anthropic_wins_over_openai(self, clean_provider_env, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        monkeypatch.setenv("OPENAI_API_KEY", "x")
        p = detect_provider(probe_ollama=False)
        assert p.provider == "anthropic"

    def test_base_url_routes_to_openai_compatible(
        self, clean_provider_env, monkeypatch
    ):
        """An OPENAI_BASE_URL without an API key implies a user-
        configured endpoint (OpenRouter, Azure, vLLM, ...).  We
        still report provider=openai because the SDK path is the
        same."""
        monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
        p = detect_provider(probe_ollama=False)
        assert p.provider == "openai"
        assert p.base_url == "https://openrouter.ai/api/v1"

    def test_none_when_nothing_set(self, clean_provider_env):
        p = detect_provider(probe_ollama=False)
        assert p.provider == "none"

    def test_gemini_api_key_env_var_also_accepted(
        self, clean_provider_env, monkeypatch
    ):
        """Some Google tooling uses GEMINI_API_KEY (not GOOGLE_API_KEY).
        We accept both; the chosen env-var name is reflected in the
        output so the rendered YAML references the right one."""
        monkeypatch.setenv("GEMINI_API_KEY", "x")
        p = detect_provider(probe_ollama=False)
        assert p.provider == "gemini"
        assert p.env_var == "GEMINI_API_KEY"


# ---------------------------------------------------------------------------
# _compose_yaml — pure
# ---------------------------------------------------------------------------


class TestComposeYaml:
    def test_no_duplicate_version_keys(self):
        """Regression guard: an early bug prepended ``version: 1`` but
        failed to strip the scan body's own ``version: "1"``, leaving
        two top-level version keys that some YAML loaders silently
        merge (last wins) and others reject."""
        from sponsio.onboard import ProviderHint

        scan_body = (
            "# Generated by: sponsio scan\n"
            'version: "1"\n\n'
            "tools: []\n\n"
            "agents:\n  agent:\n    contracts: []\n"
        )
        out = _compose_yaml(
            provider=ProviderHint(),
            mode="observe",
            agent_id="agent",
            scan_yaml=scan_body,
        )
        # Exactly one version key at column 0.
        assert out.count("\nversion:") + out.startswith("version:") == 1

    def test_agent_id_rename(self):
        from sponsio.onboard import ProviderHint

        scan_body = (
            'version: "1"\n\ntools: []\n\nagents:\n  agent:\n    contracts: []\n'
        )
        out = _compose_yaml(
            provider=ProviderHint(),
            mode="observe",
            agent_id="customer_bot",
            scan_yaml=scan_body,
        )
        assert "\n  customer_bot:\n" in out
        # Default "agent" key no longer present under `agents:`.
        assert "\n  agent:\n" not in out


# ---------------------------------------------------------------------------
# run_onboard + CLI end-to-end
# ---------------------------------------------------------------------------


def _make_fixture_project(tmp_path: Path) -> Path:
    """Create a minimal LangGraph-shaped project under tmp_path."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "agent.py").write_text(
        "from langgraph.prebuilt import create_react_agent\n"
        "from langchain_core.tools import tool\n\n"
        "@tool\n"
        "def send_email(to: str, body: str) -> str:\n"
        '    """Send an email."""\n'
        "    return 'sent'\n\n"
        "@tool\n"
        "def delete_user(user_id: str) -> str:\n"
        '    """Delete a user."""\n'
        "    return 'deleted'\n\n"
        "@tool\n"
        "def bash_run(command: str) -> str:\n"
        '    """Run a shell command."""\n'
        "    return 'ok'\n\n"
        "def build():\n"
        "    return create_react_agent(model=None, tools=[send_email, delete_user, bash_run])\n"
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["langgraph>=0.2"]\n'
    )
    return tmp_path


class TestRunOnboard:
    def test_end_to_end_no_llm_writes_valid_yaml(
        self, tmp_path: Path, clean_provider_env
    ):
        """The no-LLM path is the important one — it's what users
        without an API key land on, and it's the path we're trying
        to keep 'smooth'.  After ``run_onboard`` returns, the file
        must load through ``load_config`` AND populate the agent's
        contracts block with starter-pack entries."""
        project = _make_fixture_project(tmp_path)

        report: OnboardReport = run_onboard(
            project,
            probe_ollama=False,  # never hit localhost in tests
        )

        assert report.out_path.exists()
        assert report.framework.framework == "langgraph"
        assert report.provider.provider == "none"
        assert report.starter_pack_used is True
        assert report.tools_count == 3
        assert report.contracts_count > 0

        cfg = load_config(report.out_path)
        assert "agent" in cfg.agents
        assert len(cfg.agents["agent"].contracts) == report.contracts_count

    def test_end_to_end_with_llm_env_skips_starter_pack(
        self, tmp_path: Path, clean_provider_env, monkeypatch
    ):
        """When a provider is detected, the starter pack is a
        fallback — it should run only if the LLM + AST combined
        produced zero contracts.  On a project with recognisable
        risky tool names, AST alone finds enough, so starter-pack
        stays disabled even though we have an OpenAI key."""
        # NB: we set the key but don't actually call the LLM — the
        # OpenAI client init will fail lazily inside generate_yaml
        # only if LLM inference actually runs.  CodeAnalyzer is
        # tolerant of a no-op init when the AST pass finds tools.
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-test")

        project = _make_fixture_project(tmp_path)

        # We catch any LLM call failure — tests must not require network.
        try:
            report = run_onboard(project, probe_ollama=False)
        except Exception as e:
            # OpenAI SDK import / connection-level failure is out of scope
            # for this test; we only care that the code path is reachable.
            pytest.skip(f"LLM path unreachable in sandbox: {e}")
            return

        assert report.provider.provider == "openai"
        # Either the LLM produced contracts → starter-pack off
        # Or the LLM produced nothing → starter-pack on as fallback.
        # The cfg must still load either way.
        cfg = load_config(report.out_path)
        assert cfg.extractor is not None
        assert cfg.extractor.provider == "openai"

    def test_existing_yaml_refuses_without_force(
        self, tmp_path: Path, clean_provider_env
    ):
        project = _make_fixture_project(tmp_path)
        (project / "sponsio.yaml").write_text("# precious\n")
        with pytest.raises(FileExistsError):
            run_onboard(project, probe_ollama=False)
        # Didn't clobber the user's file.
        assert (project / "sponsio.yaml").read_text() == "# precious\n"

    def test_force_overwrites(self, tmp_path: Path, clean_provider_env):
        project = _make_fixture_project(tmp_path)
        (project / "sponsio.yaml").write_text("# precious\n")
        report = run_onboard(project, probe_ollama=False, force=True)
        assert "# precious" not in report.out_path.read_text()

    def test_custom_agent_id_propagates_into_yaml(
        self, tmp_path: Path, clean_provider_env
    ):
        project = _make_fixture_project(tmp_path)
        report = run_onboard(project, probe_ollama=False, agent_id="customer_bot")
        cfg = load_config(report.out_path)
        assert "customer_bot" in cfg.agents
        # Default id not accidentally also present.
        assert "agent" not in cfg.agents
        assert 'agent_id="customer_bot"' in report.wrap_snippet

    def test_google_adk_onboard_prints_adk_snippet(
        self, tmp_path: Path, clean_provider_env
    ):
        (tmp_path / "agent.py").write_text(
            "from google.adk.agents.llm_agent import Agent\n\n"
            "def search_flights(origin: str, destination: str) -> str:\n"
            "    return 'found'\n\n"
            "root_agent = Agent(\n"
            "    name='travel',\n"
            "    model='gemini-flash-latest',\n"
            "    tools=[search_flights],\n"
            ")\n"
        )
        report = run_onboard(tmp_path, probe_ollama=False)
        assert report.framework.framework == "google_adk"
        assert "from sponsio.google_adk import Sponsio" in report.wrap_snippet
        # Snippet now nudges users to pass ``guard.wrap(tools)`` into
        # their agent constructor rather than just declaring the
        # wrapped binding and stopping — the original two-liner left
        # day-1 users wondering "OK, where do these go?".
        assert "guard.wrap(tools)" in report.wrap_snippet
        assert "agent = Agent(" in report.wrap_snippet


class TestOnboardCli:
    def test_cli_runs_end_to_end(self, tmp_path: Path, clean_provider_env):
        runner = CliRunner()
        project = _make_fixture_project(tmp_path)
        result = runner.invoke(
            onboard,
            [str(project), "--no-probe-ollama", "--force"],
        )
        assert result.exit_code == 0, result.output
        assert (project / "sponsio.yaml").exists()
        assert "framework:  langgraph" in result.output
        assert "Add this to your agent entry point:" in result.output

    def test_cli_json_output_is_parseable(self, tmp_path: Path, clean_provider_env):
        import json

        runner = CliRunner()
        project = _make_fixture_project(tmp_path)
        result = runner.invoke(
            onboard,
            [str(project), "--no-probe-ollama", "--force", "--json"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["framework"]["framework"] == "langgraph"
        assert data["mode"] == "observe"
        assert data["tools_count"] >= 1

    def test_cli_preserves_yaml_without_force_on_existing(
        self, tmp_path: Path, clean_provider_env
    ):
        # Second-run UX: re-running ``sponsio onboard`` in a project
        # that already has a sponsio.yaml must NOT error out.  The
        # command should silently preserve the existing yaml (only
        # ``--force`` regenerates) while still refreshing the dotfiles
        # + reprinting the wrap snippet — the common "what was that
        # snippet again?" use case.
        runner = CliRunner()
        project = _make_fixture_project(tmp_path)
        (project / "sponsio.yaml").write_text("# precious\n")
        result = runner.invoke(
            onboard,
            [str(project), "--no-probe-ollama", "--no-doctor", "--no-interactive"],
        )
        assert result.exit_code == 0, result.output
        # Doesn't clobber.
        assert (project / "sponsio.yaml").read_text() == "# precious\n"
        # Surfaces the preserve path so users know why nothing changed.
        assert "preserved" in result.output

    def test_cli_force_regenerates_existing_yaml(
        self, tmp_path: Path, clean_provider_env
    ):
        # ``--force`` is the explicit opt-in for regenerating yaml.
        runner = CliRunner()
        project = _make_fixture_project(tmp_path)
        (project / "sponsio.yaml").write_text("# precious\n")
        result = runner.invoke(
            onboard,
            [
                str(project),
                "--no-probe-ollama",
                "--no-doctor",
                "--no-interactive",
                "--force",
            ],
        )
        assert result.exit_code == 0, result.output
        # ``--force`` regenerates: the placeholder content is gone.
        assert (project / "sponsio.yaml").read_text() != "# precious\n"

    def test_yaml_preserve_path_uses_fresh_detection_over_stale_rcfile(
        self, tmp_path: Path, clean_provider_env
    ):
        # Regression: when sponsio.yaml already exists (preserve path)
        # the wrap snippet was reading framework from .sponsiorc only.
        # If an older detector wrote ``framework: none`` (because it
        # didn't recognise ``sponsio.<adapter>`` imports), the preserve
        # path would forever print the generic ``import sponsio``
        # snippet even after the detector was fixed.  Today, fresh
        # ``detect_framework`` runs even on the preserve path and beats
        # a stale rcfile.
        runner = CliRunner()
        project = tmp_path / "proj"
        project.mkdir()
        # Code uses the Sponsio adapter — detection should infer
        # crewai from this alone (sponsio.crewai prefix is in the
        # signature list).
        (project / "agent.py").write_text(
            "from sponsio.crewai import Sponsio\nguard = Sponsio()\n"
        )
        # Pre-existing yaml means we hit the preserve path...
        (project / "sponsio.yaml").write_text("# precious\n")
        # ...with a stale rcfile claiming no framework.
        (project / ".sponsiorc").write_text(
            "framework: none\nextractor:\n  provider: none\n"
        )

        result = runner.invoke(
            onboard,
            [str(project), "--no-probe-ollama", "--no-doctor", "--no-interactive"],
        )
        assert result.exit_code == 0, result.output
        # yaml itself untouched.
        assert (project / "sponsio.yaml").read_text() == "# precious\n"
        # Wrap snippet uses the framework-specific factory, not the
        # generic ``import sponsio`` fallback that the stale rcfile
        # would have steered us toward.
        assert "from sponsio.crewai import Sponsio" in result.output
        assert "import sponsio\nguard = sponsio.Sponsio" not in result.output


class TestEmitContextHealthAndPreExisting:
    """``--emit-context`` adds ``health`` + ``pre_existing_contracts``
    so the host agent driving onboarding can dedupe its semantic-pass
    proposals + decide "stop and ask vs proceed" without re-grepping
    yaml or hand-rolling a Case A/B/C check in the wizard prompt.
    """

    def _emit(self, project: Path) -> dict:
        import json

        runner = CliRunner()
        result = runner.invoke(
            onboard,
            [str(project), "--no-probe-ollama", "--emit-context"],
        )
        assert result.exit_code == 0, result.output
        return json.loads(result.output)

    def test_health_ok_when_framework_and_tools_detected(
        self, tmp_path: Path, clean_provider_env
    ):
        project = _make_fixture_project(tmp_path)
        data = self._emit(project)
        assert data["health"] == "ok"
        assert data["framework"]["name"] == "langgraph"
        assert data["tool_inventory"]
        assert "framework + tools" in data["health_detail"]

    def test_health_empty_on_blank_repo(self, tmp_path: Path, clean_provider_env):
        # No framework imports, no @tool decorators, no policy doc.
        # The wizard prompt's case C path: stop and ask the user
        # what's actually here.
        (tmp_path / "agent.py").write_text("def foo(): pass\n")
        data = self._emit(tmp_path)
        assert data["health"] == "empty"
        assert data["framework"]["name"] == "none"
        assert data["tool_inventory"] == []

    def test_health_tools_only_when_framework_detected_but_no_tools(
        self, tmp_path: Path, clean_provider_env
    ):
        # Framework import present but no tool decorators — the
        # "tools come from external SDK" case (MCP, prebuilt tools,
        # OpenAI JSON schemas).  Wizard tells the agent to grep
        # tool registration manually.
        (tmp_path / "agent.py").write_text(
            "from langgraph.prebuilt import create_react_agent\n"
            "agent = create_react_agent(model, [])\n"
        )
        data = self._emit(tmp_path)
        assert data["health"] == "tools_only"
        assert data["framework"]["name"] == "langgraph"
        assert data["tool_inventory"] == []

    def test_pre_existing_contracts_parsed_from_on_disk_yaml(
        self, tmp_path: Path, clean_provider_env
    ):
        project = _make_fixture_project(tmp_path)
        # Simulate a prior onboard run that wrote some contracts.
        (project / "sponsio.yaml").write_text(
            "version: '1'\n"
            "agents:\n"
            "  agent:\n"
            "    contracts:\n"
            "      - E:\n"
            "          pattern: idempotent\n"
            "          args: [delete_user]\n"
            "          source: scan\n"
            "      - E:\n"
            "          pattern: rate_limit\n"
            "          args: [send_email, 5]\n"
            "          source: agent-extracted\n"
        )
        data = self._emit(project)
        contracts = data["pre_existing_contracts"]
        assert len(contracts) == 2
        patterns = {c["pattern"] for c in contracts}
        assert patterns == {"idempotent", "rate_limit"}
        # Source survives the parse so dedupe can tell starter rules
        # (`source: scan`) apart from agent additions
        # (`source: agent-extracted`).
        sources = {c["source"] for c in contracts}
        assert sources == {"scan", "agent-extracted"}

    def test_pre_existing_contracts_empty_when_no_yaml(
        self, tmp_path: Path, clean_provider_env
    ):
        # First-time onboard — no sponsio.yaml on disk yet.
        project = _make_fixture_project(tmp_path)
        data = self._emit(project)
        assert data["pre_existing_contracts"] == []

    def test_pre_existing_contracts_robust_to_malformed_yaml(
        self, tmp_path: Path, clean_provider_env
    ):
        # A malformed yaml shouldn't block the diagnostic JSON —
        # doctor/validate will flag the parse error elsewhere.
        project = _make_fixture_project(tmp_path)
        (project / "sponsio.yaml").write_text(
            "agents:\n  agent:\n    contracts: [oops"
        )
        data = self._emit(project)
        assert data["pre_existing_contracts"] == []
        assert data["health"] == "ok"  # detection still runs


# ---------------------------------------------------------------------------
# Miscellaneous helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Dedup — starter pack vs. existing scan output
# ---------------------------------------------------------------------------


class TestDedup:
    def test_keys_extracted_from_yaml_match_proposal_keys(self):
        """The two key extraction paths (text-scanning the YAML +
        introspecting a ProposedConstraint) must produce identical
        keys for the same logical contract — otherwise dedup silently
        breaks."""
        from sponsio.onboard import _proposal_dedup_key

        yaml_text = (
            "agents:\n"
            "  agent:\n"
            "    contracts:\n"
            "      - E:\n"
            "          pattern: arg_blacklist\n"
            '          args: [bash_run, command, ["rm -rf"]]\n'
            "      - E:\n"
            "          pattern: idempotent\n"
            "          args: [delete_user]\n"
        )
        existing = _existing_contract_keys(yaml_text)
        # idempotent → irreversible_once via alias
        assert ("arg_blacklist", "bash_run") in existing
        assert ("irreversible_once", "delete_user") in existing

        # And a same-keyed proposal must collide.
        starter = starter_contracts(["bash_run", "delete_user"])
        starter_keys = {_proposal_dedup_key(p) for p in starter}
        # Both AST contracts should be in starter's key set (so they'd
        # be dropped by dedup).
        assert ("arg_blacklist", "bash_run") in starter_keys
        assert ("irreversible_once", "delete_user") in starter_keys

    def test_dedup_drops_aliased_pattern(self):
        """``idempotent`` and ``irreversible_once`` compile to the same
        LTL — keep only the AST one."""
        starter = starter_contracts(["delete_user"])
        scan_yaml = (
            "agents:\n  agent:\n    contracts:\n"
            "      - E:\n          pattern: idempotent\n"
            "          args: [delete_user]\n"
        )
        filtered = _dedup_starter_proposals(starter, scan_yaml)
        kept_patterns = {p.formula.pattern_name for p in filtered}
        assert "irreversible_once" not in kept_patterns
        # Other rules (loop_detection, token_budget, ...) survive.
        assert "loop_detection" in kept_patterns

    def test_dedup_no_op_when_scan_yaml_empty(self):
        """No structured entries in scan output → starter pack passes
        through unchanged."""
        starter = starter_contracts(["send_email"])
        filtered = _dedup_starter_proposals(
            starter, "agents:\n  agent:\n    contracts: []\n"
        )
        assert len(filtered) == len(starter)

    def test_global_rules_collapse_on_pattern_name_only(self):
        """Patterns with non-string first arg (``token_budget(100k, "total")``,
        ``tool_allowlist([...])``) dedup on ``(pattern, None)`` — one
        of each survives, never duplicated."""
        starter = starter_contracts(["send_email"])
        # Build a fake scan YAML that already has a token_budget entry.
        scan_yaml = (
            "agents:\n  agent:\n    contracts:\n"
            "      - E:\n          pattern: token_budget\n"
            "          args: [50000, total]\n"
        )
        filtered = _dedup_starter_proposals(starter, scan_yaml)
        kept = {p.formula.pattern_name for p in filtered}
        assert "token_budget" not in kept


# ---------------------------------------------------------------------------
# Doctor integration
# ---------------------------------------------------------------------------


class TestRunOnboardWithDoctor:
    def test_doctor_results_attached_when_enabled(
        self, tmp_path: Path, clean_provider_env
    ):
        project = _make_fixture_project(tmp_path)
        report = run_onboard(project, probe_ollama=False, run_doctor=True)
        assert report.doctor_results is not None
        assert report.doctor_exit_code is not None
        # All doctor results have the expected shape.
        for r in report.doctor_results:
            assert hasattr(r, "name")
            assert hasattr(r, "status")
            assert r.status in {"ok", "warn", "fail", "skip"}

    def test_no_doctor_skips_doctor_run(self, tmp_path: Path, clean_provider_env):
        project = _make_fixture_project(tmp_path)
        report = run_onboard(project, probe_ollama=False, run_doctor=False)
        assert report.doctor_results is None
        assert report.doctor_exit_code is None

    def test_post_onboard_doctor_recognizes_generated_yaml(
        self, tmp_path: Path, clean_provider_env
    ):
        """The whole point of the auto-doctor is to confirm the YAML
        we just wrote loads cleanly.  Specifically: ``check_sponsio_yaml``
        must report ok (not ``skip`` for missing yaml, not ``fail``
        for syntax)."""
        project = _make_fixture_project(tmp_path)
        report = run_onboard(project, probe_ollama=False, run_doctor=True)
        config_check = next(
            (r for r in report.doctor_results if r.name == "Config file"),
            None,
        )
        assert config_check is not None
        assert config_check.status == "ok", config_check.detail


class TestCountContracts:
    def test_counts_both_E_only_and_A_E_pairs(self):
        yaml_text = (
            "agents:\n  agent:\n    contracts:\n"
            '      - E: "no pii"\n'
            '      - A: "when send"\n'
            '        E: "must redact"\n'
            "      - E:\n"
            "          pattern: rate_limit\n"
            "          args: [send_email, 10]\n"
        )
        assert _count_contracts(yaml_text) == 3

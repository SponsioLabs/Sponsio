"""Tests for ``include:`` — pulling shared contract packs into a host config.

The include mechanism is the headline UX for the contract-library
story: users name a pack (``sponsio:core/universal``) and every
contract from that pack's ``"*"`` template gets injected under the
host agent, with each entry source-tagged so ``overrides:`` and
``sponsio validate`` can address them by origin.

What's pinned here:

* Spec resolution — bundled (``sponsio:<cat>/<name>``), local relative
  paths, and absolute paths.  Bundled paths confined to the package's
  ``contracts/`` tree; spec sandbox-escape attempts rejected.
* End-to-end include under an agent — pulled contracts compile, are
  source-tagged, and live alongside hand-written contracts in order.
* Schema enforcement on the pack side — packs must have a single
  ``"*"`` agent; multi-agent packs and non-mapping templates are
  rejected at load time, not at compile time.
* Cycle detection on nested includes (a depends on b depends on a).
* Useful error messages — unknown spec lists available packs,
  missing local paths name the file, malformed includes name the
  agent.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from sponsio.config import (
    ConfigError,
    _resolve_include_spec,
    load_config,
)


# ---------------------------------------------------------------------------
# 1. Spec resolution
# ---------------------------------------------------------------------------


class TestResolveIncludeSpec:
    def test_bundled_pack_resolves_to_package_dir(self):
        """``sponsio:<cat>/<name>`` maps to a real file under
        ``sponsio/contracts/`` — the ground truth that lets installed
        users call up packs by name without knowing the install path."""
        path = _resolve_include_spec("sponsio:core/universal", Path.cwd())
        assert path.is_file()
        assert path.suffix == ".yaml"
        assert path.parent.name == "core"
        assert path.name == "universal.yaml"

    def test_yaml_suffix_optional(self):
        """``.yaml`` is optional in the spec — the loader appends it
        — so ``sponsio:core/universal`` and ``sponsio:core/universal.yaml``
        both work.  Saves the user from caring about file extensions."""
        a = _resolve_include_spec("sponsio:core/universal", Path.cwd())
        b = _resolve_include_spec("sponsio:core/universal.yaml", Path.cwd())
        assert a == b

    @pytest.mark.parametrize(
        "spec",
        [
            "sponsio:core/universal",
            "sponsio:core/runaway",
            "sponsio:capability/shell",
            "sponsio:capability/filesystem",
            "sponsio:incident/openclaw",
        ],
    )
    def test_every_shipped_pack_is_resolvable(self, spec):
        """Smoke test: each pack we ship must actually resolve.  Catches
        package-data omissions and category-rename regressions in one
        place — far cheaper than discovering the gap during user
        onboarding."""
        path = _resolve_include_spec(spec, Path.cwd())
        assert path.exists()

    def test_unknown_bundled_spec_lists_available(self):
        """When a user typos ``sponsio:core/univeral``, the error must
        list what's actually shipped so they can spot the right name
        without grepping the source."""
        with pytest.raises(ConfigError) as excinfo:
            _resolve_include_spec("sponsio:core/no_such_pack", Path.cwd())
        msg = str(excinfo.value)
        assert "no_such_pack" in msg
        assert "sponsio:core/universal" in msg

    def test_empty_bundled_spec_rejected(self):
        with pytest.raises(ConfigError, match="bundled spec is empty"):
            _resolve_include_spec("sponsio:", Path.cwd())

    def test_path_traversal_in_bundled_spec_rejected(self):
        """Defence in depth — ``sponsio:../../../etc/passwd`` must not
        escape the bundled tree even though we control the pack
        directory.  Cheap to check, hard to walk back if a user
        learns to rely on the escape."""
        with pytest.raises(ConfigError, match="outside the bundled"):
            _resolve_include_spec("sponsio:../../etc/passwd", Path.cwd())

    def test_relative_path_resolves_against_base_dir(self, tmp_path):
        """Relative paths (``./shared.yaml``) anchor at the *including*
        yaml's directory — not the cwd — so a project's shared pack
        keeps working when the project gets cd'd into from elsewhere."""
        (tmp_path / "shared.yaml").write_text(
            textwrap.dedent(
                """
                agents:
                  '*':
                    contracts:
                      - E: {pattern: rate_limit, args: [exec, 10]}
                """
            )
        )
        resolved = _resolve_include_spec("./shared.yaml", tmp_path)
        assert resolved == (tmp_path / "shared.yaml").resolve()

    def test_absolute_path_used_as_is(self, tmp_path):
        target = tmp_path / "abs.yaml"
        target.write_text("agents: {'*': {contracts: []}}")
        resolved = _resolve_include_spec(str(target), Path("/var"))
        assert resolved == target

    def test_missing_local_path_names_the_file(self, tmp_path):
        """A typo in the local path must surface the resolved
        absolute path — otherwise users can't tell if the file was
        looked up in the wrong directory."""
        with pytest.raises(ConfigError, match="not found"):
            _resolve_include_spec("./does-not-exist.yaml", tmp_path)

    def test_non_string_spec_rejected(self):
        with pytest.raises(ConfigError, match="non-empty string"):
            _resolve_include_spec(123, Path.cwd())  # type: ignore[arg-type]
        with pytest.raises(ConfigError, match="non-empty string"):
            _resolve_include_spec("   ", Path.cwd())


# ---------------------------------------------------------------------------
# 2. End-to-end: include: under an agent
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body))
    return p


class TestIncludeIntoAgent:
    def test_single_bundled_pack_pulled_in(self, tmp_path):
        cfg_path = _write_yaml(
            tmp_path,
            "sponsio.yaml",
            """
            agents:
              bot:
                include:
                  - sponsio:core/llm_safety
            """,
        )
        cfg = load_config(cfg_path)
        ac = cfg.agents["bot"]
        # llm_safety.yaml ships 5 sto contracts under '*'; the pin
        # protects against accidental deletions.  The count dropped
        # from 6 when scope_respect was moved out (generic default
        # scope string was an irredeemable source of judge noise —
        # see the pack file's § Scope block for the rationale and
        # the per-agent recipe).  These five contracts originally
        # lived in ``core/universal`` but moved here so the universal
        # pack stops auto-pulling judge-LLM evaluations.
        assert len(ac.contracts) == 5
        # Every pulled contract must be source-tagged so overrides:
        # has something to address.
        assert all(c.pack_source == "sponsio:core/llm_safety" for c in ac.contracts)

    def test_multiple_packs_concat_in_order(self, tmp_path):
        """When two packs are included, contracts from the first appear
        before contracts from the second.  Stable order is part of the
        contract — overrides: target by index in some configurations.

        Uses llm_safety + capability/shell because both packs are
        non-empty (``core/runaway`` and ``core/universal`` are both
        intentionally empty stubs now — they wouldn't contribute any
        contracts to compare ordering against).
        """
        cfg_path = _write_yaml(
            tmp_path,
            "sponsio.yaml",
            """
            agents:
              bot:
                include:
                  - sponsio:core/llm_safety
                  - sponsio:capability/shell
            """,
        )
        cfg = load_config(cfg_path)
        ac = cfg.agents["bot"]
        sources = [c.pack_source for c in ac.contracts]
        first_shell = sources.index("sponsio:capability/shell")
        last_llm_safety = (
            len(sources) - 1 - sources[::-1].index("sponsio:core/llm_safety")
        )
        assert last_llm_safety < first_shell

    def test_local_contracts_appended_after_includes(self, tmp_path):
        """Hand-written contracts appear AFTER everything pulled from
        includes — so when a user reads the merged config they see
        their own rules at the bottom, where their cursor naturally
        lands.  Also makes ``overrides:`` cleaner: local contracts
        always have ``pack_source is None``."""
        cfg_path = _write_yaml(
            tmp_path,
            "sponsio.yaml",
            """
            tools:
              - name: exec
            agents:
              bot:
                include:
                  - sponsio:core/universal
                contracts:
                  - desc: "my own thing"
                    E: {pattern: rate_limit, args: [exec, 100]}
            """,
        )
        cfg = load_config(cfg_path)
        contracts = cfg.agents["bot"].contracts
        assert contracts[-1].pack_source is None
        assert contracts[-1].desc == "my own thing"
        assert all(c.pack_source is not None for c in contracts[:-1])

    def test_pulled_contracts_compile(self, tmp_path):
        """Inclusion is value-add only if the pulled contracts actually
        compile in the host context.  This verifies that the routing
        path is end-to-end clean — no half-included packs.  The
        filesystem pack uses ``<workspace>/`` placeholders, so the
        host must set ``workspace:`` — exercising the same path real
        users will hit."""
        from sponsio.config import _compile_field

        cfg_path = _write_yaml(
            tmp_path,
            "sponsio.yaml",
            """
            agents:
              bot:
                workspace: "/Users/me/proj"
                include:
                  - sponsio:core/universal
                  - sponsio:capability/filesystem
            """,
        )
        cfg = load_config(cfg_path)
        for c in cfg.agents["bot"].contracts:
            _compile_field(c.enforcement)
            if c.assumption is not None:
                _compile_field(c.assumption)

    def test_local_relative_pack(self, tmp_path):
        """Mirrors how teams keep their own pack repo and share it via
        ``include: ['./shared/policies.yaml']``."""
        _write_yaml(
            tmp_path,
            "shared.yaml",
            """
            agents:
              '*':
                contracts:
                  - desc: "internal rule"
                    E: {pattern: must_precede, args: [auth, refund]}
            """,
        )
        cfg_path = _write_yaml(
            tmp_path,
            "sponsio.yaml",
            """
            agents:
              bot:
                include: ["./shared.yaml"]
            """,
        )
        cfg = load_config(cfg_path)
        contracts = cfg.agents["bot"].contracts
        assert len(contracts) == 1
        assert contracts[0].pack_source == "./shared.yaml"

    def test_include_must_be_list(self, tmp_path):
        cfg_path = _write_yaml(
            tmp_path,
            "sponsio.yaml",
            """
            agents:
              bot:
                include: "sponsio:core/universal"
            """,
        )
        with pytest.raises(ConfigError, match="must be a list"):
            load_config(cfg_path)


# ---------------------------------------------------------------------------
# 3. Pack-shape enforcement
# ---------------------------------------------------------------------------


class TestPackSchema:
    def test_pack_must_have_star_agent(self, tmp_path):
        _write_yaml(
            tmp_path,
            "bad.yaml",
            """
            agents:
              concrete_agent:
                contracts:
                  - E: {pattern: rate_limit, args: [exec, 1]}
            """,
        )
        cfg_path = _write_yaml(
            tmp_path,
            "sponsio.yaml",
            """
            agents:
              bot:
                include: ["./bad.yaml"]
            """,
        )
        with pytest.raises(ConfigError, match="exactly one agent named"):
            load_config(cfg_path)

    def test_pack_must_have_only_star_agent(self, tmp_path):
        """Multi-agent packs would be ambiguous — which one's contracts
        get pulled?  We make the user split the file rather than guess."""
        _write_yaml(
            tmp_path,
            "bad.yaml",
            """
            agents:
              '*': {contracts: []}
              other:
                contracts:
                  - E: {pattern: rate_limit, args: [x, 1]}
            """,
        )
        cfg_path = _write_yaml(
            tmp_path,
            "sponsio.yaml",
            """
            agents:
              bot:
                include: ["./bad.yaml"]
            """,
        )
        with pytest.raises(ConfigError, match="exactly one agent named"):
            load_config(cfg_path)

    def test_pack_with_empty_agents_rejected(self, tmp_path):
        _write_yaml(tmp_path, "bad.yaml", "agents: {}\n")
        cfg_path = _write_yaml(
            tmp_path,
            "sponsio.yaml",
            """
            agents:
              bot:
                include: ["./bad.yaml"]
            """,
        )
        with pytest.raises(ConfigError, match="agents:"):
            load_config(cfg_path)


# ---------------------------------------------------------------------------
# 4. Nested includes + cycle detection
# ---------------------------------------------------------------------------


class TestNestedInclude:
    def test_pack_can_include_another_pack(self, tmp_path):
        """Composition is the point — if every team has to inline
        every dep, the library scales linearly with users."""
        _write_yaml(
            tmp_path,
            "leaf.yaml",
            """
            agents:
              '*':
                contracts:
                  - E: {pattern: rate_limit, args: [exec, 5]}
            """,
        )
        _write_yaml(
            tmp_path,
            "middle.yaml",
            """
            agents:
              '*':
                include: ["./leaf.yaml"]
                contracts:
                  - E: {pattern: rate_limit, args: [exec, 50]}
            """,
        )
        cfg_path = _write_yaml(
            tmp_path,
            "sponsio.yaml",
            """
            agents:
              bot:
                include: ["./middle.yaml"]
            """,
        )
        cfg = load_config(cfg_path)
        contracts = cfg.agents["bot"].contracts
        # leaf contract first (nested includes resolve before
        # the wrapping pack's own contracts), then middle's own.
        assert len(contracts) == 2
        assert contracts[0].pack_source == "./leaf.yaml"
        assert contracts[1].pack_source == "./middle.yaml"

    def test_cycle_is_detected(self, tmp_path):
        """A direct cycle (a includes b includes a) must fail with a
        readable chain so the user can break the loop."""
        _write_yaml(
            tmp_path,
            "a.yaml",
            """
            agents:
              '*':
                include: ["./b.yaml"]
                contracts: []
            """,
        )
        _write_yaml(
            tmp_path,
            "b.yaml",
            """
            agents:
              '*':
                include: ["./a.yaml"]
                contracts: []
            """,
        )
        cfg_path = _write_yaml(
            tmp_path,
            "sponsio.yaml",
            """
            agents:
              bot:
                include: ["./a.yaml"]
            """,
        )
        with pytest.raises(ConfigError, match="cycle detected"):
            load_config(cfg_path)

"""Tests for ``tool_rename:`` + ``workspace:`` — adapt pulled-in pack
contents to the host's vocabulary.

A pack like ``sponsio:capability/shell`` ships with the generic tool
name ``exec``; teams whose actual tool is named ``bash`` (or whose
filesystem tools are ``read_file`` / ``write_file`` instead of
``read`` / ``write``) need a way to opt into the pack without
forking it.  Same for ``<workspace>/`` — packs name the placeholder,
the user's config resolves it to their actual project root.

What's pinned here:

* Schema validation — both ``workspace:`` and ``tool_rename:`` parse
  cleanly when present, are ignored when absent, and reject the
  obvious bad shapes (non-string values, cycles, no-op self-mappings).
* Rewrite scope — only ``args`` and ``ltl`` strings get touched;
  ``nl`` / ``pattern`` / ``desc`` / ``source`` are left alone.
* Whole-identifier renames — ``exec → bash`` doesn't accidentally
  replace ``executor`` or ``rexec`` substrings.
* Nested args — ``args: [scope_limit, [<workspace>/, /tmp/]]`` walks
  list elements recursively.
* Safety net — if a pack uses ``<workspace>/`` and the host forgets
  to set it, ConfigError names the offending pattern at load time
  rather than letting the rule mis-fire on every runtime event.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from sponsio.config import (
    ConfigError,
    ConstraintEntry,
    ContractEntry,
    _parse_tool_rename,
    _rewrite_arg,
    _rewrite_constraint_entry,
    _rewrite_string,
    load_config,
)


# ---------------------------------------------------------------------------
# 1. _rewrite_string — the per-string primitive
# ---------------------------------------------------------------------------


class TestRewriteString:
    def test_workspace_substitution(self):
        out = _rewrite_string("<workspace>/src/main.py", "/Users/me/proj", {})
        assert out == "/Users/me/proj/src/main.py"

    def test_workspace_trailing_slash_normalized(self):
        """Whether the user wrote ``/Users/me/proj`` or
        ``/Users/me/proj/``, the result must be the same — drop the
        trailing slash on the user's value, then re-append.  Otherwise
        a pack rule like ``<workspace>/src`` and a user-config like
        ``workspace: /Users/me/proj/`` would yield ``//src``."""
        a = _rewrite_string("<workspace>/x", "/Users/me/proj", {})
        b = _rewrite_string("<workspace>/x", "/Users/me/proj/", {})
        assert a == b == "/Users/me/proj/x"

    def test_no_workspace_leaves_placeholder_alone(self):
        """A None workspace doesn't substitute — the leftover-check
        in `_rewrite_constraint_entry` is what surfaces the error.
        Splitting that responsibility means the string-level helper
        stays a pure transformation."""
        out = _rewrite_string("<workspace>/x", None, {})
        assert out == "<workspace>/x"

    def test_tool_rename_word_boundary(self):
        """``exec → bash`` must not corrupt ``executor`` or ``rexec``
        — those are different identifiers.  Pack contents lean on
        this: the bash regex blacklist mentions ``execute`` in
        comments and ``exec`` as the tool name, and the user must
        get only the latter rewritten."""
        out = _rewrite_string(
            "called(exec) and called(executor)", None, {"exec": "bash"}
        )
        assert out == "called(bash) and called(executor)"

    def test_combined_workspace_and_rename(self):
        out = _rewrite_string(
            "<workspace>/src + exec",
            "/proj",
            {"exec": "bash"},
        )
        assert out == "/proj/src + bash"


# ---------------------------------------------------------------------------
# 2. _rewrite_arg — recurses into list args
# ---------------------------------------------------------------------------


class TestRewriteArg:
    def test_string_arg_exact_match_renames(self):
        """When a tool name is the *whole* string arg (no surrounding
        text, no word boundary), the rename must still fire — e.g.
        ``args: [exec, 50]`` becomes ``args: [bash, 50]``."""
        assert _rewrite_arg("exec", None, {"exec": "bash"}) == "bash"

    def test_string_arg_substring_renames_via_boundary(self):
        """Substring match within a longer string still goes through
        the word-boundary regex — important for regex args that name
        a tool inline."""
        assert _rewrite_arg("called(exec)", None, {"exec": "bash"}) == "called(bash)"

    def test_list_arg_recurses(self):
        """``args: [scope_limit, [<workspace>/, /tmp/]]`` — the second
        arg is a list, and each element needs the substitution.  This
        is the literal shape the filesystem pack uses."""
        out = _rewrite_arg(
            ["<workspace>/AGENTS.md", "<workspace>/SOUL.md"], "/proj", {}
        )
        assert out == ["/proj/AGENTS.md", "/proj/SOUL.md"]

    def test_non_string_scalars_pass_through(self):
        for v in [50, 0.95, True, None]:
            assert _rewrite_arg(v, "/proj", {"exec": "bash"}) == v


# ---------------------------------------------------------------------------
# 3. _rewrite_constraint_entry — full ConstraintEntry walk + leftover check
# ---------------------------------------------------------------------------


class TestRewriteConstraintEntry:
    def test_args_and_ltl_get_rewritten(self):
        ce = ConstraintEntry(
            pattern="rate_limit",
            args=["exec", 50],
            ltl=None,
        )
        _rewrite_constraint_entry(ce, "/proj", {"exec": "bash"}, "bot", True)
        assert ce.args == ["bash", 50]

        ce2 = ConstraintEntry(
            ltl="G(called(exec) -> count(confirm) >= count(exec))",
        )
        _rewrite_constraint_entry(ce2, None, {"exec": "bash"}, "bot", True)
        assert ce2.ltl == "G(called(bash) -> count(confirm) >= count(bash))"

    def test_pattern_name_not_rewritten(self):
        """Pattern names are stable identifiers — renaming
        ``rate_limit`` would break the registry lookup downstream.
        The rewrite must scope to args/ltl only."""
        ce = ConstraintEntry(pattern="rate_limit", args=["exec", 50])
        _rewrite_constraint_entry(
            ce, None, {"rate_limit": "rate", "exec": "bash"}, "bot", True
        )
        assert ce.pattern == "rate_limit"
        assert ce.args == ["bash", 50]

    def test_nl_field_not_rewritten(self):
        """NL is fluid prose; substituting ``exec → bash`` could
        corrupt grammar (``execute`` becomes ``bashute``).  We trust
        the user's NL and don't touch it."""
        ce = ConstraintEntry(nl="agent must call exec then confirm")
        _rewrite_constraint_entry(ce, None, {"exec": "bash"}, "bot", True)
        assert ce.nl == "agent must call exec then confirm"

    def test_unsubstituted_workspace_raises_with_offending_value(self):
        """The whole reason this check exists: a placeholder reaching
        runtime would silently mis-match every path.  The error must
        name both the pattern and the offending arg so the user can
        find the line in their config."""
        ce = ConstraintEntry(
            pattern="scope_limit",
            args=["write", ["<workspace>/"]],
        )
        with pytest.raises(ConfigError) as excinfo:
            _rewrite_constraint_entry(ce, None, {}, "bot", True)
        msg = str(excinfo.value)
        assert "scope_limit" in msg
        assert "<workspace>/" in msg
        assert "workspace:" in msg

    def test_unsubstituted_workspace_in_ltl_raises(self):
        """LTL strings get the same check — a contract whose LTL
        formula references ``<workspace>/`` would fail to evaluate
        anything sensibly."""
        ce = ConstraintEntry(
            ltl='G(arg(write, path) starts_with "<workspace>/")',
        )
        with pytest.raises(ConfigError, match="<workspace>/"):
            _rewrite_constraint_entry(ce, None, {}, "bot", True)

    def test_check_skipped_when_not_enforced(self):
        """The ``enforce_placeholder_check=False`` path: directly
        inspecting a pack file with ``sponsio validate`` shouldn't
        require a host workspace.  The rewrite still applies (so the
        placeholder remains literal), but no error fires."""
        ce = ConstraintEntry(
            pattern="scope_limit",
            args=["write", ["<workspace>/"]],
        )
        _rewrite_constraint_entry(ce, None, {}, "bot", False)
        assert ce.args == ["write", ["<workspace>/"]]


# ---------------------------------------------------------------------------
# 4. _parse_tool_rename — schema validation
# ---------------------------------------------------------------------------


class TestParseToolRename:
    def test_absent_returns_empty_dict(self):
        assert _parse_tool_rename(None, "bot") == {}

    def test_well_formed_round_trips(self):
        out = _parse_tool_rename({"exec": "bash", "read": "read_file"}, "bot")
        assert out == {"exec": "bash", "read": "read_file"}

    def test_non_dict_rejected(self):
        with pytest.raises(ConfigError, match="must be a mapping"):
            _parse_tool_rename(["exec=bash"], "bot")  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad_key", ["", "   ", 5, None])
    def test_bad_keys_rejected(self, bad_key):
        with pytest.raises(ConfigError, match="keys must be"):
            _parse_tool_rename({bad_key: "bash"}, "bot")

    @pytest.mark.parametrize("bad_val", ["", "   ", 5, None])
    def test_bad_values_rejected(self, bad_val):
        with pytest.raises(ConfigError, match="values must be"):
            _parse_tool_rename({"exec": bad_val}, "bot")

    def test_self_mapping_rejected(self):
        """``exec: exec`` is a no-op — almost certainly a typo.  If a
        user really wanted to assert "yes I know exec is the tool
        name", they can leave the entry out."""
        with pytest.raises(ConfigError, match="self-mapping"):
            _parse_tool_rename({"exec": "exec"}, "bot")

    def test_cycle_rejected(self):
        """``exec → bash`` and ``bash → exec`` together would make
        rewrite order observable (apply ``exec → bash`` first and
        ``bash → exec`` second yields ``exec`` again).  Reject at
        parse time rather than expose order to users."""
        with pytest.raises(ConfigError, match="cycle"):
            _parse_tool_rename({"exec": "bash", "bash": "exec"}, "bot")


# ---------------------------------------------------------------------------
# 5. End-to-end via load_config
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body))
    return p


class TestEndToEnd:
    def test_workspace_resolves_in_filesystem_pack(self, tmp_path):
        """The full UX: include filesystem-strict pack, set workspace,
        every ``<workspace>/`` placeholder becomes the user's actual
        root.  Picks the ``Writes restricted to workspace`` rule
        because it has the simplest args shape to assert against.

        ``filesystem-strict`` (not the base ``filesystem``) carries
        the workspace-using rules — they were split off because they
        false-positive on relative-path traces and require an
        absolute workspace to be useful.
        """
        cfg_path = _write_yaml(
            tmp_path,
            "sponsio.yaml",
            """
            agents:
              bot:
                workspace: "/Users/me/proj"
                include: [sponsio:capability/filesystem-strict]
            """,
        )
        cfg = load_config(cfg_path)
        write_rule = next(
            c
            for c in cfg.agents["bot"].contracts
            if c.desc and "Writes restricted" in c.desc
        )
        assert write_rule.guarantee.args == ["write", ["/Users/me/proj/"]]

    def test_tool_rename_propagates_through_includes(self, tmp_path):
        """Rename ``exec → bash`` and verify the shell pack's first
        contract (``arg_blacklist`` for ``exec``) now references
        ``bash`` after load.  This is the exact UX a user gets when
        their tool is registered as ``bash`` and they want the shell
        pack."""
        cfg_path = _write_yaml(
            tmp_path,
            "sponsio.yaml",
            """
            agents:
              bot:
                tool_rename: {exec: bash}
                include: [sponsio:capability/shell]
            """,
        )
        cfg = load_config(cfg_path)
        first_arg_blacklist = next(
            c
            for c in cfg.agents["bot"].contracts
            if c.guarantee
            and not isinstance(c.guarantee, list)
            and c.guarantee.pattern == "arg_blacklist"
        )
        # First positional arg of arg_blacklist is the tool name
        assert first_arg_blacklist.guarantee.args[0] == "bash"

    def test_missing_workspace_with_placeholder_pack_errors(self, tmp_path):
        """The smoothness pin: forgetting ``workspace:`` while
        including the filesystem-strict pack must fail at load time,
        not runtime.  The error must name what's missing and give a
        copy-pasteable fix."""
        cfg_path = _write_yaml(
            tmp_path,
            "sponsio.yaml",
            """
            agents:
              bot:
                include: [sponsio:capability/filesystem-strict]
            """,
        )
        with pytest.raises(ConfigError) as excinfo:
            load_config(cfg_path)
        msg = str(excinfo.value)
        assert "workspace:" in msg
        assert "<workspace>/" in msg

    def test_local_contracts_also_get_rewrites(self, tmp_path):
        """Rewrites apply to hand-written contracts too — the user
        might use ``<workspace>/`` in their own rules to keep the
        config portable across machines.  Single mental model: every
        contract under the agent goes through the same rewriter."""
        cfg_path = _write_yaml(
            tmp_path,
            "sponsio.yaml",
            """
            tools: [{name: read}, {name: write}]
            agents:
              bot:
                workspace: "/proj"
                tool_rename: {read: read_file}
                contracts:
                  - desc: "team rule"
                    G: {pattern: must_precede, args: [read, write]}
            """,
        )
        cfg = load_config(cfg_path)
        team_rule = cfg.agents["bot"].contracts[0]
        assert team_rule.guarantee.args == ["read_file", "write"]

    def test_workspace_must_be_non_empty_string(self, tmp_path):
        cfg_path = _write_yaml(
            tmp_path,
            "sponsio.yaml",
            """
            agents:
              bot:
                workspace: ""
                contracts: []
            """,
        )
        with pytest.raises(ConfigError, match="workspace.*non-empty"):
            load_config(cfg_path)


# ---------------------------------------------------------------------------
# 6. ContractEntry-level walk handles list shapes
# ---------------------------------------------------------------------------


class TestRewriteListShapes:
    def test_enforcement_as_list_walked(self):
        """``enforcement:`` may be a list (= AND of constraints).  The
        rewriter must walk every element, not just the first."""
        from sponsio.config import _rewrite_contract_entry

        ce_a = ConstraintEntry(pattern="rate_limit", args=["exec", 5])
        ce_b = ConstraintEntry(pattern="must_precede", args=["read", "exec"])
        contract = ContractEntry(guarantee=[ce_a, ce_b])
        _rewrite_contract_entry(contract, None, {"exec": "bash"}, "bot")
        assert ce_a.args == ["bash", 5]
        assert ce_b.args == ["read", "bash"]

    def test_assumption_field_also_walked(self):
        from sponsio.config import _rewrite_contract_entry

        a = ConstraintEntry(pattern="called", args=["exec"])
        e = ConstraintEntry(pattern="rate_limit", args=["exec", 1])
        contract = ContractEntry(guarantee=e, assumption=a)
        _rewrite_contract_entry(contract, None, {"exec": "bash"}, "bot")
        assert a.args == ["bash"]
        assert e.args == ["bash", 1]

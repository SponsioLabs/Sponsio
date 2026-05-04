"""Tests for the ``customized:`` section — disable / tweak individual
contracts pulled in via ``include:`` without forking the pack.

Why overrides matter: the value prop of shipped contract packs
(``sponsio:capability/shell`` etc.) collapses if the only way to
disagree with a single rule is to copy-paste the whole pack.
``customized:`` keeps the pack as the source of truth and lets
each host say "all of that, except this one rule which I want
disabled / tightened / scoped differently".

What's pinned here:

* **Match shape** — every supported match key (`desc`, `pack_source`,
  `source`, `pattern`); AND-semantics across keys; non-empty match
  required (empty would silently apply to everything).
* **Effect shape** — `disabled: true` drops the contract; field
  edits (`threshold`, `prompt_override`, `context_scope`) write back
  to the enforcement constraint.  Mixing `disabled` with edits is
  rejected (the edits would be dead code).
* **Drift catch** — unmatched override rules raise `ConfigError`
  listing them.  This is the whole point of strict matching: pack
  version bumps shouldn't silently re-enable rules the user
  intentionally turned off.
* **End-to-end** — overrides applied via `load_config` against the
  shipped shell pack work as expected.

Schema reminder::

    customized:
      - match: {desc: "Ban recursive deletes of sensitive roots"}
        disabled: true
      - match: {pack_source: "sponsio:capability/shell"}
        threshold: 0.85
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from sponsio.config import (
    ConfigError,
    ConstraintEntry,
    ContractEntry,
    OverrideRule,
    _apply_overrides,
    _matches_override,
    _parse_override_rule,
    load_config,
)


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "sponsio.yaml"
    p.write_text(textwrap.dedent(body))
    return p


def _ce(**kw) -> ConstraintEntry:
    return ConstraintEntry(**kw)


def _ct(enforcement, **kw) -> ContractEntry:
    return ContractEntry(guarantee=enforcement, **kw)


# ---------------------------------------------------------------------------
# 1. _parse_override_rule — schema validation
# ---------------------------------------------------------------------------


class TestParseOverrideRule:
    def test_minimal_disable_rule(self):
        r = _parse_override_rule({"match": {"desc": "x"}, "disabled": True}, "bot", 0)
        assert r.match == {"desc": "x"}
        assert r.disabled is True
        assert r.threshold is None

    def test_threshold_edit_rule(self):
        r = _parse_override_rule(
            {"match": {"pattern": "injection_free"}, "threshold": 0.9},
            "bot",
            0,
        )
        assert r.threshold == 0.9
        assert r.disabled is False

    def test_must_be_dict(self):
        with pytest.raises(ConfigError, match="must be a mapping"):
            _parse_override_rule(["match", "disabled"], "bot", 0)  # type: ignore[arg-type]

    def test_match_required_and_non_empty(self):
        """Empty `match` would silently apply to everything — that's
        the worst possible UX for a feature whose whole purpose is
        precise targeting."""
        with pytest.raises(ConfigError, match="non-empty 'match:'"):
            _parse_override_rule({"disabled": True}, "bot", 0)
        with pytest.raises(ConfigError, match="non-empty 'match:'"):
            _parse_override_rule({"match": {}, "disabled": True}, "bot", 0)

    def test_unknown_match_key_rejected(self):
        """Typing `descripton:` instead of `desc:` would silently
        match nothing if we ignored unknown keys; that's recoverable
        but slow.  Reject at parse time and list valid keys."""
        with pytest.raises(ConfigError, match="unknown match keys"):
            _parse_override_rule(
                {"match": {"descripton": "x"}, "disabled": True}, "bot", 0
            )

    def test_unknown_effect_key_rejected(self):
        with pytest.raises(ConfigError, match="unknown effect keys"):
            _parse_override_rule({"match": {"desc": "x"}, "enabled": False}, "bot", 0)

    def test_no_effect_rejected(self):
        """A match with no effect is a no-op.  We reject it because
        it's almost certainly an in-progress edit the user forgot
        to finish."""
        with pytest.raises(ConfigError, match="has no effect"):
            _parse_override_rule({"match": {"desc": "x"}}, "bot", 0)

    def test_disabled_must_be_bool(self):
        with pytest.raises(ConfigError, match="disabled must be a boolean"):
            _parse_override_rule({"match": {"desc": "x"}, "disabled": "yes"}, "bot", 0)

    @pytest.mark.parametrize("bad", [-0.1, 1.1, "0.5", None])
    def test_threshold_in_range(self, bad):
        with pytest.raises(ConfigError, match="threshold must be"):
            _parse_override_rule({"match": {"desc": "x"}, "threshold": bad}, "bot", 0)

    def test_disabled_with_edits_rejected(self):
        """Edits next to ``disabled: true`` are dead code — the
        contract is gone, so nothing reads them.  Catching the
        contradiction at parse time preserves user intent.  Either
        the user wanted a disable (drop the edit) or they wanted a
        tweak (drop the disable)."""
        with pytest.raises(ConfigError, match="alongside field-edits"):
            _parse_override_rule(
                {"match": {"desc": "x"}, "disabled": True, "threshold": 0.5},
                "bot",
                0,
            )

    def test_match_value_must_be_non_empty_string(self):
        with pytest.raises(ConfigError, match="non-empty string"):
            _parse_override_rule({"match": {"desc": ""}, "disabled": True}, "bot", 0)


# ---------------------------------------------------------------------------
# 2. _matches_override — match semantics
# ---------------------------------------------------------------------------


class TestMatchesOverride:
    def test_desc_match(self):
        c = _ct(_ce(pattern="rate_limit"), desc="hello")
        assert _matches_override(OverrideRule({"desc": "hello"}), c)
        assert not _matches_override(OverrideRule({"desc": "world"}), c)

    def test_pack_source_match(self):
        c = _ct(_ce(pattern="x"), pack_source="sponsio:core/universal")
        assert _matches_override(
            OverrideRule({"pack_source": "sponsio:core/universal"}), c
        )
        assert not _matches_override(
            OverrideRule({"pack_source": "sponsio:capability/shell"}), c
        )

    def test_source_match_constraint_level(self):
        """``source`` lives on ConstraintEntry — `_contract_constraints`
        flattens both single-CE and list-of-CE shapes so the matcher
        only sees one shape."""
        c = _ct(_ce(pattern="x", source="library:tier1.shell"))
        assert _matches_override(OverrideRule({"source": "library:tier1.shell"}), c)
        assert not _matches_override(OverrideRule({"source": "library:tier1.fs"}), c)

    def test_source_match_in_list_enforcement(self):
        """E may be a list-AND of constraints with different sources;
        match if *any* of them carries the requested source."""
        a = _ce(pattern="rate_limit", source="library:tier1.shell")
        b = _ce(pattern="must_precede", source="library:tier2.audit")
        c = _ct([a, b])
        assert _matches_override(OverrideRule({"source": "library:tier2.audit"}), c)

    def test_pattern_match(self):
        c = _ct(_ce(pattern="injection_free"))
        assert _matches_override(OverrideRule({"pattern": "injection_free"}), c)
        assert not _matches_override(OverrideRule({"pattern": "rate_limit"}), c)

    def test_and_semantics(self):
        """Multiple match keys — ALL must agree.  Pinning AND not OR
        is what makes overrides precise; an OR semantics would drop
        too much and surprise users."""
        c = _ct(_ce(pattern="rate_limit"), desc="hello")
        rule = OverrideRule({"desc": "hello", "pattern": "rate_limit"})
        assert _matches_override(rule, c)
        rule_partial = OverrideRule({"desc": "hello", "pattern": "must_precede"})
        assert not _matches_override(rule_partial, c)


# ---------------------------------------------------------------------------
# 3. _apply_overrides — disable / edit / drift catch
# ---------------------------------------------------------------------------


class TestApplyOverrides:
    def test_disable_drops_contract(self):
        keep = _ct(_ce(pattern="rate_limit"), desc="keep")
        drop = _ct(_ce(pattern="rate_limit"), desc="drop")
        rule = OverrideRule({"desc": "drop"}, disabled=True)
        out = _apply_overrides([keep, drop], [rule], "bot")
        assert [c.desc for c in out] == ["keep"]

    def test_threshold_edit_writes_back(self):
        c = _ct(_ce(pattern="injection_free"), desc="x")
        rule = OverrideRule({"desc": "x"}, threshold=0.95)
        out = _apply_overrides([c], [rule], "bot")
        assert out[0].guarantee.threshold == 0.95

    def test_threshold_edit_writes_to_all_in_list_enforcement(self):
        """List-AND enforcement: the edit applies to every
        constraint.  We don't have a way to target a specific list
        element without exposing index-based matching, which would be
        more pain than gain."""
        a = _ce(pattern="x")
        b = _ce(pattern="y")
        c = _ct([a, b], desc="x")
        _apply_overrides([c], [OverrideRule({"desc": "x"}, threshold=0.7)], "bot")
        assert a.threshold == 0.7
        assert b.threshold == 0.7

    def test_unmatched_rule_raises(self):
        """The drift catch — pack version bumps that rename ``desc:``
        would silently re-enable a previously-disabled rule.  Failing
        loudly with the stale match clauses is far better."""
        c = _ct(_ce(pattern="x"), desc="hello")
        rule = OverrideRule({"desc": "ghost"}, disabled=True)
        with pytest.raises(ConfigError) as excinfo:
            _apply_overrides([c], [rule], "bot")
        msg = str(excinfo.value)
        assert "matched no contract" in msg
        assert "ghost" in msg

    def test_partial_unmatched_still_raises(self):
        """Even when *some* rules match, the unmatched ones still
        surface — silent failure of one rule is just as bad as
        silent failure of all rules."""
        c = _ct(_ce(pattern="x"), desc="real")
        rules = [
            OverrideRule({"desc": "real"}, disabled=True),
            OverrideRule({"desc": "ghost"}, disabled=True),
        ]
        with pytest.raises(ConfigError, match="ghost"):
            _apply_overrides([c], rules, "bot")

    def test_pack_source_disable_drops_all_from_pack(self):
        """The ergonomic shortcut: "I don't trust this whole pack"
        without having to enumerate every rule.  Demonstrates one
        rule taking out N contracts in one go."""
        ps = "sponsio:capability/shell"
        contracts = [
            _ct(_ce(pattern="x"), desc=f"r{i}", pack_source=ps) for i in range(5)
        ]
        rule = OverrideRule({"pack_source": ps}, disabled=True)
        out = _apply_overrides(contracts, [rule], "bot")
        assert out == []

    def test_apply_order_independent_for_disjoint_rules(self):
        """Overrides on disjoint match sets must commute — we don't
        want the order in YAML to leak into semantics.

        Fresh contract copies are built inline for each run below since
        ``matched_count`` mutates; the inline rebuild is the fixture.
        """
        rules1 = [
            OverrideRule({"desc": "a"}, disabled=True),
            OverrideRule({"desc": "b"}, threshold=0.5),
        ]
        rules2 = [
            OverrideRule({"desc": "b"}, threshold=0.5),
            OverrideRule({"desc": "a"}, disabled=True),
        ]
        # Reset fixtures between runs since matched_count mutates
        out1 = _apply_overrides(
            [_ct(_ce(pattern="x"), desc="a"), _ct(_ce(pattern="y"), desc="b")],
            rules1,
            "bot",
        )
        out2 = _apply_overrides(
            [_ct(_ce(pattern="x"), desc="a"), _ct(_ce(pattern="y"), desc="b")],
            rules2,
            "bot",
        )
        assert [c.desc for c in out1] == [c.desc for c in out2] == ["b"]
        assert out1[0].guarantee.threshold == out2[0].guarantee.threshold == 0.5


# ---------------------------------------------------------------------------
# 4. End-to-end via load_config against the real shell pack
# ---------------------------------------------------------------------------


class TestLoadConfigOverrides:
    def test_disable_real_pack_rule(self, tmp_path):
        """The full UX path: include the shell pack, disable one
        named rule, watch it disappear from the loaded contract
        list.  Uses a real desc from sponsio:capability/shell so we
        catch any drift between this test and the shipped pack."""
        cfg = load_config(
            _write(
                tmp_path,
                """
            agents:
              bot:
                workspace: "/proj"
                include: [sponsio:capability/shell]
                customized:
                  - match: {desc: "Ban recursive deletes of sensitive roots"}
                    disabled: true
            """,
            )
        )
        descs = [c.desc for c in cfg.agents["bot"].contracts]
        assert "Ban recursive deletes of sensitive roots" not in descs

    def test_pack_source_disable_drops_all_shell_rules(self, tmp_path):
        """Disabling by pack_source removes *every* contract from
        that pack — useful when including the pack for one team but
        wanting only the llm_safety pack's rules to apply for
        another agent."""
        cfg = load_config(
            _write(
                tmp_path,
                """
            agents:
              bot:
                workspace: "/proj"
                include:
                  - sponsio:core/llm_safety
                  - sponsio:capability/shell
                customized:
                  - match: {pack_source: "sponsio:capability/shell"}
                    disabled: true
            """,
            )
        )
        sources = {c.pack_source for c in cfg.agents["bot"].contracts}
        assert "sponsio:capability/shell" not in sources
        assert "sponsio:core/llm_safety" in sources

    def test_threshold_override_on_pattern(self, tmp_path):
        """Loosen the rate_limit threshold across the shell pack.
        Verifies field-edit write-back works through the whole load
        pipeline."""
        cfg = load_config(
            _write(
                tmp_path,
                """
            agents:
              bot:
                workspace: "/proj"
                include: [sponsio:capability/shell]
                customized:
                  - match: {pattern: rate_limit}
                    threshold: 0.42
            """,
            )
        )
        rates = [
            c
            for c in cfg.agents["bot"].contracts
            if c.guarantee
            and not isinstance(c.guarantee, list)
            and c.guarantee.pattern == "rate_limit"
        ]
        assert rates, "expected at least one rate_limit rule in the shell pack"
        for c in rates:
            assert c.guarantee.threshold == 0.42

    def test_unmatched_override_in_real_config_raises(self, tmp_path):
        """The drift catch in the full pipeline: a typo in `desc:`
        must surface as a clear ConfigError naming the offending
        match clause."""
        with pytest.raises(ConfigError) as excinfo:
            load_config(
                _write(
                    tmp_path,
                    """
                agents:
                  bot:
                    workspace: "/proj"
                    include: [sponsio:capability/shell]
                    customized:
                      - match: {desc: "Ban recurzive deletes of sensitive roots"}
                        disabled: true
                """,
                )
            )
        assert "matched no contract" in str(excinfo.value)
        assert "Ban recurzive" in str(excinfo.value)

    def test_overrides_apply_after_rewrites(self, tmp_path):
        """Rewrites (tool_rename, workspace) happen before overrides,
        so an override matching by `pattern:` sees the post-rewrite
        contract.  Pin this ordering — flipping it would mean
        matchers had to reason about pre-rewrite state, which is
        invisible to the user reading the YAML."""
        cfg = load_config(
            _write(
                tmp_path,
                """
            agents:
              bot:
                workspace: "/proj"
                tool_rename: {exec: bash}
                include: [sponsio:capability/shell]
                customized:
                  - match: {pattern: rate_limit}
                    threshold: 0.5
            """,
            )
        )
        # The rate_limit rule should still be present and now
        # references `bash` after rename
        rate = next(
            c
            for c in cfg.agents["bot"].contracts
            if c.guarantee
            and not isinstance(c.guarantee, list)
            and c.guarantee.pattern == "rate_limit"
        )
        assert rate.guarantee.args[0] == "bash"
        assert rate.guarantee.threshold == 0.5

    def test_overrides_must_be_list(self, tmp_path):
        with pytest.raises(ConfigError, match="'customized:' must be a list"):
            load_config(
                _write(
                    tmp_path,
                    """
                agents:
                  bot:
                    customized: {match: {desc: x}, disabled: true}
                """,
                )
            )

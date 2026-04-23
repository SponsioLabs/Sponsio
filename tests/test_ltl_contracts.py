"""Tests for the ``ltl:`` YAML field + repr-format parser fixes.

Covers the path contract-pack YAMLs depend on:

* The ``!`` token in :func:`sponsio.formulas.parser.parse_repr` must
  accept any unary sub-expression (``!called(x)`` / ``!F(...)`` /
  ``!flow(...)``), not just the historical ``!(...)`` form.
* The config loader must recognise ``{ltl: "G(...)"}`` entries,
  route them through :func:`sponsio.config._compile_ltl`, and surface
  a useful ``ConfigError`` when the formula doesn't parse.
* The shipped packs under ``contracts/`` must all validate end-to-end
  via ``sponsio.config.load_config``.

Each real LTL string pulled from the five contract packs is exercised
explicitly — so if someone edits a pack in a way that no longer parses,
a focused test fails instead of the generic "contract pack load failed"
at the integration layer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sponsio.config import (
    ConfigError,
    ConstraintEntry,
    _compile_ltl,
    _parse_constraint_entry,
    load_config,
)
from sponsio.formulas.parser import ParseError, parse_repr


# ---------------------------------------------------------------------------
# 1. parse_repr covers every LTL shape used in the contract packs
# ---------------------------------------------------------------------------


# Real strings pulled verbatim from the five packs under ``contracts/``.
# Keyed by (pack, section) for readable failures.
PACK_LTL_CASES: list[tuple[str, str]] = [
    (
        "runaway:any-tool-loop",
        "G(consecutive_count(__any_tool__) <= 10)",
    ),
    (
        "shell:privileged-requires-confirm",
        'G(called_with(exec, "(sudo\\s|dd\\s+if=|chmod\\s+777|chown\\s+-R|mount\\s)") '
        "-> count(confirm_reconfirmed) "
        '>= count_with(exec, "(sudo\\s|dd\\s+if=|chmod\\s+777|chown\\s+-R|mount\\s)"))',
    ),
    (
        "shell:confirm-count-dominates-exec",
        "G(called(exec) -> count(confirm_reconfirmed) >= count(exec))",
    ),
    (
        "filesystem:bootstrap-self-modify",
        'G(called_with(edit, "(AGENTS|SOUL|IDENTITY|USER|CLAUDE|\\.cursorrules)\\.md?") '
        "-> count(confirm_reconfirmed) "
        '>= count_with(edit, "(AGENTS|SOUL|IDENTITY|USER|CLAUDE|\\.cursorrules)\\.md?"))',
    ),
    (
        "openclaw:taint-flow",
        "G(contains(untrusted) -> !flow(openclaw_local, external))",
    ),
    (
        "openclaw:post-install-exec-window",
        "G(called(install_skill) -> (!called(exec) & X(!called(exec) & "
        "X(!called(exec) & X(!called(exec) & X(!called(exec)))))))",
    ),
    (
        "openclaw:channel-no-exec",
        'G(called_with(send_message, "channel_type=group") -> !F(called(exec)))',
    ),
]


@pytest.mark.parametrize(("label", "formula_text"), PACK_LTL_CASES)
def test_parse_repr_accepts_pack_ltl(label: str, formula_text: str):
    """Every LTL string shipped in ``contracts/*.yaml`` must parse."""
    tree = parse_repr(formula_text)
    # Whatever the top level is (G / F / Implies / etc.) the parse must
    # return a truthy node, not ``None`` or a raw token.
    assert tree is not None
    assert type(tree).__name__ in {"G", "F", "X", "Implies", "And", "Or", "Not"}, (
        f"{label}: unexpected root {type(tree).__name__}"
    )


class TestNotOperator:
    """Regression tests for the ``!`` fix in ``_parse_repr_unary``."""

    def test_bang_with_parens_still_works(self):
        """The historical ``!(expr)`` shape must keep parsing."""
        tree = parse_repr("!(called(a))")
        assert type(tree).__name__ == "Not"

    def test_bang_directly_on_predicate(self):
        """``!called(x)`` — previously rejected; must now parse."""
        tree = parse_repr("!called(x)")
        assert type(tree).__name__ == "Not"

    def test_bang_directly_on_temporal_operator(self):
        """``!F(...)`` and ``!G(...)`` — common in "never eventually X"."""
        for src in ("!F(called(x))", "!G(called(x))", "!X(called(x))"):
            tree = parse_repr(src)
            assert type(tree).__name__ == "Not", src

    def test_double_negation(self):
        """``!!x`` folds to two Not nodes — harmless but should parse."""
        tree = parse_repr("!!called(x)")
        assert type(tree).__name__ == "Not"
        inner = tree.child if hasattr(tree, "child") else None
        # fall back to first field regardless of the node's attribute name
        inner = inner or getattr(tree, "phi", None) or getattr(tree, "operand", None)
        if inner is not None:
            assert type(inner).__name__ == "Not"


# ---------------------------------------------------------------------------
# 2. Config loader recognises ``ltl:`` entries
# ---------------------------------------------------------------------------


class TestConstraintEntryLtl:
    def test_dict_with_ltl_key_parses(self):
        entry = _parse_constraint_entry(
            {"ltl": "G(called(x) -> F(called(y)))", "source": "test"}
        )
        assert isinstance(entry, ConstraintEntry)
        assert entry.is_ltl is True
        assert entry.is_structured is False
        assert entry.source == "test"

    def test_ltl_and_pattern_together_is_rejected(self):
        """Specifying both keys is almost always a typo — fail loud."""
        with pytest.raises(ConfigError, match="both 'pattern' and 'ltl'"):
            _parse_constraint_entry(
                {"pattern": "rate_limit", "args": [1], "ltl": "G(x)"}
            )

    def test_empty_ltl_is_rejected(self):
        with pytest.raises(ConfigError, match="non-empty string"):
            _parse_constraint_entry({"ltl": ""})
        with pytest.raises(ConfigError, match="non-empty string"):
            _parse_constraint_entry({"ltl": "   "})

    def test_nl_key_still_supported(self):
        """The explicit ``nl:`` dict form must still parse, not just the
        bare-string form — otherwise yamls that mix structured and NL
        entries break on round-trip."""
        entry = _parse_constraint_entry({"nl": "every send_email needs confirmation"})
        assert entry.nl == "every send_email needs confirmation"
        assert entry.is_structured is False
        assert entry.is_ltl is False


class TestCompileLtl:
    def test_well_formed_ltl_compiles_to_detformula(self):
        entry = ConstraintEntry(ltl="G(called(x) -> F(called(y)))")
        compiled = _compile_ltl(entry)
        # Runtime treats pattern-library output identically to this, so
        # we check the wrapper shape the runtime relies on.
        assert hasattr(compiled, "formula")
        assert hasattr(compiled, "desc")
        assert hasattr(compiled, "pattern_name")
        assert compiled.pattern_name == "ltl"

    def test_bad_ltl_wraps_parser_error(self):
        """We re-raise as ``ConfigError`` with the original LTL text
        included — the bare parser error is unactionable without it."""
        entry = ConstraintEntry(ltl="G(this is not valid")
        with pytest.raises(ConfigError, match="Failed to parse ltl formula"):
            _compile_ltl(entry)

    def test_compile_ltl_preserves_formula_shape(self):
        """Verify the compiled formula is a real AST, not a string."""
        entry = ConstraintEntry(ltl="G(called(exec))")
        compiled = _compile_ltl(entry)
        # Root must be a G node
        assert type(compiled.formula).__name__ == "G"


# ---------------------------------------------------------------------------
# 3. End-to-end: the shipped contract packs actually load
# ---------------------------------------------------------------------------


_PACKS_DIR = Path(__file__).resolve().parents[1] / "contracts"
_PACK_FILES = sorted(_PACKS_DIR.glob("*.yaml"))


@pytest.mark.parametrize(
    "pack_path", _PACK_FILES, ids=[p.stem for p in _PACK_FILES]
)
def test_contract_pack_parses_without_ltl_errors(pack_path: Path):
    """Each ``contracts/*.yaml`` must parse cleanly via ``load_config``
    — no ``ParseError`` from the LTL side, no ``ConfigError`` of any
    kind.

    Loading is the floor; full per-contract compilation is pinned in
    :mod:`tests.test_sto_patterns_in_yaml`.
    """
    cfg = load_config(pack_path)
    assert cfg is not None
    assert cfg.agents, f"{pack_path.name} parsed to an empty agents dict"

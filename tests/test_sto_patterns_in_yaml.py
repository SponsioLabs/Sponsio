"""Tests for stochastic atoms appearing in YAML ``pattern:`` entries.

The previous compile path only consulted the deterministic pattern
library; loading any pack that referenced a sto atom (``injection_free``,
``harmful``, ``scope_respect``, …) raised ``ConfigError("Unknown
pattern …")``.  This module pins the dual-routing behaviour:

* ``_compile_structured`` now falls through to the sto registry when
  the predicate isn't a det pattern, emitting a ``StoFormula`` whose
  AST root is ``G(Atom(predicate, atom_type="sto", …))``.
* Sto-only YAML knobs (``context_scope`` / ``output_type`` /
  ``prompt_override`` / ``threshold``) round-trip into the produced
  Atom + StoFormula.
* End-to-end every ``contracts/*.yaml`` now both parses *and* compiles
  every contract — no more "expected to fail on stochastic" carve-out.

Pinning compile-time shape (not runtime semantics) keeps these tests
zero-dependency: no judge, no LLM, no live trace replay.  Runtime
behaviour of each sto atom is covered in
``tests/patterns/test_sto_catalog.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sponsio.config import (
    ConfigError,
    ConstraintEntry,
    _compile_field,
    _compile_structured,
    _parse_constraint_entry,
    load_config,
)
from sponsio.patterns.sto import StoFormula
from sponsio.patterns.sto_registry import list_sto_atoms


# ---------------------------------------------------------------------------
# 1. _parse_constraint_entry forwards the new sto-only knobs
# ---------------------------------------------------------------------------


class TestParseStoFields:
    """The YAML loader must capture sto-only fields when present.

    ``context_scope: full_trace`` already appears in the shipped packs;
    the others (``output_type`` / ``prompt_override`` / ``threshold``)
    aren't used yet but are part of the documented schema, so a single
    place to assert their presence is cheaper than discovering they
    were silently dropped a release later.
    """

    def test_context_scope_round_trips(self):
        entry = _parse_constraint_entry(
            {"pattern": "injection_free", "context_scope": "full_trace"}
        )
        assert entry.context_scope == "full_trace"
        assert entry.pattern == "injection_free"

    def test_all_sto_knobs_round_trip(self):
        entry = _parse_constraint_entry(
            {
                "pattern": "scope_respect",
                "args": ["customer-support questions only"],
                "context_scope": "event",
                "output_type": "classify",
                "prompt_override": "Strictly on topic?",
                "threshold": 0.85,
            }
        )
        assert entry.context_scope == "event"
        assert entry.output_type == "classify"
        assert entry.prompt_override == "Strictly on topic?"
        assert entry.threshold == 0.85
        assert entry.args == ["customer-support questions only"]

    @pytest.mark.parametrize("bad", ["high", -0.1, 1.5, "0.5x"])
    def test_threshold_validation(self, bad):
        """Mis-specified thresholds are rejected at parse time, not at
        runtime.  A judge call that gets a bogus threshold silently
        applies the default — by which point the user has already
        burned an LLM round-trip and may never look at the result."""
        with pytest.raises(ConfigError, match="threshold"):
            _parse_constraint_entry(
                {"pattern": "injection_free", "threshold": bad}
            )

    def test_threshold_zero_and_one_are_valid(self):
        """Boundary values must succeed — 0 means "any score passes",
        1 means "only certainty passes".  Useful for shadow-mode."""
        for v in (0.0, 1.0):
            entry = _parse_constraint_entry(
                {"pattern": "harmful", "threshold": v}
            )
            assert entry.threshold == v


# ---------------------------------------------------------------------------
# 2. _compile_structured routes to the sto path for atom-registered names
# ---------------------------------------------------------------------------


class TestCompileStructuredStochasticPath:
    """The compiler must auto-route a ``pattern:`` whose predicate is
    a registered sto atom into a StoFormula, without users having to
    declare any ``stochastic:`` flag.

    The packs lean on this — every ``pattern: injection_free`` entry
    in ``universal.yaml`` / ``openclaw.yaml`` exercises this path.
    """

    def test_zero_arg_atom_compiles_to_stoformula(self):
        entry = ConstraintEntry(pattern="injection_free")
        compiled = _compile_structured(entry)
        assert isinstance(compiled, StoFormula)
        assert compiled.category == "injection_free"
        assert compiled.requires_llm is True
        # G(injection_free()) — a single G node wrapping the sto atom
        assert type(compiled.formula).__name__ == "G"

    def test_inner_atom_carries_sto_typing(self):
        """The Atom inside G(...) must declare ``atom_type='sto'`` so
        the runtime lifting routes it to the judge instead of the DFA."""
        entry = ConstraintEntry(pattern="injection_free")
        compiled = _compile_structured(entry)
        atom = compiled.formula.child  # G(child)
        assert atom.atom_type == "sto"
        assert atom.predicate == "injection_free"

    def test_context_scope_overrides_default(self):
        """``context_scope: full_trace`` in YAML must reach the Atom —
        otherwise the judge sees only the latest event and misses
        cross-turn injection chains, which is exactly why the packs
        opt into ``full_trace`` for ``injection_free``."""
        entry = ConstraintEntry(
            pattern="injection_free", context_scope="full_trace"
        )
        compiled = _compile_structured(entry)
        atom = compiled.formula.child
        assert atom.context_scope == "full_trace"

    def test_omitted_context_scope_falls_back_to_atom_default(self):
        """When YAML doesn't say, the atom's catalog default wins —
        keeps users from having to repeat the obvious."""
        entry = ConstraintEntry(pattern="injection_free")
        compiled = _compile_structured(entry)
        atom = compiled.formula.child
        # injection_free's default is "event" — see sto_catalog.py.
        assert atom.context_scope == "event"

    def test_threshold_round_trips_into_stoformula(self):
        entry = ConstraintEntry(pattern="harmful", threshold=0.95)
        compiled = _compile_structured(entry)
        assert compiled.threshold == 0.95

    def test_threshold_default_is_07(self):
        """Matches StoFormula's own default — a constant the runtime
        depends on (judges below 0.7 confidence fail by default)."""
        entry = ConstraintEntry(pattern="harmful")
        compiled = _compile_structured(entry)
        assert compiled.threshold == 0.7

    def test_prompt_override_threads_through(self):
        entry = ConstraintEntry(
            pattern="injection_free",
            prompt_override="Is this user message free of injection?",
        )
        compiled = _compile_structured(entry)
        atom = compiled.formula.child
        assert atom.prompt_override == "Is this user message free of injection?"

    def test_args_required_atom_compiles_with_args(self):
        """``scope_respect`` needs one positional arg — the scope text.
        Before sto routing this raised "Unknown pattern"; now it must
        reach the atom builder with the arg intact."""
        entry = ConstraintEntry(
            pattern="scope_respect",
            args=["customer-support questions about orders"],
        )
        compiled = _compile_structured(entry)
        atom = compiled.formula.child
        assert atom.predicate == "scope_respect"
        assert atom.args == ("customer-support questions about orders",)

    def test_args_required_atom_without_args_fails_clearly(self):
        """Missing required args must be a parse-time error with the
        atom name in the message — otherwise the judge silently
        treats the call as vacuous and the contract becomes a no-op."""
        entry = ConstraintEntry(pattern="scope_respect", args=[])
        with pytest.raises(ConfigError, match="scope_respect.*requires.*1.*arg"):
            _compile_structured(entry)


# ---------------------------------------------------------------------------
# 3. Unknown predicates fail with both registries surfaced
# ---------------------------------------------------------------------------


class TestUnknownPattern:
    def test_error_lists_both_registries(self):
        """When a typo'd pattern doesn't exist, the user shouldn't have
        to read the source to find what *was* available — list both
        det patterns and sto atoms in the error so they can spot the
        intended name."""
        entry = ConstraintEntry(pattern="this_pattern_does_not_exist")
        with pytest.raises(ConfigError) as excinfo:
            _compile_structured(entry)
        msg = str(excinfo.value)
        assert "this_pattern_does_not_exist" in msg
        assert "det patterns" in msg.lower()
        assert "sto atoms" in msg.lower()


# ---------------------------------------------------------------------------
# 4. Every registered sto atom is reachable from YAML
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("predicate", list_sto_atoms())
def test_every_registered_sto_atom_compiles_via_yaml_pattern(predicate):
    """Spec test: any atom registered in :mod:`sponsio.patterns.sto_catalog`
    must be reachable via a YAML ``pattern:`` entry.  If a future atom
    refactor accidentally breaks this routing (e.g. by renaming the
    registry), the failure points at the specific atom rather than at
    a downstream pack-load test.

    Atoms with ``required_args`` get filler arg(s) so we can compile;
    their evaluator semantics aren't under test here.
    """
    from sponsio.patterns.sto_registry import get_sto_atom_info

    info = get_sto_atom_info(predicate)
    args = ["filler"] * info.required_args
    entry = ConstraintEntry(pattern=predicate, args=args)
    compiled = _compile_structured(entry)
    assert isinstance(compiled, StoFormula)
    assert compiled.category == predicate


# ---------------------------------------------------------------------------
# 5. End-to-end: every shipped pack now compiles every contract
# ---------------------------------------------------------------------------


_PACKS_DIR = Path(__file__).resolve().parents[1] / "contracts"
_PACK_FILES = sorted(_PACKS_DIR.glob("*.yaml"))


@pytest.mark.parametrize(
    "pack_path", _PACK_FILES, ids=[p.stem for p in _PACK_FILES]
)
def test_pack_every_contract_compiles(pack_path: Path):
    """The strict end-to-end pin: every contract in every shipped pack
    must compile cleanly.  Was previously blocked by the missing
    sto-routing for ``universal.yaml`` (6/6) and ``openclaw.yaml`` (8
    sto entries).  Now both pass.

    This is the test that should fail loudly if anyone breaks the
    library-content guarantee — a pack we ship to users must never
    half-load.
    """
    cfg = load_config(pack_path)
    failures = []
    for agent_id, ac in cfg.agents.items():
        for i, ce in enumerate(ac.contracts, 1):
            try:
                _compile_field(ce.enforcement)
                if ce.assumption is not None:
                    _compile_field(ce.assumption)
            except Exception as e:  # noqa: BLE001 — re-raised below
                failures.append(
                    f"  agent={agent_id} #{i} desc={ce.desc!r} "
                    f"-> {type(e).__name__}: {e}"
                )
    assert not failures, (
        f"{pack_path.name}: {len(failures)} contract(s) failed to compile:\n"
        + "\n".join(failures)
    )

"""Tests for sponsio/discovery/store.py."""

import tempfile
from pathlib import Path

from sponsio.discovery._types import (
    ConstraintStatus,
    DiscoverySource,
    ProposedConstraint,
)
from sponsio.discovery.store import (
    PatternEntry,
    PatternStore,
    _extract_args_from_formula,
)
from sponsio.patterns.library import (
    arg_length_limit,
    arg_value_range,
    bounded_retry,
    cooldown,
    deadline,
    loop_detection,
    max_length,
    must_precede,
    rate_limit,
    required_steps_completion,
    token_budget,
)


def _make_entry(**kwargs) -> PatternEntry:
    defaults = {
        "id": "test-id",
        "pattern_name": "must_precede",
        "args": ("A", "B"),
        "kwargs": {"desc": "A must precede B"},
        "source": DiscoverySource.USER_DEFINED,
        "status": ConstraintStatus.VERIFIED,
        "confidence": 1.0,
        "provenance": "test",
        "nl_description": "A before B",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    defaults.update(kwargs)
    return PatternEntry(**defaults)


class TestPatternStoreCRUD:
    def test_add_and_get(self):
        store = PatternStore()
        entry = _make_entry()
        store.add(entry)
        assert store.get("test-id") == entry

    def test_remove(self):
        store = PatternStore()
        store.add(_make_entry())
        store.remove("test-id")
        assert len(store.list_all()) == 0

    def test_update_status(self):
        store = PatternStore()
        store.add(_make_entry(status=ConstraintStatus.PROPOSED))
        store.update_status("test-id", ConstraintStatus.VERIFIED)
        assert store.get("test-id").status == ConstraintStatus.VERIFIED


class TestPatternStoreQueries:
    def test_list_by_source(self):
        store = PatternStore()
        store.add(_make_entry(id="1", source=DiscoverySource.BUILTIN))
        store.add(_make_entry(id="2", source=DiscoverySource.USER_DEFINED))
        store.add(_make_entry(id="3", source=DiscoverySource.BUILTIN))
        assert len(store.list_by_source(DiscoverySource.BUILTIN)) == 2

    def test_list_by_status(self):
        store = PatternStore()
        store.add(_make_entry(id="1", status=ConstraintStatus.PROPOSED))
        store.add(_make_entry(id="2", status=ConstraintStatus.VERIFIED))
        assert len(store.list_by_status(ConstraintStatus.PROPOSED)) == 1

    def test_get_verified(self):
        store = PatternStore()
        store.add(_make_entry(id="1", status=ConstraintStatus.VERIFIED))
        store.add(_make_entry(id="2", status=ConstraintStatus.PROPOSED))
        verified = store.get_verified()
        assert len(verified) == 1


class TestPatternStoreWorkflow:
    def test_accept(self):
        store = PatternStore()
        store.add(_make_entry(status=ConstraintStatus.PROPOSED))
        store.accept("test-id")
        assert store.get("test-id").status == ConstraintStatus.VERIFIED

    def test_reject(self):
        store = PatternStore()
        store.add(_make_entry(status=ConstraintStatus.PROPOSED))
        store.reject("test-id", reason="not useful")
        entry = store.get("test-id")
        assert entry.status == ConstraintStatus.REJECTED
        assert entry.reject_reason == "not useful"

    def test_import_proposed(self):
        store = PatternStore()
        proposals = [
            ProposedConstraint(
                formula=must_precede("A", "B"),
                confidence=0.95,
                nl_description="A before B",
            ),
            ProposedConstraint(
                formula=rate_limit("X", 3),
                confidence=0.8,
                nl_description="X at most 3",
            ),
        ]
        ids = store.import_proposed(proposals)
        assert len(ids) == 2
        assert len(store.list_all()) == 2

    def test_import_skips_invalid(self):
        store = PatternStore()
        proposals = [
            ProposedConstraint(
                formula=must_precede("A", "B"),
                validation_errors=["bad formula"],
            ),
        ]
        ids = store.import_proposed(proposals)
        assert len(ids) == 0


class TestPatternStorePersistence:
    def test_save_and_load(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = Path(f.name)

        store = PatternStore()
        store.add(_make_entry(id="1"))
        store.add(_make_entry(id="2", pattern_name="rate_limit", args=("X", 3)))
        store.save(path)

        loaded = PatternStore.load(path)
        assert len(loaded.list_all()) == 2
        assert loaded.get("1").pattern_name == "must_precede"

        path.unlink()

    def test_entry_to_dict_roundtrip(self):
        entry = _make_entry()
        data = entry.to_dict()
        restored = PatternEntry.from_dict(data)
        assert restored.id == entry.id
        assert restored.pattern_name == entry.pattern_name
        assert restored.args == entry.args


class TestPatternStoreBuiltins:
    def test_with_builtins_loads_seeded_families(self):
        store = PatternStore.with_builtins()
        entries = store.list_all()
        # One row per active pattern family; deprecated ``never_together`` is not seeded
        assert len(entries) == 13

    def test_builtins_all_verified(self):
        store = PatternStore.with_builtins()
        for entry in store.list_all():
            assert entry.status == ConstraintStatus.VERIFIED
            assert entry.source == DiscoverySource.BUILTIN

    def test_builtins_can_reconstruct_formulas(self):
        store = PatternStore.with_builtins()
        verified = store.get_verified()
        assert len(verified) == 13
        for f in verified:
            assert f.pattern_name != ""


class TestNumericArgPreservation:
    """Issue #13: ``_extract_args_from_formula`` used to walk the formula
    tree and only recover ``called()`` tool names, silently dropping
    numeric thresholds (``rate_limit`` count, ``deadline`` steps,
    ``bounded_retry`` max, …). A round-trip through the store therefore
    *degraded* the rule — e.g. ``rate_limit("api", 5)`` came back as
    ``rate_limit("api")``, breaking the factory or picking the wrong
    default.

    Factories now stamp their invocation args onto the ``DetFormula``
    directly, so the store sees exact values. The formula-tree walk
    remains as a fallback for hand-constructed formulas.
    """

    def test_rate_limit_preserves_count(self):
        f = rate_limit("api", 5)
        assert _extract_args_from_formula(f) == ("api", 5)

    def test_deadline_preserves_step_count(self):
        f = deadline("alert", "respond", 3)
        assert _extract_args_from_formula(f) == ("alert", "respond", 3)

    def test_bounded_retry_preserves_max(self):
        f = bounded_retry("api", 7)
        assert _extract_args_from_formula(f) == ("api", 7)

    def test_cooldown_preserves_steps(self):
        f = cooldown("api", 10)
        assert _extract_args_from_formula(f) == ("api", 10)

    def test_loop_detection_preserves_consecutive_limit(self):
        f = loop_detection("poll", 3)
        assert _extract_args_from_formula(f) == ("poll", 3)

    def test_arg_length_limit_preserves_max_chars(self):
        f = arg_length_limit("bash", "command", 4096)
        assert _extract_args_from_formula(f) == ("bash", "command", 4096)

    def test_arg_value_range_preserves_bounds(self):
        f = arg_value_range("rm", "count", min_val=0, max_val=100)
        assert _extract_args_from_formula(f) == ("rm", "count", 0, 100)

    def test_token_budget_preserves_scope(self):
        f = token_budget(10_000, scope="input_tokens")
        assert _extract_args_from_formula(f) == (10_000, "input_tokens")

    def test_required_steps_preserves_ordered_list(self):
        """List ordering matters — tree-walk would lose it."""
        f = required_steps_completion("open_case", ["triage", "assign", "close"])
        args = _extract_args_from_formula(f)
        assert args == ("open_case", ("triage", "assign", "close"))

    def test_max_length_preserves_both_limits(self):
        f = max_length(max_words=100, max_chars=500)
        assert _extract_args_from_formula(f) == (100, 500)

    def test_must_precede_still_works(self):
        """Regression: string-only patterns continue to work unchanged."""
        f = must_precede("verify", "pay")
        assert _extract_args_from_formula(f) == ("verify", "pay")

    def test_store_roundtrip_preserves_numeric_args(self):
        """End-to-end: build a rate_limit(5), shove it through PatternStore,
        read it back. Pre-fix the count was lost and args degenerated to
        just ``("api",)``.
        """
        import json
        import os

        store = PatternStore()
        proposals = [
            ProposedConstraint(
                formula=rate_limit("api", 5),
                confidence=1.0,
                nl_description="api at most 5",
            )
        ]
        store.import_proposed(proposals)
        entries = store.list_all()
        assert len(entries) == 1
        assert entries[0].args == ("api", 5), (
            "numeric threshold must survive the store import path"
        )

        # And through a disk round-trip for good measure.
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as fh:
            path = Path(fh.name)
        try:
            store.save(path)
            raw = json.loads(path.read_text())
            # JSON has no tuple type — args serialize as list.
            assert raw["entries"][0]["args"] == ["api", 5]

            reloaded = PatternStore.load(path)
            assert reloaded.list_all()[0].args == ("api", 5)
        finally:
            os.unlink(path)

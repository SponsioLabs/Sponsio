"""Tests for sponsio/discovery/store.py."""

import tempfile
from pathlib import Path

from sponsio.discovery._types import (
    ConstraintStatus,
    DiscoverySource,
    ProposedConstraint,
)
from sponsio.discovery.store import PatternEntry, PatternStore
from sponsio.patterns.library import must_precede, rate_limit


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
    def test_with_builtins_loads_14(self):
        store = PatternStore.with_builtins()
        entries = store.list_all()
        assert len(entries) == 14

    def test_builtins_all_verified(self):
        store = PatternStore.with_builtins()
        for entry in store.list_all():
            assert entry.status == ConstraintStatus.VERIFIED
            assert entry.source == DiscoverySource.BUILTIN

    def test_builtins_can_reconstruct_formulas(self):
        store = PatternStore.with_builtins()
        verified = store.get_verified()
        assert len(verified) == 14
        for f in verified:
            assert f.pattern_name != ""

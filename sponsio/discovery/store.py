"""PatternStore — categorized, JSON-backed storage for discovered patterns."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sponsio.discovery._types import (
    ConstraintStatus,
    DiscoverySource,
    ProposedConstraint,
)
from sponsio.patterns.library import DetFormula


@dataclass
class PatternEntry:
    """A pattern in the store with source and status metadata."""

    id: str
    pattern_name: str
    args: tuple
    kwargs: dict
    source: DiscoverySource
    status: ConstraintStatus
    confidence: float
    provenance: str
    nl_description: str
    created_at: str
    updated_at: str
    evidence: dict = field(default_factory=dict)
    reject_reason: str = ""

    def to_formula(self) -> DetFormula:
        """Reconstruct the DetFormula from stored pattern_name + args."""
        from sponsio.discovery.store import _get_full_registry

        registry = _get_full_registry()
        fn = registry[self.pattern_name]
        return fn(*self.args, **self.kwargs)

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        return {
            "id": self.id,
            "pattern_name": self.pattern_name,
            "args": list(self.args),
            "kwargs": self.kwargs,
            "source": self.source.value,
            "status": self.status.value,
            "confidence": self.confidence,
            "provenance": self.provenance,
            "nl_description": self.nl_description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "evidence": self.evidence,
            "reject_reason": self.reject_reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PatternEntry:
        """Deserialize from a dict."""
        return cls(
            id=data["id"],
            pattern_name=data["pattern_name"],
            args=tuple(data["args"]),
            kwargs=data.get("kwargs", {}),
            source=DiscoverySource(data["source"]),
            status=ConstraintStatus(data["status"]),
            confidence=data.get("confidence", 1.0),
            provenance=data.get("provenance", ""),
            nl_description=data.get("nl_description", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            evidence=data.get("evidence", {}),
            reject_reason=data.get("reject_reason", ""),
        )


def _get_full_registry() -> dict:
    """Get the full pattern registry with all 14 patterns."""
    from sponsio.patterns.library import (
        always_followed_by,
        bounded_retry,
        cooldown,
        deadline,
        idempotent,
        must_confirm,
        must_precede,
        mutual_exclusion,
        never_together,
        no_data_leak,
        no_reversal,
        rate_limit,
        requires_permission,
        segregation_of_duty,
    )

    return {
        "must_precede": must_precede,
        "always_followed_by": always_followed_by,
        "never_together": never_together,
        "no_reversal": no_reversal,
        "requires_permission": requires_permission,
        "no_data_leak": no_data_leak,
        "mutual_exclusion": mutual_exclusion,
        "rate_limit": rate_limit,
        "idempotent": idempotent,
        "deadline": deadline,
        "must_confirm": must_confirm,
        "cooldown": cooldown,
        "segregation_of_duty": segregation_of_duty,
        "bounded_retry": bounded_retry,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_DEFAULT_STORE_DIR = Path.home() / ".sponsio"
_DEFAULT_STORE_FILE = _DEFAULT_STORE_DIR / "patterns.json"


class PatternStore:
    """Categorized store for all patterns (builtin + user + auto-extracted).

    Thread-safe. Supports JSON persistence.

    Default storage location: ``~/.sponsio/patterns.json``
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._entries: dict[str, PatternEntry] = {}
        self._path = Path(path) if path else None
        self._lock = threading.Lock()

    @classmethod
    def default(cls) -> PatternStore:
        """Load or create the default store at ``~/.sponsio/patterns.json``.

        If the file exists, loads it. Otherwise creates a new store
        pre-populated with the 14 builtin patterns and saves it.
        """
        if _DEFAULT_STORE_FILE.exists():
            return cls.load(_DEFAULT_STORE_FILE)
        store = cls.with_builtins(path=_DEFAULT_STORE_FILE)
        _DEFAULT_STORE_DIR.mkdir(parents=True, exist_ok=True)
        store.save()
        return store

    # -----------------------------------------------------------------
    # CRUD
    # -----------------------------------------------------------------

    def _auto_save(self) -> None:
        """Save to disk if a path is configured."""
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": 1,
                "entries": [e.to_dict() for e in self._entries.values()],
            }
            self._path.write_text(json.dumps(data, indent=2))

    def add(self, entry: PatternEntry) -> str:
        """Add a pattern entry. Returns its id."""
        with self._lock:
            self._entries[entry.id] = entry
            self._auto_save()
            return entry.id

    def get(self, entry_id: str) -> PatternEntry:
        """Get a pattern entry by id. Raises KeyError if not found."""
        return self._entries[entry_id]

    def remove(self, entry_id: str) -> None:
        """Remove a single pattern entry by id. Builtin patterns cannot be removed."""
        with self._lock:
            entry = self._entries[entry_id]
            if entry.source == DiscoverySource.BUILTIN:
                raise ValueError(f"Cannot remove builtin pattern: {entry.pattern_name}")
            del self._entries[entry_id]
            self._auto_save()

    def clear(self, source: Optional[DiscoverySource] = None) -> int:
        """Remove patterns by source. Builtin patterns are never removed.

        Args:
            source: If provided, only remove entries from this source.
                If None, remove all non-builtin entries.

        Examples::

            store.clear(DiscoverySource.AUTO_EXTRACTED)  # remove proposed/mined only
            store.clear(DiscoverySource.USER_DEFINED)    # remove user rules only
            store.clear()                                # remove all except builtin
        """
        with self._lock:
            if source == DiscoverySource.BUILTIN:
                return 0  # builtin patterns are protected
            if source is None:
                to_remove = [
                    eid
                    for eid, e in self._entries.items()
                    if e.source != DiscoverySource.BUILTIN
                ]
            else:
                to_remove = [
                    eid for eid, e in self._entries.items() if e.source == source
                ]
            for eid in to_remove:
                del self._entries[eid]
            self._auto_save()
        return len(to_remove)

    def clear_rejected(self) -> int:
        """Remove all rejected entries. Returns the number removed."""
        with self._lock:
            to_remove = [
                eid
                for eid, e in self._entries.items()
                if e.status == ConstraintStatus.REJECTED
            ]
            for eid in to_remove:
                del self._entries[eid]
            self._auto_save()
        return len(to_remove)

    def update_status(self, entry_id: str, status: ConstraintStatus) -> None:
        """Update the status of a pattern entry."""
        with self._lock:
            entry = self._entries[entry_id]
            entry.status = status
            entry.updated_at = _now_iso()
            self._auto_save()

    # -----------------------------------------------------------------
    # Queries
    # -----------------------------------------------------------------

    def list_all(self) -> list[PatternEntry]:
        """Return all entries."""
        return list(self._entries.values())

    def list_by_source(self, source: DiscoverySource) -> list[PatternEntry]:
        """Return entries matching a source."""
        return [e for e in self._entries.values() if e.source == source]

    def list_by_status(self, status: ConstraintStatus) -> list[PatternEntry]:
        """Return entries matching a status."""
        return [e for e in self._entries.values() if e.status == status]

    def summary(self) -> str:
        """Human-readable summary of the library, grouped by source."""
        lines: list[str] = []

        source_order = [
            DiscoverySource.BUILTIN,
            DiscoverySource.USER_DEFINED,
            DiscoverySource.AUTO_EXTRACTED,
        ]
        source_labels = {
            DiscoverySource.BUILTIN: "Builtin Patterns",
            DiscoverySource.USER_DEFINED: "User-Defined",
            DiscoverySource.AUTO_EXTRACTED: "Auto-Extracted",
        }

        total = len(self._entries)
        verified = sum(
            1 for e in self._entries.values() if e.status == ConstraintStatus.VERIFIED
        )
        proposed = sum(
            1 for e in self._entries.values() if e.status == ConstraintStatus.PROPOSED
        )
        lines.append(
            f"Pattern Library: {total} patterns ({verified} verified, {proposed} proposed)"
        )
        lines.append("")

        for source in source_order:
            entries = [e for e in self._entries.values() if e.source == source]
            if not entries:
                continue

            lines.append(f"  {source_labels[source]} ({len(entries)})")
            for e in entries:
                status_icon = {
                    ConstraintStatus.VERIFIED: "+",
                    ConstraintStatus.PROPOSED: "?",
                    ConstraintStatus.REJECTED: "x",
                }.get(e.status, " ")
                conf = f" ({e.confidence:.0%})" if e.confidence < 1.0 else ""
                lines.append(
                    f"    [{status_icon}] {e.pattern_name}: {e.nl_description}{conf}"
                )
            lines.append("")

        return "\n".join(lines)

    def get_verified(self) -> list[DetFormula]:
        """Return all verified patterns as DetFormula objects."""
        return [
            e.to_formula()
            for e in self._entries.values()
            if e.status == ConstraintStatus.VERIFIED
        ]

    # -----------------------------------------------------------------
    # Bulk operations / workflow
    # -----------------------------------------------------------------

    def import_proposed(self, constraints: list[ProposedConstraint]) -> list[str]:
        """Import proposed constraints from an extractor. Returns ids."""
        ids = []
        now = _now_iso()
        with self._lock:
            for c in constraints:
                if not c.ok:
                    continue
                entry_id = str(uuid.uuid4())
                entry = PatternEntry(
                    id=entry_id,
                    pattern_name=c.formula.pattern_name,
                    args=_extract_args_from_formula(c.formula),
                    kwargs={"desc": c.formula.desc},
                    source=c.source,
                    status=c.status,
                    confidence=c.confidence,
                    provenance=c.provenance,
                    nl_description=c.nl_description or c.formula.desc,
                    created_at=now,
                    updated_at=now,
                    evidence=c.evidence,
                )
                self._entries[entry_id] = entry
                ids.append(entry_id)
            self._auto_save()
        return ids

    def import_user_defined(
        self, formulas: list[DetFormula], provenance: str = "user"
    ) -> list[str]:
        """Import user-written constraints as verified entries. Returns ids."""
        ids = []
        now = _now_iso()
        with self._lock:
            for f in formulas:
                entry_id = str(uuid.uuid4())
                entry = PatternEntry(
                    id=entry_id,
                    pattern_name=f.pattern_name,
                    args=_extract_args_from_formula(f),
                    kwargs={"desc": f.desc},
                    source=DiscoverySource.USER_DEFINED,
                    status=ConstraintStatus.VERIFIED,
                    confidence=1.0,
                    provenance=provenance,
                    nl_description=f.desc,
                    created_at=now,
                    updated_at=now,
                )
                self._entries[entry_id] = entry
                ids.append(entry_id)
            self._auto_save()
        return ids

    def accept(self, entry_id: str) -> None:
        """Accept a proposed constraint (proposed -> verified)."""
        self.update_status(entry_id, ConstraintStatus.VERIFIED)

    def reject(self, entry_id: str, reason: str = "") -> None:
        """Reject a proposed constraint."""
        with self._lock:
            entry = self._entries[entry_id]
            entry.status = ConstraintStatus.REJECTED
            entry.reject_reason = reason
            entry.updated_at = _now_iso()
            self._auto_save()

    # -----------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------

    def save(self, path: Optional[Path] = None) -> None:
        """Save the store to a JSON file."""
        target = Path(path) if path else self._path
        if target is None:
            raise ValueError("No path specified for save")
        data = {
            "version": 1,
            "entries": [e.to_dict() for e in self._entries.values()],
        }
        target.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path) -> PatternStore:
        """Load a store from a JSON file."""
        data = json.loads(Path(path).read_text())
        store = cls(path=path)
        for entry_data in data.get("entries", []):
            entry = PatternEntry.from_dict(entry_data)
            store._entries[entry.id] = entry
        return store

    # -----------------------------------------------------------------
    # Bootstrap
    # -----------------------------------------------------------------

    @classmethod
    def with_builtins(cls, path: Optional[Path] = None) -> PatternStore:
        """Create a store pre-populated with all 14 builtin patterns."""
        store = cls(path=path)
        now = _now_iso()
        registry = _get_full_registry()

        # Sample args for each builtin pattern to generate a formula
        _BUILTIN_SAMPLES = {
            "must_precede": ("A", "B"),
            "always_followed_by": ("A", "B"),
            "never_together": ("A", "B"),
            "no_reversal": ("A", "B"),
            "requires_permission": ("tool", "perm"),
            "no_data_leak": ("src", "ext"),
            "mutual_exclusion": ("A", "B"),
            "rate_limit": ("action", 1),
            "idempotent": ("action",),
            "deadline": ("trigger", "action", 3),
            "must_confirm": ("action",),
            "cooldown": ("action", 2),
            "segregation_of_duty": ("A", "B"),
            "bounded_retry": ("action", 3),
        }

        for name, sample_args in _BUILTIN_SAMPLES.items():
            fn = registry[name]
            formula = fn(*sample_args)
            entry_id = str(uuid.uuid4())
            store._entries[entry_id] = PatternEntry(
                id=entry_id,
                pattern_name=name,
                args=sample_args,
                kwargs={"desc": formula.desc},
                source=DiscoverySource.BUILTIN,
                status=ConstraintStatus.VERIFIED,
                confidence=1.0,
                provenance="builtin",
                nl_description=formula.desc,
                created_at=now,
                updated_at=now,
            )

        return store


def _extract_args_from_formula(formula: DetFormula) -> tuple:
    """Best-effort extraction of pattern args from an DetFormula.

    Falls back to empty tuple if the formula's internal structure
    cannot be introspected.
    """
    from sponsio.formulas.formula import collect_atoms

    atoms = collect_atoms(formula.formula)
    # Extract tool names from called() atoms
    tools = []
    for a in atoms:
        if a.predicate == "called" and a.args:
            tools.append(a.args[0])
    return tuple(sorted(set(tools))) if tools else ()

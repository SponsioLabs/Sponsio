"""Run the boundary corpus against the current tree.

Tier 1 runs in normal CI. Tier 2 and Tier 3 run only when
``SPONSIO_TEST_TIER`` is ``2`` or ``3``, in the CI jobs of those names.
Tier 1 and Tier 3 cases get the throwaway :class:`~tests.security.corpus.Sandbox`;
Tier 2 cases get a :class:`~tests.security.system_sandbox.SystemSandbox`
on the machine's system install. A case whose closing phase is not in
``corpus.CLOSED_PHASES`` is a strict xfail: when the fix lands the
test flips and the phase has to be recorded.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.security.corpus import (
    CLOSED_PHASES,
    UNASSERTED,
    VECTORS,
    Vector,
    make_sandbox,
)
from tests.security.system_sandbox import open_system_sandbox

REPO = Path(__file__).resolve().parents[2]


def _requested_tier() -> int | None:
    raw = os.environ.get("SPONSIO_TEST_TIER", "").strip()
    return int(raw) if raw in {"2", "3"} else None


def _param(vector: Vector, tier: int):
    case = vector.case(tier)
    marks = []
    if case.run is not None and case.closes_in and case.closes_in not in CLOSED_PHASES:
        marks.append(
            pytest.mark.xfail(strict=True, reason=f"closes in {case.closes_in}")
        )
    return pytest.param(vector, id=vector.id, marks=marks)


@pytest.fixture
def sandbox(monkeypatch, sponsio_from_this_checkout):
    sb = make_sandbox(monkeypatch)
    yield sb
    sb.close()


@pytest.fixture
def system_sandbox(request):
    sb = open_system_sandbox(request.node.callspec.params["vector"].id)
    yield sb
    sb.close()


# Which sandbox fixture each tier's cases run in.
_SANDBOX_FIXTURE = {1: "sandbox", 2: "system_sandbox", 3: "sandbox"}


def _run(vector: Vector, tier: int, request) -> None:
    case = vector.case(tier)
    if case.expect in UNASSERTED:
        pytest.skip(f"{vector.id} is {case.expect} at tier {tier}: not asserted")
    if case.run is None:
        pytest.skip(f"{vector.id} has no tier {tier} runner yet")
    # Requested only now, so a case with nothing to run never needs the
    # system install.
    case.run(request.getfixturevalue(_SANDBOX_FIXTURE[tier]))


@pytest.mark.parametrize("vector", [_param(v, 1) for v in VECTORS])
def test_tier1(vector: Vector, request):
    _run(vector, 1, request)


@pytest.mark.parametrize("vector", [_param(v, 2) for v in VECTORS])
def test_tier2(vector: Vector, request):
    if _requested_tier() != 2:
        pytest.skip("Tier 2 expectations run only with SPONSIO_TEST_TIER=2")
    _run(vector, 2, request)


@pytest.mark.parametrize("vector", [_param(v, 3) for v in VECTORS])
def test_tier3(vector: Vector, request):
    if _requested_tier() != 3:
        pytest.skip("Tier 3 expectations run only with SPONSIO_TEST_TIER=3")
    _run(vector, 3, request)


# ---------------------------------------------------------------------------
# Table integrity
# ---------------------------------------------------------------------------


def test_vector_ids_are_unique():
    ids = [v.id for v in VECTORS]
    assert len(ids) == len(set(ids))


def test_tier1_cases_are_asserted_or_documented():
    """Every Tier 1 expectation is either runnable or explicitly one
    of the unasserted kinds; none is silently missing a harness."""
    for v in VECTORS:
        case = v.tier1
        if case.expect in UNASSERTED:
            assert case.run is None
        else:
            assert case.run is not None, f"{v.id} has no Tier 1 runner"


def test_closed_phases_are_known():
    phases = {c.closes_in for v in VECTORS for c in (v.tier1, v.tier2, v.tier3)}
    phases.discard(None)
    unknown = CLOSED_PHASES - phases - {"P0.1"}
    assert not unknown, f"CLOSED_PHASES names phases no vector refers to: {unknown}"


# Distinctive fragments of the reproduction recipes. They may appear
# in tests/security/ only.
_RECIPE_FRAGMENTS = (
    "'/.spon'+'sio",
    "'~/.cla'+'ude",
    "cd ~/.spons*",
)

_SCAN_ROOTS = (
    "README.md",
    "CHANGELOG.md",
    "QUICKSTART.md",
    "CONTRIBUTING.md",
    "llms-full.txt",
    "docs",
    "sponsio",
    "plugins",
    "scripts",
    "ts/packages/sdk/src",
    ".github",
)
_SCAN_SUFFIXES = {".md", ".txt", ".py", ".ts", ".yaml", ".yml", ".json", ".mjs"}


def test_recipes_stay_in_security_fixtures():
    hits = []
    for root in _SCAN_ROOTS:
        path = REPO / root
        if not path.exists():
            continue
        files = [path] if path.is_file() else path.rglob("*")
        for f in files:
            if not f.is_file() or f.suffix not in _SCAN_SUFFIXES:
                continue
            if "node_modules" in f.parts or "__pycache__" in f.parts:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for frag in _RECIPE_FRAGMENTS:
                if frag in text:
                    hits.append((str(f.relative_to(REPO)), frag))
    assert not hits, f"reproduction recipes leaked outside tests/security/: {hits}"

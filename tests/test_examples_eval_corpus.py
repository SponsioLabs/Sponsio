"""Smoke test: the example eval corpus always produces the report
shape advertised in ``examples/eval/README.md``.

If someone edits ``generate_corpus.py``, the bundled
``sponsio.yaml`` contracts, or the eval runner internals, this test
catches the drift before it confuses a new user who copy-pastes the
README and gets different numbers.

Also verifies that ``generate_corpus.py`` is deterministic — running
it twice produces byte-identical files.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = REPO_ROOT / "examples" / "eval"


pytest.importorskip("yaml")  # config loader needs PyYAML


def _load_corpus_module():
    spec = importlib.util.spec_from_file_location(
        "_eval_corpus_gen", EVAL_DIR / "generate_corpus.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_generator_is_deterministic(tmp_path):
    """Running ``generate_corpus.py`` twice in a row must produce
    byte-identical files.  Without this, a CI-time regenerate would
    create spurious diffs and erode trust in the bundled corpus."""
    module = _load_corpus_module()

    # Redirect output to tmp_path
    module.OUT_DIR = tmp_path / "traces"
    module.main()
    first = {p.name: p.read_bytes() for p in module.OUT_DIR.iterdir()}

    # Wipe + regenerate
    for p in module.OUT_DIR.iterdir():
        p.unlink()
    module.main()
    second = {p.name: p.read_bytes() for p in module.OUT_DIR.iterdir()}

    assert first == second


def test_bundled_traces_match_generator():
    """The committed JSONs under ``traces/`` must match what
    ``generate_corpus.py`` produces — otherwise a contributor edited
    one without running the generator."""
    module = _load_corpus_module()

    bundled = {
        p.name: p.read_bytes()
        for p in (EVAL_DIR / "traces").iterdir()
        if p.suffix == ".json"
    }

    # Generate fresh into a sibling tempdir without touching the repo
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        module.OUT_DIR = Path(td)
        module.main()
        regenerated = {p.name: p.read_bytes() for p in module.OUT_DIR.iterdir()}

    assert bundled == regenerated, (
        "examples/eval/traces is stale — re-run "
        "`python examples/eval/generate_corpus.py` and commit"
    )


def test_eval_report_matches_readme():
    """End-to-end: running the eval against the bundled corpus +
    config must produce exactly the per-contract counts shown in
    the README.  This is the test that fails loudest if either the
    corpus or the contracts drift."""
    from sponsio.config import load_config
    from sponsio.eval_runner import discover_cases, run_eval

    cfg = load_config(EVAL_DIR / "sponsio.yaml")
    contracts: list = []
    for ce in cfg.agents["customer_bot"].contracts:
        # Each contract has only an enforcement field here.
        if isinstance(ce.enforcement, list):
            contracts.extend(ce.enforcement)
        else:
            contracts.append(ce.enforcement)

    cases = discover_cases(EVAL_DIR / "traces")
    assert len(cases) == 6
    report = run_eval(cases, contracts)

    assert report.n_safe == 3
    assert report.n_unsafe == 3
    assert report.n_unlabelled == 0

    # Per-contract counts, locked in to match the README:
    by_contract = {m.contract_nl: m for m in report.contracts}
    must_precede = next(m for nl, m in by_contract.items() if "must precede" in nl)
    rate_limit = next(m for nl, m in by_contract.items() if "at most" in nl)
    # must_precede catches 2 of the 3 unsafe traces (rate-limit case
    # has a verify so this contract passes it)
    assert (must_precede.tp, must_precede.fp, must_precede.fn, must_precede.tn) == (
        2,
        0,
        1,
        3,
    )
    # rate_limit catches 1 of the 3 unsafe traces (only the 3-refund one)
    assert (rate_limit.tp, rate_limit.fp, rate_limit.fn, rate_limit.tn) == (1, 0, 2, 3)

    # Overall: the two contracts together cover everything → 0 misses, 0 overblocks.
    assert (
        report.overall_tp,
        report.overall_fp,
        report.overall_fn,
        report.overall_tn,
    ) == (3, 0, 0, 3)
    assert report.overall_fpr == 0.0
    assert report.overall_fnr == 0.0


def test_each_trace_is_valid_otlp():
    """Defensive: every bundled JSON must round-trip through the
    OTel consumer.  Catches an accidentally-corrupted JSON edit."""
    from sponsio.tracer.otel_consumer import otel_to_trace

    for p in (EVAL_DIR / "traces").glob("*.json"):
        data = json.loads(p.read_text())
        trace = otel_to_trace(data)
        assert trace.events, f"{p.name} produced an empty trace"

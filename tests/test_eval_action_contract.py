"""Contract test between ``actions/eval/action.yml`` and ``sponsio eval --json``.

The GitHub Action's composite YAML contains an inline Python block
that parses the eval report and extracts ``fpr``/``fnr``/``*-delta``
into step outputs.  That parsing silently depends on Sponsio's JSON
schema — rename ``overall_fpr_delta`` in ``eval_runner.py`` and the
action starts emitting empty outputs without any loud failure.

This test is the canary.  It:
1. Produces a real ``sponsio eval --json`` report (with baseline).
2. Loads ``actions/eval/action.yml``.
3. Re-executes the inline Python parse block with the real report
   as input and asserts the step outputs contain the expected fields.

Lightweight intentionally — we don't spin up a GH Actions runner,
just replicate the contract the action relies on.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("yaml")
import yaml

from sponsio.eval_runner import discover_cases, run_eval

ACTION_YML = Path(__file__).parent.parent / "actions" / "eval" / "action.yml"


def _write_min_corpus(tmp: Path) -> Path:
    """Tiny but real OTLP corpus: one safe, one unsafe."""
    from sponsio.models.trace import Event, Trace
    from sponsio.tracer.otel_writer import trace_to_otlp

    traces = tmp / "traces"
    traces.mkdir()

    safe = Trace(
        events=[
            Event(ts=0, agent="bot", event_type="tool_call", tool="verify"),
            Event(ts=1, agent="bot", event_type="tool_call", tool="refund"),
        ]
    )
    (traces / "safe_ok.json").write_text(
        json.dumps(trace_to_otlp(safe, agent_id="bot"))
    )

    unsafe = Trace(
        events=[
            Event(ts=0, agent="bot", event_type="tool_call", tool="refund"),
        ]
    )
    (traces / "unsafe_bad.json").write_text(
        json.dumps(trace_to_otlp(unsafe, agent_id="bot"))
    )

    return traces


def _extract_parse_block(action_yml: dict) -> str:
    """Pull the ``python <<'PY' ... PY`` heredoc body out of the
    ``Parse eval report`` step.  Returns the Python source verbatim
    so we can run it against real data.
    """
    steps = action_yml["runs"]["steps"]
    parse_step = next(s for s in steps if s.get("id") == "parse")
    run = parse_step["run"]
    m = re.search(r"python <<'PY'\n(.*?)\nPY", run, re.DOTALL)
    if not m:
        pytest.fail("Couldn't locate the Python heredoc in Parse eval report step.")
    return m.group(1)


def test_action_parse_block_handles_real_eval_output(tmp_path: Path):
    """End-to-end: generate a real report, run the action's parse
    block against it, confirm the expected outputs appear.

    This would catch: renamed JSON keys, dropped ``overall`` block,
    nested/flat schema swap, division-by-zero in ``overall``."""
    traces = _write_min_corpus(tmp_path)
    contracts = ["tool `verify` must precede `refund`"]

    # Baseline report (identical to current = zero deltas)
    cases = discover_cases(traces)
    report = run_eval(cases, contracts)
    flat = report.to_dict()
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(flat))

    # Current report with baseline diff — mirrors the nested shape
    # the CLI produces when ``--baseline`` is passed.
    from sponsio.eval_runner import diff_reports

    diff = diff_reports(json.loads(baseline_path.read_text()), report)
    nested = {"report": flat, "baseline_diff": diff.to_dict()}
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(nested))

    # Run the action's parse block in a subprocess so its writes to
    # $GITHUB_OUTPUT don't pollute our test process env.
    action_yml = yaml.safe_load(ACTION_YML.read_text())
    parse_src = _extract_parse_block(action_yml)

    github_output = tmp_path / "gh_output"
    github_output.touch()
    env = {
        **os.environ,
        "SPONSIO_REPORT_PATH": str(report_path),
        "SPONSIO_WITH_BASELINE": str(baseline_path),
        "GITHUB_OUTPUT": str(github_output),
    }
    res = subprocess.run(
        [sys.executable, "-c", parse_src],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Parse block crashed:\n{res.stderr}"

    # Confirm all four expected outputs were written.  We don't
    # assert on the exact values (those depend on the grounding
    # implementation and may legitimately move); we only assert on
    # presence + that at least one line is non-empty, which is what
    # the action's downstream steps rely on.
    lines = dict(
        line.split("=", 1)
        for line in github_output.read_text().strip().splitlines()
        if "=" in line
    )
    for key in ("fpr", "fnr", "fpr-delta", "fnr-delta"):
        assert key in lines, f"action parse block didn't emit ``{key}=``"
    # At least one non-empty metric (otherwise the report parse is
    # silently broken).
    assert any(v.strip() for v in lines.values()), (
        "All parsed outputs empty — JSON schema mismatch between "
        "sponsio eval --json and actions/eval/action.yml parse block."
    )

    # And a sticky-comment body was written.
    comment_body = Path("/tmp/sponsio-eval-comment.md")
    assert comment_body.exists(), "Parse block should always emit comment body"
    text = comment_body.read_text()
    assert "Sponsio eval gate" in text
    assert "| FPR |" in text and "| FNR |" in text


def test_action_inputs_documented_in_readme():
    """Keep ``actions/eval/README.md`` in sync with ``action.yml``.

    If someone adds an input but forgets to document it, users get
    a surprise flag.  If someone renames an input and forgets to
    update the README, users copy-paste a broken example.  Both
    fail this test loudly.
    """
    action_yml = yaml.safe_load(ACTION_YML.read_text())
    readme = (ACTION_YML.parent / "README.md").read_text()

    declared = set(action_yml.get("inputs", {}).keys())
    for name in declared:
        # Allow either ``| `name` |`` in the table or ``name:`` in a
        # code block — both are natural doc styles.
        assert f"`{name}`" in readme, (
            f"Input ``{name}`` is declared in action.yml but not "
            f"mentioned anywhere in README.md.  Add it to the "
            f"inputs table before shipping."
        )

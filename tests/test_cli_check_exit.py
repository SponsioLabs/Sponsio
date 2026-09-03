"""`sponsio check` is a CI gate, so its exit code has to mean something.

`docs/reference/cli.md` states that every command exits 0 on success and
1 on failure, and lists a contract violation as failure. This command
printed "✗ 1/1 contract(s) VIOLATED" and exited 0, so a job wired the way
the docs describe was green whatever the trace contained.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from sponsio.cli import check


def _trace(tmp_path, tools):
    """An OTLP file with one span per tool, in order."""
    spans = [
        {
            "traceId": "0" * 32,
            "spanId": f"{i:016x}",
            "name": name,
            "startTimeUnixNano": str(1_000_000_000 * (i + 1)),
            "endTimeUnixNano": str(1_000_000_000 * (i + 1) + 1000),
            "attributes": [{"key": "tool.name", "value": {"stringValue": name}}],
        }
        for i, name in enumerate(tools)
    ]
    path = tmp_path / "trace.json"
    path.write_text(
        json.dumps(
            {
                "resourceSpans": [
                    {
                        "resource": {
                            "attributes": [
                                {
                                    "key": "service.name",
                                    "value": {"stringValue": "bot"},
                                }
                            ]
                        },
                        "scopeSpans": [{"scope": {"name": "t"}, "spans": spans}],
                    }
                ]
            }
        )
    )
    return str(path)


def _config(tmp_path):
    path = tmp_path / "sponsio.yaml"
    path.write_text(
        'version: "1"\nagents:\n  bot:\n    contracts:\n'
        '      - G: "tool `check_policy` must precede `issue_refund`"\n'
    )
    return str(path)


def test_a_violation_exits_nonzero(tmp_path):
    """`issue_refund` before `check_policy` breaks the ordering."""
    result = CliRunner().invoke(
        check,
        [
            "--trace",
            _trace(tmp_path, ["issue_refund", "check_policy"]),
            "--config",
            _config(tmp_path),
            "--agent",
            "bot",
        ],
    )
    assert "VIOLATED" in result.output
    assert result.exit_code == 1


def test_a_clean_trace_exits_zero(tmp_path):
    result = CliRunner().invoke(
        check,
        [
            "--trace",
            _trace(tmp_path, ["check_policy", "issue_refund"]),
            "--config",
            _config(tmp_path),
            "--agent",
            "bot",
        ],
    )
    assert result.exit_code == 0, result.output


def test_the_report_does_not_call_no_record_zero():
    """An enforce run keeps no session log by default, so a zero here
    means "not recorded", not "did not happen". A run that blocked four
    calls reported "Actually blocked (enforce mode): 0"."""
    from sponsio.reporting.aggregator import Report
    from sponsio.reporting.renderer import render_markdown

    out = render_markdown(Report())
    assert "not recorded" in out
    assert "**Actually blocked (enforce mode):** 0" not in out


def test_a_real_block_count_is_still_shown():
    from sponsio.reporting.aggregator import Report
    from sponsio.reporting.renderer import render_markdown

    out = render_markdown(Report(blocked=4))
    assert "**Actually blocked (enforce mode):** 4" in out

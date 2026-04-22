"""CLI tests for ``sponsio bench``.

Uses Click's ``CliRunner`` (in-process) so the test is fast and
doesn't spawn a Python subprocess per case.  Parse ``--json`` via ``result.stdout`` — Click 8.2+ keeps stderr
separate; ``result.output`` interleaves both and breaks ``json.loads``.

What we cover:

  * happy-path single-agent config prints a table
  * ``--json`` emits parseable JSON with the documented keys
  * custom ``--actions`` forces a specific tool rotation
  * unknown agent IDs error out with exit code 2
  * missing config file errors out before touching the guard
  * multi-agent config without ``--agent`` is rejected
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from sponsio.cli import cli


# ---------------------------------------------------------------------------
# Fixture: minimal single-agent config
# ---------------------------------------------------------------------------


SINGLE_AGENT_YAML = """
version: "1"
tools:
  - name: search_web
  - name: send_email
agents:
  bench:
    contracts:
      - E:
          pattern: tool_allowlist
          args:
            - [search_web]
"""


MULTI_AGENT_YAML = """
version: "1"
agents:
  alpha:
    contracts:
      - E:
          pattern: tool_allowlist
          args: [[x]]
  beta:
    contracts:
      - E:
          pattern: tool_allowlist
          args: [[y]]
"""


@pytest.fixture()
def config_path(tmp_path):
    p = tmp_path / "sponsio.yaml"
    p.write_text(SINGLE_AGENT_YAML)
    return p


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_bench_default_prints_table(config_path):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "bench",
            str(config_path),
            "--iterations",
            "500",
            "--warmup",
            "50",
            "--actions",
            "search_web",
        ],
    )
    assert result.exit_code == 0, result.output
    # Table rendering markers — if any of these move, update
    # downstream docs at the same time.
    assert "sponsio bench" in result.output
    assert "wall:" in result.output
    assert "pure DFA" in result.output
    assert "QPS" in result.output


def test_bench_json_has_required_keys(config_path):
    """--json output is a stable contract: the CI perf-diff script
    reads these exact keys and will break if they rename."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "bench",
            str(config_path),
            "-n",
            "500",
            "--warmup",
            "50",
            "--actions",
            "search_web",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.strip())
    for key in (
        "total_checks",
        "n_pure_det",
        "zero_llm_ratio",
        "pure_det",
        "sto_cached",
        "sto_live",
        "per_contract",
        "iterations",
        "warmup",
        "wall_clock_s",
        "effective_qps",
        "tools",
        "agent_id",
    ):
        assert key in payload, f"missing key {key!r}"
    assert payload["iterations"] == 500
    assert payload["warmup"] == 50
    assert payload["tools"] == ["search_web"]
    assert payload["agent_id"] == "bench"


def test_bench_warmup_discarded_from_summary(config_path):
    """After ``--warmup N``, the reported bucket count should
    reflect the post-warmup iterations, not warmup + iterations."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "bench",
            str(config_path),
            "-n",
            "200",
            "--warmup",
            "50",
            "--actions",
            "search_web",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.strip())
    # Warmup is excluded: 200 iters recorded (not 250).
    assert payload["total_checks"] == 200


def test_bench_rotates_through_provided_actions(config_path, tmp_path):
    """--actions list must be what drives the rotation; the
    tools: inventory is secondary.  We verify by using a tool
    name that DOESN'T appear in tools: — it should still work."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "bench",
            str(config_path),
            "-n",
            "100",
            "--warmup",
            "10",
            "--actions",
            "custom_tool_not_in_config",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["tools"] == ["custom_tool_not_in_config"]


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_bench_missing_config_errors_early(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["bench", str(tmp_path / "nope.yaml")],
    )
    # Click type=click.Path(exists=True) rejects before the callback
    # runs, so exit code is 2 (usage error).
    assert result.exit_code == 2
    assert "nope.yaml" in result.output or "does not exist" in result.output


def test_bench_multi_agent_without_flag_errors(tmp_path):
    """A multi-agent config without ``--agent`` is ambiguous — we
    refuse rather than silently benching a random one."""
    p = tmp_path / "multi.yaml"
    p.write_text(MULTI_AGENT_YAML)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["bench", str(p), "-n", "10", "--warmup", "0", "--actions", "x"],
    )
    assert result.exit_code == 2
    assert "multiple agents" in result.output


def test_bench_unknown_agent_errors(tmp_path):
    p = tmp_path / "multi.yaml"
    p.write_text(MULTI_AGENT_YAML)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "bench",
            str(p),
            "--agent",
            "ghost",
            "-n",
            "10",
            "--warmup",
            "0",
            "--actions",
            "x",
        ],
    )
    assert result.exit_code == 2
    assert "ghost" in result.output


def test_bench_empty_actions_errors(config_path):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "bench",
            str(config_path),
            "-n",
            "10",
            "--warmup",
            "0",
            "--actions",
            ",, ,",  # empty after strip
        ],
    )
    assert result.exit_code == 2
    assert "empty" in result.output


def test_bench_no_actions_falls_back_to_extracted_tools(config_path):
    """Without --actions we should rotate through the ``tools:``
    inventory.  The config declares two tools, so the bench should
    pick both up."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "bench",
            str(config_path),
            "-n",
            "30",
            "--warmup",
            "0",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.strip())
    assert set(payload["tools"]) == {"search_web", "send_email"}

"""Tests for ``sponsio explain`` rendering + lookup helpers.

Resolution semantics (alias / exact-desc / substring), session-log
violation lookup, and the renderer's zone / color contract. The CLI
command itself is exercised at the smoke level via the explain helper
so we don't have to spin up an actual Click test runner — the
end-to-end smoke ran clean against `/tmp/test_explain.yaml` during
development.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from rich.console import Console

from sponsio.render.explain import (
    explain_to_dict,
    find_last_violation,
    render_explain,
    resolve_contract,
)


# ---------------------------------------------------------------------------
# Test fixtures.
# ---------------------------------------------------------------------------


@dataclass
class _Constraint:
    """Stand-in for a DetFormula wrapper."""

    pattern_name: str | None = None
    args: tuple = ()
    formula: object | None = None  # not introspected by these tests


@dataclass
class _Contract:
    desc: str | None = None
    assumption: object | None = None
    guarantee: object | None = None
    alpha: float = 1.0
    beta: float = 1.0
    activate_at: str | None = None
    agent: object | None = None


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def _render(contract, idx, **kwargs) -> tuple[str, str]:
    console = Console(
        record=True, width=100, force_terminal=True, color_system="truecolor"
    )
    render_explain(console=console, contract=contract, index=idx, **kwargs)
    ansi = console.export_text(styles=True)
    return ansi, _strip_ansi(ansi)


# ---------------------------------------------------------------------------
# resolve_contract — alias / exact / substring lookup precedence.
# ---------------------------------------------------------------------------


def test_resolve_by_C_alias_one_indexed():
    cs = [_Contract(desc="a"), _Contract(desc="b"), _Contract(desc="c")]
    contract, idx = resolve_contract("C2", cs)
    assert idx == 1
    assert contract is cs[1]


def test_resolve_by_lowercase_c_alias():
    cs = [_Contract(desc="a"), _Contract(desc="b")]
    contract, idx = resolve_contract("c1", cs)
    assert idx == 0
    assert contract is cs[0]


def test_resolve_by_alias_out_of_range_returns_none():
    cs = [_Contract(desc="a")]
    assert resolve_contract("C99", cs) == (None, None)


def test_resolve_by_exact_desc_case_insensitive():
    cs = [_Contract(desc="Code Freeze: no SQL"), _Contract(desc="other")]
    contract, idx = resolve_contract("code freeze: no sql", cs)
    assert idx == 0
    assert contract is cs[0]


def test_resolve_by_substring_when_no_exact_match():
    cs = [
        _Contract(desc="rate limit: refunds at most 1"),
        _Contract(desc="code freeze: no destructive SQL"),
    ]
    contract, idx = resolve_contract("code freeze", cs)
    assert idx == 1
    assert contract is cs[1]


def test_resolve_returns_first_substring_match():
    """When multiple descs contain the substring, the first one wins —
    consistent with humans reading top-to-bottom."""
    cs = [
        _Contract(desc="rate limit one"),
        _Contract(desc="rate limit two"),
    ]
    _, idx = resolve_contract("rate", cs)
    assert idx == 0


def test_resolve_no_match_returns_pair_of_nones():
    cs = [_Contract(desc="a"), _Contract(desc="b")]
    assert resolve_contract("totally absent", cs) == (None, None)


def test_resolve_empty_contract_list():
    assert resolve_contract("C1", []) == (None, None)


# ---------------------------------------------------------------------------
# find_last_violation — session-log scanning.
# ---------------------------------------------------------------------------


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_find_last_violation_returns_most_recent_match(tmp_path):
    sessions = tmp_path / "sessions"
    _write_jsonl(
        sessions / "agent1" / "20260501_120000_111.jsonl",
        [
            {
                "ts": 100.0,
                "agent_id": "agent1",
                "action": "execute_sql",
                "constraint": "rule_X",
                "result": {"action": "blocked", "message": "first hit"},
            },
            {
                "ts": 300.0,  # newest
                "agent_id": "agent1",
                "action": "execute_sql",
                "constraint": "rule_X",
                "result": {"action": "blocked", "message": "third hit"},
            },
            {
                "ts": 200.0,  # in between
                "agent_id": "agent1",
                "action": "execute_sql",
                "constraint": "rule_X",
                "result": {"action": "blocked", "message": "second hit"},
            },
        ],
    )
    found = find_last_violation("rule_X", sessions)
    assert found is not None
    assert found["ts"] == 300.0
    assert found["result"]["message"] == "third hit"


def test_find_last_violation_filters_to_target_constraint(tmp_path):
    sessions = tmp_path / "sessions"
    _write_jsonl(
        sessions / "agent1" / "log.jsonl",
        [
            {
                "ts": 100.0,
                "constraint": "other_rule",
                "result": {"action": "blocked"},
            },
            {
                "ts": 50.0,
                "constraint": "rule_X",
                "result": {"action": "blocked", "message": "hit"},
            },
        ],
    )
    found = find_last_violation("rule_X", sessions)
    assert found["result"]["message"] == "hit"


def test_find_last_violation_skips_passed_actions(tmp_path):
    sessions = tmp_path / "sessions"
    _write_jsonl(
        sessions / "agent1" / "log.jsonl",
        [
            {
                "ts": 100.0,
                "constraint": "rule_X",
                "result": {"action": "allowed"},  # not a violation action
            }
        ],
    )
    assert find_last_violation("rule_X", sessions) is None


def test_find_last_violation_includes_observed_and_retrying(tmp_path):
    sessions = tmp_path / "sessions"
    _write_jsonl(
        sessions / "agent1" / "log.jsonl",
        [
            {
                "ts": 100.0,
                "constraint": "rule_X",
                "result": {"action": "observed", "message": "shadow hit"},
            }
        ],
    )
    found = find_last_violation("rule_X", sessions)
    assert found["result"]["action"] == "observed"


def test_find_last_violation_returns_none_when_dir_missing(tmp_path):
    assert find_last_violation("rule_X", tmp_path / "nope") is None


def test_find_last_violation_skips_malformed_lines(tmp_path):
    sessions = tmp_path / "sessions"
    path = sessions / "agent1" / "log.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"ts":1,"constraint":"rule","result":{"action":"blocked"}}\n'
        "this is not json\n"
        '{"ts":2,"constraint":"rule","result":{"action":"blocked","message":"newer"}}\n',
        encoding="utf-8",
    )
    found = find_last_violation("rule", sessions)
    assert found["result"]["message"] == "newer"


# ---------------------------------------------------------------------------
# render_explain — zone + color contract.
# ---------------------------------------------------------------------------


def test_render_emits_all_zones():
    contract = _Contract(
        desc="rate limit: at most 1",
        guarantee=_Constraint(pattern_name="rate_limit", args=("issue_refund", 1)),
    )
    _, plain = _render(contract, 0)
    assert "Sponsio" in plain
    assert "explain C1" in plain  # banner reflects alias
    assert "C1" in plain  # title row
    assert "contract" in plain
    assert "compiled (LTL)" in plain
    assert "recent activity" in plain
    assert "how to resolve" in plain
    assert "→" in plain  # CTA arrow


def test_render_shows_pattern_summary_when_available():
    contract = _Contract(
        desc="rate limit",
        guarantee=_Constraint(pattern_name="rate_limit", args=("X", 50)),
    )
    _, plain = _render(contract, 0)
    assert "rate_limit('X', 50)" in plain


def test_render_unconditional_label_when_no_assumption():
    contract = _Contract(
        desc="bare", guarantee=_Constraint(pattern_name="rate_limit", args=())
    )
    _, plain = _render(contract, 0)
    assert "unconditional" in plain


def test_render_assumption_pattern_summary_when_present():
    contract = _Contract(
        desc="conditional",
        assumption=_Constraint(pattern_name="some_assume", args=()),
        guarantee=_Constraint(pattern_name="rate_limit", args=()),
    )
    _, plain = _render(contract, 0)
    assert "some_assume" in plain


def test_render_no_violation_section_text_when_clean():
    contract = _Contract(desc="x", guarantee=_Constraint(pattern_name="rate_limit"))
    _, plain = _render(contract, 0, last_violation=None)
    assert "no recorded violations" in plain


def test_render_violation_section_when_present():
    contract = _Contract(desc="x", guarantee=_Constraint(pattern_name="rate_limit"))
    last = {
        "ts": 1730000000.0,
        "agent_id": "support_bot",
        "action": "issue_refund",
        "constraint": "x",
        "result": {"action": "blocked", "message": "limit exceeded"},
    }
    ansi, plain = _render(contract, 0, last_violation=last)
    assert "BLOCKED" in plain
    assert "support_bot" in plain
    assert "issue_refund" in plain
    assert "limit exceeded" in plain
    # PALETTE['violation'] = #FCA5A5 → 38;2;252;165;165
    assert "38;2;252;165;165" in ansi


def test_render_resolution_hint_specialises_per_pattern():
    """Each pattern kind should produce a specific first hint."""
    rate = _Contract(desc="x", guarantee=_Constraint(pattern_name="rate_limit"))
    blacklist = _Contract(desc="x", guarantee=_Constraint(pattern_name="arg_blacklist"))
    precede = _Contract(desc="x", guarantee=_Constraint(pattern_name="must_precede"))
    _, p_rate = _render(rate, 0)
    _, p_black = _render(blacklist, 0)
    _, p_prec = _render(precede, 0)
    assert "space out calls" in p_rate
    assert "forbidden pattern" in p_black
    assert "prerequisite tool" in p_prec


def test_render_includes_config_path_hint_when_provided(tmp_path):
    """The 'source: <path>' line lets users open the yaml directly."""
    contract = _Contract(desc="x", guarantee=_Constraint(pattern_name="rate_limit"))
    cfg = tmp_path / "sponsio.yaml"
    cfg.write_text("contracts: []\n", encoding="utf-8")
    _, plain = _render(contract, 0, config_path=cfg)
    # Rich wraps long paths across lines; collapse whitespace before checking.
    flat = re.sub(r"\s+", "", plain)
    assert re.sub(r"\s+", "", str(cfg)) in flat


# ---------------------------------------------------------------------------
# explain_to_dict — JSON shape.
# ---------------------------------------------------------------------------


def test_explain_to_dict_basic_shape():
    contract = _Contract(
        desc="rate limit",
        guarantee=_Constraint(pattern_name="rate_limit", args=("X", 1)),
        alpha=1.0,
        beta=1.0,
    )
    out = explain_to_dict(contract, 0)
    assert out["alias"] == "C1"
    assert out["desc"] == "rate limit"
    assert out["guarantee"]["pattern"] == "rate_limit('X', 1)"
    assert out["assumption"]["pattern"] is None
    assert out["last_violation"] is None
    assert isinstance(out["resolution_hints"], list)
    assert len(out["resolution_hints"]) >= 1


def test_explain_to_dict_includes_last_violation_when_provided():
    contract = _Contract(desc="x", guarantee=_Constraint(pattern_name="rate_limit"))
    last = {"ts": 1.0, "result": {"action": "blocked"}}
    out = explain_to_dict(contract, 0, last_violation=last)
    assert out["last_violation"] == last


def test_explain_to_dict_serializable():
    """The dict must round-trip through json.dumps for --format=json."""
    contract = _Contract(
        desc="rate limit",
        guarantee=_Constraint(pattern_name="rate_limit", args=("X", 1)),
    )
    out = explain_to_dict(contract, 0)
    serialized = json.dumps(out, default=str)
    assert "rate_limit" in serialized

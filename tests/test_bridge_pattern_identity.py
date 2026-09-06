"""A violation leaves the machine as a pattern, not only as a sentence.

Contract rows already carry ``pattern`` and ``args`` (added for the
copilot's mining dedupe). Violations did not: everything downstream that
wants to group runs by rule, rank by severity or say what to change had
to parse English to recover what the SDK already knew.
"""

from __future__ import annotations

import sponsio
from sponsio.bridge.session import _contracts_from_guard
from sponsio.bridge.spans import violations_from_turn


def test_contract_rows_carry_the_pattern_and_its_arguments(capsys):
    guard = sponsio.Sponsio(
        agent_id="a",
        contracts=[
            "tool `check_policy` must precede `issue_refund`",
            "tool `run_bash` at most 5 times",
        ],
        verbose=False,
    )
    capsys.readouterr()
    rows = {r["pattern"]: r for r in _contracts_from_guard(guard)}
    assert rows["must_precede"]["args"] == ["check_policy", "issue_refund"]
    assert rows["rate_limit"]["args"] == ["run_bash", 5]


def test_tuple_arguments_become_lists_so_they_survive_json():
    """scope_limit and friends carry a tuple of paths; a tuple is not JSON.

    Built from the pattern library directly rather than through the DSL,
    because the property is about the projection, not about parsing."""
    from types import SimpleNamespace

    from sponsio.patterns.library import scope_limit

    formula = scope_limit("read_file", ["/srv/app", "/srv/data"])
    guard = SimpleNamespace(
        mode="enforce",
        _system=SimpleNamespace(
            _contracts=[SimpleNamespace(desc=None, mode=None, guarantees=[formula])]
        ),
    )
    (row,) = _contracts_from_guard(guard)
    assert row["pattern"] == "scope_limit"
    assert row["args"] == ["read_file", ["/srv/app", "/srv/data"]]
    for arg in row["args"]:
        assert not isinstance(arg, tuple)


def test_a_violation_carries_the_pattern_of_the_rule_it_broke():
    label = "tool `check_policy` must precede `issue_refund`"
    by_label = {
        label: {
            "id": "c1",
            "label": label,
            "pattern": "must_precede",
            "args": ["check_policy", "issue_refund"],
        }
    }
    turn = {
        "children": [
            {
                "span_type": "sponsio.contract_check",
                "contract_name": label,
                "children": [
                    {"span_type": "sponsio.guarantee", "formula_desc": label},
                    {"span_type": "sponsio.violation", "kind": "guarantee"},
                    {"span_type": "sponsio.enforcement", "result_action": "blocked"},
                ],
            }
        ]
    }
    (entry,) = violations_from_turn(turn, by_label)
    assert entry["pattern"] == "must_precede"
    assert entry["args"] == ["check_policy", "issue_refund"]

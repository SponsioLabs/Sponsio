"""Every spelling of "A is forbidden after B" forbids A after B.

The DSL swaps ``no_reversal``'s arguments for phrasings that name the
contradiction first. Three common spellings never matched the swap
trigger, so the rule compiled to its mirror image: it permitted exactly
the order it names and blocked the harmless one, while the console
reported the rule ARMED. No test covered those spellings, which is how
the bug survived.

Each case is checked against the runtime, not against the formula
string: what matters is which order a guard actually stops.
"""

from __future__ import annotations

import pytest

import sponsio

A, B = "export_phi", "share_record"

# Every spelling means: once B has happened, A is forbidden.
SPELLINGS = [
    f"never `{A}` after `{B}`",
    f"cannot `{A}` after `{B}`",
    f"must not `{A}` after `{B}`",
    f"`{A}` is forbidden after `{B}`",
    f"`{A}` is not allowed after `{B}`",
    f"tool `{A}` must not follow `{B}`",
    f"no {A} after {B}",
    f"cannot {A} after {B}",
    f"once `{B}` is called, `{A}` must not be called",
]


def _stops(rule: str, script: list[str]) -> list[bool]:
    guard = sponsio.Sponsio(agent_id="t", contracts=[rule], mode="enforce", verbose=False)
    return [not guard.guard_before(tool, {}).allowed for tool in script]


@pytest.mark.parametrize("rule", SPELLINGS)
def test_the_forbidden_order_is_stopped(rule, capsys):
    assert _stops(rule, [B, A])[-1] is True, f"{rule!r} let {A} run after {B}"
    capsys.readouterr()


@pytest.mark.parametrize("rule", SPELLINGS)
def test_the_harmless_order_is_allowed(rule, capsys):
    assert _stops(rule, [A, B])[-1] is False, f"{rule!r} blocked {B} after {A}"
    capsys.readouterr()

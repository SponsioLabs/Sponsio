"""The phrasing this module's own docstring advertises has to work.

``dsl_to_contract``'s docstring lists::

    never call `A` and `B` together    -> mutual_exclusion

and it matched nothing. Every entry in the keyword table expects the
negation next to "together"; this shape puts both actions in between, so
the rule parsed as no pattern at all and a rulebook containing the
documented spelling refused to arm.

Found by ``scripts/fuzz_cross_language.py``, which generates rules from
templates and noticed that one in eighteen was unparseable in both
runtimes. A hand-written corpus would not have: the phrasing was in the
docs and in nobody's tests.
"""

from __future__ import annotations

import pytest

from sponsio.generation.dsl_to_contract import parse_dsl

A, B = "read_database", "send_email"

SPELLINGS = [
    f"never call `{A}` and `{B}` together",
    f"never calling `{A}` and `{B}` together",
    f"never `{A}` and `{B}` together",
    f"tools `{A}` and `{B}` are mutually exclusive",
    f"`{A}` and `{B}` must never be called together",
]


@pytest.mark.parametrize("rule", SPELLINGS)
def test_every_spelling_reaches_mutual_exclusion(rule):
    parsed = parse_dsl(rule)
    assert parsed.error in (None, ""), f"{rule!r} did not parse: {parsed.error}"
    assert parsed.pattern_name == "mutual_exclusion"
    assert set(parsed.args) == {A, B}


def test_the_documented_spelling_is_one_of_them():
    """The docstring is the contract a reader reads first."""
    from sponsio.generation import dsl_to_contract

    assert "never call `A` and `B` together" in dsl_to_contract.__doc__


def test_it_did_not_steal_the_reversal_spellings():
    """ "never `A` after `B`" shares its first word and means something
    else entirely, and the mutual-exclusion rule is checked first."""
    parsed = parse_dsl(f"never `{A}` after `{B}`")
    assert parsed.pattern_name == "no_reversal"
    # The commitment first, which is the order the pattern wants.
    assert parsed.args == (B, A)

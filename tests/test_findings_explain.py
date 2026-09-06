"""Every deterministic pattern can say what it means in plain language.

A platform embedding Sponsio shows these sentences to people who did not
write the rule and will never read a formula. The table is the product
surface for that audience, so the test that matters is coverage: a new
pattern landing in the library without an entry here would appear in a
customer's dashboard as raw pattern-name soup.
"""

from __future__ import annotations

import re
from pathlib import Path

from sponsio.findings.explain import RULES, explain

LIBRARY = Path(__file__).resolve().parent.parent / "sponsio" / "patterns" / "library.py"


def library_patterns() -> set[str]:
    return set(re.findall(r'pattern_name="([a-z_0-9]+)"', LIBRARY.read_text()))


def test_every_library_pattern_has_an_explanation():
    missing = sorted(library_patterns() - set(RULES))
    assert not missing, f"patterns with no plain-language entry: {missing}"


# Patterns explained ahead of the library commit that adds them. Each entry
# here names work that is on its way; empty this set when it lands. An
# entry that stays here past its branch is a sentence nobody will see.
FORTHCOMING = {
    # fix/enforcement-and-evidence-mw: SMT claim checking
    "claim_requires_smt_valid",
    "smt_premises_must_be_consistent",
}


def test_the_table_invents_no_patterns():
    """An entry for a pattern that no longer exists is a sentence nobody
    will ever see, and it will quietly rot."""
    extra = sorted(set(RULES) - library_patterns() - FORTHCOMING)
    assert not extra, f"explanations for patterns that no longer exist: {extra}"


def test_forthcoming_entries_are_still_forthcoming():
    """Once the library has the pattern, the allowance above is stale."""
    landed = sorted(FORTHCOMING & library_patterns())
    assert not landed, f"drop these from FORTHCOMING, the library has them now: {landed}"


def test_an_unknown_pattern_still_explains_itself():
    """A pattern from outside the library, a customer's own, must not
    vanish from the dashboard just because this table has not caught up."""
    e = explain("some_future_pattern", ("a", "b"), rule_text="a must not follow b")
    assert e.known is False
    assert e.business
    assert e.title


def test_enforcement_changes_what_the_sentence_claims():
    """The same rule blocking a call and watching one go through are
    different events, and a business reader must be able to tell."""
    blocked = explain("rate_limit", ("issue_refund", 2), action="blocked")
    observed = explain("rate_limit", ("issue_refund", 2), action="observed")
    assert "stopped the call" in blocked.business
    assert "still ran" in observed.business
    assert blocked.severity != observed.severity


def test_every_entry_renders_without_arguments():
    """Args arrive from a trace and a trace can be short of them. A
    KeyError here would take down whatever endpoint was rendering the
    finding, for every finding, not just the malformed one."""
    for name in RULES:
        e = explain(name, ())
        assert e.title and e.business and e.fix


def test_every_entry_renders_with_plausible_arguments():
    for name in RULES:
        e = explain(name, ("alpha_tool", "beta_tool", 3, 9), action="blocked")
        assert "{" not in e.business and "{" not in e.fix
        assert e.severity in ("low", "medium", "high", "critical")

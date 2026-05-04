"""``sponsio scan`` should drop unparseable contracts so the saved YAML
is directly usable.  We hit the helper directly with a synthetic mix of
good + bad entries (skipping the analyzer) since that's the contract
being validated."""

from __future__ import annotations

import textwrap

import pytest

yaml_module = pytest.importorskip("yaml")

from sponsio.cli import _drop_contract_indices, _filter_invalid_contracts  # noqa: E402


def test_filter_drops_unparseable_nl_keeps_valid() -> None:
    src = textwrap.dedent(
        """\
        version: "1"

        agents:
          agent:
            contracts:
              - G: "tool `check_policy` must precede `issue_refund`"
              - G: "this is utter nonsense and matches no pattern at all whatsoever"
              - G: "tool `issue_refund` at most 3 times"
        """
    )

    cleaned, dropped = _filter_invalid_contracts(src)

    assert len(dropped) == 1
    assert "nonsense" in dropped[0]["nl"]
    assert dropped[0]["agent"] == "agent"

    parsed = yaml_module.safe_load(cleaned)
    contracts = parsed["agents"]["agent"]["contracts"]
    assert len(contracts) == 2
    assert all("nonsense" not in str(c) for c in contracts)
    assert any("check_policy" in str(c) for c in contracts)
    assert any("issue_refund" in str(c) for c in contracts)


def test_filter_collapses_empty_contracts_to_empty_list() -> None:
    """When every contract is dropped the file must still parse: the
    ``contracts:`` line should become ``contracts: []`` rather than an
    orphan key with no value."""
    src = textwrap.dedent(
        """\
        version: "1"

        agents:
          agent:
            contracts:
              - G: "complete gibberish here"
              - G: "more gibberish over there"
        """
    )

    cleaned, dropped = _filter_invalid_contracts(src)

    assert len(dropped) == 2
    parsed = yaml_module.safe_load(cleaned)
    assert parsed["agents"]["agent"]["contracts"] == []


def test_filter_handles_structured_entries() -> None:
    """Structured ``pattern: ...`` entries should also be validated and
    dropped if the pattern doesn't compile."""
    src = textwrap.dedent(
        """\
        version: "1"

        agents:
          agent:
            contracts:
              - G:
                  pattern: must_precede
                  args: [check_policy, issue_refund]
              - G:
                  pattern: this_pattern_does_not_exist
                  args: [foo, bar]
        """
    )

    cleaned, dropped = _filter_invalid_contracts(src)

    assert len(dropped) == 1
    assert "this_pattern_does_not_exist" in dropped[0]["nl"]

    parsed = yaml_module.safe_load(cleaned)
    contracts = parsed["agents"]["agent"]["contracts"]
    assert len(contracts) == 1
    assert contracts[0]["G"]["pattern"] == "must_precede"


def test_filter_preserves_pair_a_e_entries() -> None:
    """Conditional (A, G) entries should be kept as a unit when both
    parts compile, and dropped as a unit when either part fails."""
    src = textwrap.dedent(
        """\
        version: "1"

        agents:
          agent:
            contracts:
              - A: "called `modify_order`"
                G: "tool `get_order_details` must precede `modify_order`"
              - A: "totally meaningless precondition string"
                G: "tool `a` must precede `b`"
        """
    )

    cleaned, dropped = _filter_invalid_contracts(src)

    assert len(dropped) == 1
    parsed = yaml_module.safe_load(cleaned)
    contracts = parsed["agents"]["agent"]["contracts"]
    assert len(contracts) == 1
    assert "modify_order" in contracts[0]["A"]


def test_drop_indices_keeps_comments_and_confidence_tags() -> None:
    """Inline confidence comments on kept entries should survive."""
    src = textwrap.dedent(
        """\
        version: "1"

        agents:
          agent:
            contracts:
              - G: "tool `a` must precede `b`"  # confidence: 0.85
              - G: "totally bogus"
              - G: "tool `c` must precede `d`"  # confidence: 0.40 — review recommended
        """
    )

    cleaned = _drop_contract_indices(src, {"agent": {1}})

    assert "confidence: 0.85" in cleaned
    assert "review recommended" in cleaned
    assert "totally bogus" not in cleaned

"""Per-contract ``mode:`` overrides the global one.

``docs/reference/config-yaml.md`` has always documented this ("Global
default; per-contract ``mode:`` overrides") but the field was parsed away
and never reached the engine, so a rulebook could only be flipped
wholesale. That gap is why a per-rule decision was something a config could
express and the runtime could not read.

The tests below pin both directions, because only one of them is obvious:
tightening a single rule inside an observing book is the feature people ask
for, and loosening a single rule inside an enforcing book is how you ship a
rule you are not yet sure about without turning the whole book off.
"""

from __future__ import annotations

import pytest

import sponsio
from sponsio.config import ConfigError, load_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """The suite sets SPONSIO_MODE=enforce by default, and the env var
    outranks the yaml. These tests are about what the yaml says."""
    monkeypatch.delenv("SPONSIO_MODE", raising=False)


BLOCKED_CALL = ("bash", {"command": "rm -rf /data"})
OTHER_CALL = ("curl", {"url": "http://evil.example"})


def _two_rules(global_mode: str, first_rule_mode: str | None) -> str:
    """Two rules; only the first may carry its own mode.

    Built line by line rather than with a dedented f-string: an interpolated
    line at the wrong indent silently changes the yaml structure, and a test
    that quietly tests a different document than it claims is worse than no
    test.
    """
    lines = [
        "version: '1'",
        f"mode: {global_mode}",
        "agents:",
        "  a:",
        "    contracts:",
        "    - G:",
        "        pattern: arg_blacklist",
        "        args: [bash, command, ['rm -rf']]",
        "      desc: no destructive shell",
    ]
    if first_rule_mode:
        lines.append(f"      mode: {first_rule_mode}")
    lines += [
        "    - G:",
        "        pattern: arg_blacklist",
        "        args: [curl, url, ['evil']]",
        "      desc: no evil hosts",
    ]
    return "\n".join(lines) + "\n"


def _write(tmp_path, body: str):
    path = tmp_path / "sponsio.yaml"
    path.write_text(body)
    return path


def test_one_rule_can_enforce_inside_an_observing_book(tmp_path):
    """The feature people ask for: arm the rule you trust, keep watching
    the rest."""
    guard = sponsio.Sponsio(
        config=str(_write(tmp_path, _two_rules("observe", "enforce"))), agent_id="a"
    )

    assert guard.mode == "observe"
    assert guard.guard_before(*BLOCKED_CALL).allowed is False, (
        "the rule with `mode: enforce` should block even though the book observes"
    )
    assert guard.guard_before(*OTHER_CALL).allowed is True, (
        "a rule without its own mode must still follow the global mode"
    )


def test_one_rule_can_observe_inside_an_enforcing_book(tmp_path):
    """The other direction: ship a rule you are unsure about without
    turning the whole book off."""
    guard = sponsio.Sponsio(
        config=str(_write(tmp_path, _two_rules("enforce", "observe"))), agent_id="a"
    )

    assert guard.mode == "enforce"
    assert guard.guard_before(*BLOCKED_CALL).allowed is True, (
        "the rule with `mode: observe` should only record, not block"
    )
    assert guard.guard_before(*OTHER_CALL).allowed is False, (
        "a rule without its own mode must still follow the global mode"
    )


def test_no_mode_means_no_opinion(tmp_path):
    """Absent is not the same as ``observe``. Defaulting a silent rule to
    observe would silently downgrade every contract that simply did not
    mention a mode."""
    guard = sponsio.Sponsio(
        config=str(_write(tmp_path, _two_rules("enforce", None))), agent_id="a"
    )

    assert guard.guard_before(*BLOCKED_CALL).allowed is False
    assert guard.guard_before(*OTHER_CALL).allowed is False


def test_the_parsed_entry_carries_the_mode(tmp_path):
    cfg = load_config(str(_write(tmp_path, _two_rules("observe", "enforce"))))
    modes = [c.mode for c in cfg.agents["a"].contracts]
    assert modes == ["enforce", None]


def test_a_typo_in_the_mode_is_an_error_not_a_shrug(tmp_path):
    """Silently ignoring an unrecognised value is how this key spent its
    life documented and inert."""
    with pytest.raises(ConfigError, match="contract `mode` must be one of"):
        load_config(str(_write(tmp_path, _two_rules("observe", "enfrce"))))

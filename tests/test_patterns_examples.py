"""Every example ``sponsio patterns`` prints has to work.

A first-run user copied an example straight out of this command, fed it to
``sponsio validate``, and got a syntax error whose advice was to consult
``sponsio patterns`` — the source of the broken example. Eighteen of the
thirty-six printed lines did not parse.

Half of those were templates (``tool `X` at most N times``) and half were
patterns with no natural-language form at all. Both are now handled
honestly: templates became concrete sentences, and a pattern without a
sentence prints the yaml that does work instead of a sentence that does not.

This test is the thing that keeps it true.
"""

from __future__ import annotations

import re

import pytest
from click.testing import CliRunner

from sponsio.cli import cli
from sponsio.generation.dsl_to_contract import parse_contract

EXAMPLE_RE = re.compile(r"^\s*Example\s*:\s*(.+)$", re.MULTILINE)
YAML_RE = re.compile(r"^\s*Yaml\s*:\s*(.+)$", re.MULTILINE)


@pytest.fixture(scope="module")
def output() -> str:
    result = CliRunner().invoke(cli, ["patterns"])
    assert result.exit_code == 0, result.output
    return result.output


def _parses(sentence: str) -> bool:
    try:
        return bool(getattr(parse_contract(sentence), "ok", False))
    except Exception:  # noqa: BLE001 - a crash is a failure like any other
        return False


def test_the_command_still_prints_examples(output):
    assert len(EXAMPLE_RE.findall(output)) > 20, "guard against a vacuous pass"


def test_every_printed_example_parses(output):
    """The one that matters. A user copies these."""
    failures = [
        line for line in EXAMPLE_RE.findall(output) if not _parses(line.strip())
    ]
    assert not failures, "examples that do not parse:\n  " + "\n  ".join(failures)


def test_no_example_is_a_template(output):
    """`tool \\`X\\` at most N times` teaches the shape and then fails when
    you run it. A concrete sentence teaches the shape AND runs."""
    placeholders = [
        line
        for line in EXAMPLE_RE.findall(output)
        if re.search(r"`[A-Z]`|\bN\b|`action`|`trigger`|`perm`|`src`|`ext`", line)
    ]
    assert not placeholders, "examples still using placeholders:\n  " + "\n  ".join(
        placeholders
    )


def test_patterns_without_a_sentence_show_working_yaml(output):
    """A pattern the DSL cannot express yet still has to be usable."""
    yamls = YAML_RE.findall(output)
    assert yamls, "expected some patterns to advertise their yaml form"
    for line in yamls:
        assert line.strip().startswith("G: {pattern:"), line
        assert "args:" in line, line


def test_the_advertised_yaml_names_a_real_pattern(output):
    """A yaml example naming a factory that does not exist is worse than no
    example: it looks authoritative."""
    from sponsio.patterns import library

    for line in YAML_RE.findall(output):
        name = re.search(r"pattern:\s*([a-z_0-9]+)", line).group(1)
        assert hasattr(library, name), f"{name} is not in the pattern library"

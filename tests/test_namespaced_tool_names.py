"""Tests for the ``foo:bar`` disambiguation heuristic.

A colon in a tool name historically meant ``tool:argpattern``
(``bash:rm -rf`` ⇒ "Bash called with args matching ``rm -rf``").
Claude Code's plugin namespace convention also uses a colon
(``my-plugin:hello``) to mean a literal namespaced tool name. The
two collide.

The heuristic in :func:`sponsio.patterns.library._is_namespaced_tool_name`
treats the form as a literal tool name iff both halves look like
identifiers (``[A-Za-z_][\\w-]*``); any whitespace or regex
metacharacter on the RHS keeps the legacy pattern semantics.

The same predicate is duplicated in
:mod:`sponsio.tracer.grounding` (``_NAMESPACED_TOOL_RE``) — these
tests pin the contract so a future tweak to one location doesn't
silently desync from the other.
"""

from __future__ import annotations

import pytest

from sponsio.patterns.library import _is_namespaced_tool_name, _physical_tool


# ---------------------------------------------------------------------------
# Cases that MUST be treated as literal namespaced tool names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool",
    [
        # Claude Code namespaced skills
        "acme:fetch",
        "acme:fetch_data",
        "my-plugin:hello",
        "sponsio-shield:scan",
        # Hypothetical MCP-style with single colon (the ``mcp__`` form
        # has no colon at all, so it's irrelevant here, but shorter
        # variants might appear).
        "mcp:server",
        "github:create_issue",
        "filesystem:read_file",
    ],
)
def test_namespaced_identifiers_are_literal(tool):
    assert _is_namespaced_tool_name(tool) is True
    # _physical_tool passes them through unchanged (no split).
    assert _physical_tool(tool) == tool


# ---------------------------------------------------------------------------
# Cases that MUST stay as tool:argpattern (legacy semantics)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool,physical",
    [
        ("bash:rm -rf", "bash"),  # whitespace
        ("bash:sed -i", "bash"),  # whitespace
        ("bash:python -c", "bash"),  # whitespace
        ("bash:rm\\s+-rf", "bash"),  # regex backslash
        ("bash:^sudo", "bash"),  # regex ^
        ("bash:rm$", "bash"),  # regex $
        ("bash:rm.*", "bash"),  # regex *
        ("bash:rm|cp", "bash"),  # regex |
        ("bash:.*", "bash"),  # regex .
    ],
)
def test_pattern_forms_keep_split_semantics(tool, physical):
    assert _is_namespaced_tool_name(tool) is False
    assert _physical_tool(tool) == physical


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool",
    [
        "Bash",  # no colon at all — not "namespaced"
        "Edit",
        "mcp__server__tool",  # MCP convention, no colon
        "",
    ],
)
def test_no_colon_is_not_namespaced(tool):
    assert _is_namespaced_tool_name(tool) is False
    assert _physical_tool(tool) == tool


def test_grounding_heuristic_matches_library_heuristic():
    """The two duplicated predicates must accept and reject the same set."""
    from sponsio.tracer.grounding import _NAMESPACED_TOOL_RE

    cases = [
        ("acme:fetch", True),
        ("my-plugin:hello", True),
        ("bash:rm -rf", False),
        ("bash:^sudo", False),
        ("Bash", False),
        ("mcp__server__tool", False),
    ]
    for tool, expected in cases:
        from_lib = _is_namespaced_tool_name(tool)
        from_grounding = bool(_NAMESPACED_TOOL_RE.match(tool))
        assert from_lib == from_grounding == expected, (
            f"divergent for {tool!r}: lib={from_lib}, grounding={from_grounding}"
        )

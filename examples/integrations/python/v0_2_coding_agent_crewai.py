"""v0.2 case study: coding agent with proactive default-deny (CrewAI).

Scenario: a code-assistant agent that should only be able to read /
search / lint a repository. The user wants strong containment: even
if a prompt-injection convinces the model to call something dangerous,
the dangerous tool should not be on the menu at all.

v0.2 primitives shown:

1. ``tool_policy`` with ``default: deny`` + ``enforcement: proactive``
   strips denied tools from the CrewAI agent's bound tool set at
   ``wrap()`` time. The model literally never sees them in its
   tool selection prompt.
2. The reactive-mode fallback still catches anything that slips
   through (it shouldn't, but defense in depth).

Run::

    python examples/integrations/python/v0_2_coding_agent_crewai.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from sponsio.integrations.crewai import CrewAIGuard  # noqa: E402


# ---------------------------------------------------------------------------
# A representative coding-agent toolset. Half are safe (read-only,
# diagnostic); half are dangerous (write, delete, network egress).
# ---------------------------------------------------------------------------


def read_file(path: str) -> str:
    """Read the contents of a file."""
    return f"<contents of {path}>"


def search_code(pattern: str) -> str:
    """grep the repo for a pattern."""
    return f"<matches for {pattern!r}>"


def run_linter(path: str) -> str:
    """Run ruff on the file."""
    return f"<lint report for {path}>"


def write_file(path: str, contents: str) -> str:
    """Write contents to a file (DANGEROUS. overwrites)."""
    return f"wrote {len(contents)} bytes to {path}"


def shell_exec(cmd: str) -> str:
    """Run an arbitrary shell command (DANGEROUS)."""
    return f"<exec output of {cmd!r}>"


def network_post(url: str, body: str) -> str:
    """POST data to a URL (DANGEROUS. exfil risk)."""
    return f"POST {url} body={body!r}"


ALL_TOOLS = [
    read_file,
    search_code,
    run_linter,
    write_file,
    shell_exec,
    network_post,
]


# ---------------------------------------------------------------------------
# Build the guard. Proactive enforcement so the CrewAI Agent's bound
# tool list literally doesn't include the denied tools.
# ---------------------------------------------------------------------------


def build_guard() -> CrewAIGuard:
    return CrewAIGuard(
        agent_id="coder",
        tool_policy={
            "default": "deny",
            "approved": ["read_file", "search_code", "run_linter"],
            "enforcement": "proactive",
        },
        mode="enforce",
        verbose=False,
    )


def run() -> int:
    guard = build_guard()
    wrapped = guard.wrap(ALL_TOOLS)

    bound_names = sorted(
        getattr(t, "name", getattr(t, "__name__", "")) for t in wrapped
    )

    print("=" * 70)
    print("v0.2 case study: coding agent (CrewAI, proactive default-deny)")
    print("=" * 70)
    print(
        f"All tools the dev passed to guard.wrap(): {sorted(t.__name__ for t in ALL_TOOLS)}"
    )
    print(f"Tools the CrewAI Agent will actually see:  {bound_names}")
    print()

    approved = {"read_file", "search_code", "run_linter"}
    denied = {"write_file", "shell_exec", "network_post"}

    leaked = set(bound_names) & denied
    missing = approved - set(bound_names)

    if leaked:
        print(f"FAIL: denied tools leaked into the agent's binding: {leaked}")
        return 1
    if missing:
        print(f"FAIL: approved tools missing from binding: {missing}")
        return 1

    print("PASS: only approved tools are visible to the CrewAI Agent.")
    print()
    print("Implication for prompt injection: even if the model is")
    print("convinced to call `shell_exec` or `network_post`, those")
    print("tools aren't in its tool prompt at all. there is nothing")
    print("for the model to invoke.")

    # Also confirm filter_tools agrees with the wrap-time decision.
    legal = guard.filter_tools([t.__name__ for t in ALL_TOOLS])
    print()
    print(f"filter_tools agrees: {legal}")
    if set(legal) != approved:
        print(f"FAIL: filter_tools returned {legal}, expected {approved}")
        return 1
    print()
    print("PASS: filter_tools matches wrap-time decision.")
    return 0


if __name__ == "__main__":
    sys.exit(run())

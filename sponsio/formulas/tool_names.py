"""One canonical spelling for a tool name, shared by contracts and grounding.

Predicate keys are dict keys: ``called(issue_refund)`` and
``called(Issue_Refund)`` are simply different entries, and a contract
looks up the spelling its author typed. So a rule written against
``issue_refund`` used to be silently inert against an event whose tool
arrived as ``Issue_Refund``, as ``issue_refund `` with a stray space, or
as ``mcp__finance__issue_refund`` — which is our own documented wire
format for an MCP tool. ``must_precede`` compiles to
``Or(order-holds, never-called)``, so a name that never matches makes
the second disjunct trivially true and the whole contract reports
satisfied while the guarded action runs unchecked.

Two functions, used on opposite sides of the same join:

* :func:`canonical_tool` — what a *contract* keys on. Contracts are
  authored once, so they collapse to a single canonical spelling.
* :func:`tool_aliases` — what an *event* answers to. A tool call is a
  fact about the world and cannot be rewritten, so it is grounded under
  every spelling a contract might reasonably have used, canonical form
  included. That is where the two sides meet.

Widening only ever makes more contracts apply to a given call, never
fewer, so the failure mode this introduces is a rule firing on a tool
whose name merely resembles the one named — visible and reportable,
unlike the silent pass it replaces.
"""

from __future__ import annotations

import re

__all__ = ["canonical_tool", "tool_aliases", "MCP_TOOL_RE"]

# Claude Code / MCP wire format: ``mcp__<server>__<tool>``. The server
# segment has no ``__`` of its own; the tool segment may (some servers
# namespace further), so the split is on the FIRST ``__`` after the
# prefix and the remainder is the tool.
MCP_TOOL_RE = re.compile(r"^mcp__(?P<server>[^_]+(?:_[^_]+)*)__(?P<tool>.+)$")


def _strip_mcp(name: str) -> str | None:
    """``mcp__finance__issue_refund`` -> ``issue_refund``; else ``None``."""
    m = MCP_TOOL_RE.match(name)
    return m.group("tool") if m else None


def canonical_tool(tool: str) -> str:
    """The single spelling a contract keys on.

    Strips surrounding whitespace and case-folds. The MCP prefix is
    *not* stripped here: a contract that deliberately names
    ``mcp__finance__issue_refund`` means that server's tool and should
    not silently widen to every tool of that name. Grounding supplies
    the bare-name alias so the reverse direction still joins.
    """
    return str(tool).strip().casefold()


def tool_aliases(tool: str) -> tuple[str, ...]:
    """Every spelling an event's tool call should answer to.

    Ordered, duplicate-free, and always contains the raw name first so
    existing traces and predicate keys keep working unchanged.
    """
    raw = str(tool)
    out = [raw]

    stripped = raw.strip()
    out.append(stripped)
    out.append(stripped.casefold())

    bare = _strip_mcp(stripped)
    if bare:
        out.append(bare)
        out.append(bare.casefold())

    # dict.fromkeys preserves first-seen order while de-duplicating.
    return tuple(k for k in dict.fromkeys(out) if k)

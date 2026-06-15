"""Helpers shared across more than one CLI command/group module.

Kept deliberately small: only things genuinely used by multiple
command modules live here, so the per-command modules stay focused and
there is one obvious home for cross-cutting helpers.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import click


def _contract_guarantee(entry):
    """Read the guarantee block out of a YAML/dict contract entry.

    Reads the canonical ``G`` (short) / ``guarantee`` (long) keys. No
    legacy alias support. the rename is hard.
    """
    if not isinstance(entry, dict):
        return None
    return entry.get("G") or entry.get("guarantee")


def _looks_like_sponsio_config(path: Path) -> bool:
    """Return True if ``path`` is probably a :file:`sponsio.yaml` (not
    an arbitrary string the user wanted to parse as a contract).

    Kept intentionally narrow so ``sponsio validate interesting.yaml`` only
    auto-routes when the file *looks* like a Sponsio config, not every YAML
    on disk.
    """
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:32768]
    except OSError:
        return False
    # Project configs list agents; ``init`` output uses version+extractor.
    if re.search(r"(?m)^\s*agents:\s*", head):
        return True
    return bool(
        re.search(r"(?m)^\s*version:\s*\d", head)
        and re.search(r"(?m)^\s*extractor:\s*", head)
    )


def _resolve_entry(entry):
    """Resolve a constraint entry (string or ConstraintEntry) to (nl_text, parsed_result).

    For structured entries (pattern + args), compiles directly.
    For NL strings, runs through parse_nl_unified.
    """
    from sponsio.config import ConstraintEntry, _compile_structured
    from sponsio.generation.dsl_to_contract import (
        ContractSyntaxError,
        UnifiedParseResult,
        parse_nl_unified,
    )

    if isinstance(entry, ConstraintEntry):
        if entry.is_structured:
            try:
                compiled = _compile_structured(entry)
                nl = f"{entry.pattern}({', '.join(str(a) for a in entry.args)})"
                return nl, UnifiedParseResult(original_nl=nl, hard=compiled)
            except Exception:
                return str(entry.pattern), None
        elif entry.is_ltl:
            from sponsio.config import _compile_ltl

            try:
                compiled = _compile_ltl(entry)
                return entry.ltl or "", UnifiedParseResult(
                    original_nl=entry.ltl or "", hard=compiled
                )
            except Exception:
                return entry.ltl or "ltl", None
        else:
            nl = entry.nl
    else:
        nl = str(entry)
    try:
        return nl, parse_nl_unified(nl)
    except ContractSyntaxError:
        # Unparseable. `sponsio check` signals this by returning
        # a None result, same shape as a structured-compile error.
        return nl, None


def _parse_since(since: str) -> float:
    """Parse a relative duration like ``"24h"`` / ``"7d"`` / ``"30m"``
    into a Unix-timestamp cutoff (seconds).

    Returns ``0.0`` (= no cutoff) for the empty / sentinel values the
    user might pass when they want everything. Bare integers are
    interpreted as hours (``--since 6`` == ``--since 6h``) since
    ``hour`` is the unit operators reach for first.
    """
    import re as _re

    s = (since or "").strip().lower()
    if not s or s in ("0", "all"):
        return 0.0
    m = _re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd]?)", s)
    if not m:
        raise click.BadParameter(
            f"invalid --since value {since!r}; expected '24h' / '7d' / '30m' / '90s'",
        )
    n = float(m.group(1))
    unit = m.group(2) or "h"
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return time.time() - n * multipliers[unit]


def _parse_existing_contracts(yaml_path: Path, agent_id: str) -> list[dict]:
    """Extract the on-disk yaml's contracts so ``--emit-context``
    consumers can dedupe their semantic-pass proposals.

    Pulls only the fields a deduper actually needs (pattern, args,
    source) and only from the named agent's block.  Conservative:
    on any parse error, returns an empty list. a malformed yaml
    will still surface elsewhere (doctor, validate), no need to
    block the diagnostic JSON over it.

    Each returned dict has the shape::

        {"pattern": "arg_blacklist",
         "args": ["delete_snapshot", "path", ["...", "..."]],
         "source": "scan" | "library:tier1.shell" | "agent-extracted" | ...}

    Pack-included rules (resolved via ``include:``) are NOT walked
    here. the host agent only needs to dedupe against rules
    actually written into THIS yaml (the inline ``contracts:``
    block).  Pack rules round-trip through ``include:`` and the
    template's "don't inline what the pack already covers" rule
    keeps them out of the agent's proposals.
    """
    try:
        import yaml as _yaml
    except ImportError:
        return []

    try:
        text = yaml_path.read_text(encoding="utf-8")
        data = _yaml.safe_load(text)
    except Exception:
        return []

    if not isinstance(data, dict):
        return []
    agents = data.get("agents")
    if not isinstance(agents, dict):
        return []
    agent_block = agents.get(agent_id)
    if not isinstance(agent_block, dict):
        return []
    contracts = agent_block.get("contracts")
    if not isinstance(contracts, list):
        return []

    out: list[dict] = []
    for c in contracts:
        if not isinstance(c, dict):
            continue
        # Contracts can be written ``- G: {...}`` or ``- A: {...}, G: {...}``.
        # We pull from whichever has the pattern.
        g = _contract_guarantee(c)
        body = g if isinstance(g, dict) else c
        if not isinstance(body, dict):
            continue
        pattern = body.get("pattern")
        if not isinstance(pattern, str):
            continue
        out.append(
            {
                "pattern": pattern,
                "args": body.get("args") or [],
                "source": body.get("source") or c.get("source") or "",
            }
        )
    return out

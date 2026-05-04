"""Renderer + helpers for ``sponsio explain <contract>``.

When a user sees ``BLOCKED → C1`` in the session view, the first
follow-up is "what is C1, and why was it blocked?". This module
answers that:

  - resolves a name / ``C1`` alias against the loaded contract list
  - renders the contract's source intent + compiled LTL form
  - finds the most recent violation (and its triggering action) in
    the local session log, if any
  - shows a generic "how to resolve" pointer

OSS-only — Cloud installs layer LLM-driven fix hints + cross-trace
patterns on top via a separate ``cloud.explain`` overlay.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.text import Text

from sponsio.render.components import (
    cta_line,
    header_banner,
    indent,
    section_rule,
)
from sponsio.render.derive import short_contract_alias
from sponsio.render.tokens import PALETTE, STATUS, SYMBOLS

# ---------------------------------------------------------------------------
# Contract resolution.
# ---------------------------------------------------------------------------


_ALIAS_RE = re.compile(r"^[Cc](\d+)$")


def resolve_contract(query: str, contracts: list) -> tuple[Any | None, int | None]:
    """Resolve ``query`` against ``contracts``.

    Returns ``(contract, index)`` for the first match, or ``(None, None)``.

    Match order:
      1. ``C<n>`` alias (1-based) — direct index lookup.
      2. Exact ``contract.desc`` match (case-insensitive).
      3. Substring match against ``contract.desc`` (case-insensitive).

    Substring match is intentional: contract descs are often long
    sentences ("no destructive SQL while the freeze is in effect"); the
    user shouldn't have to retype them verbatim.
    """
    if not contracts:
        return None, None
    q = query.strip()

    # 1. Alias lookup.
    m = _ALIAS_RE.match(q)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(contracts):
            return contracts[idx], idx
        return None, None

    # 2. Exact desc.
    q_low = q.lower()
    for i, c in enumerate(contracts):
        if (getattr(c, "desc", "") or "").lower() == q_low:
            return c, i

    # 3. Substring desc.
    for i, c in enumerate(contracts):
        if q_low in (getattr(c, "desc", "") or "").lower():
            return c, i

    return None, None


# ---------------------------------------------------------------------------
# Compiled-formula rendering.
# ---------------------------------------------------------------------------


def _compile_to_nl(constraint: Any) -> str | None:
    """Walk a contract's constraint to a human-readable NL form.

    The Constraint type is a union (Formula / DetFormula / StoFormula /
    list). We unwrap to the Formula AST and run it through
    :func:`sponsio.formulas.nl_gen.formula_to_nl`. Returns ``None``
    if the constraint doesn't carry a Formula (e.g. an unbound NL stub).
    """
    if constraint is None:
        return None
    try:
        from sponsio.formulas.formula import FormulaMixin
        from sponsio.formulas.nl_gen import formula_to_nl
    except ImportError:
        return None

    items = constraint if isinstance(constraint, list) else [constraint]
    out: list[str] = []
    for item in items:
        formula = (
            item if isinstance(item, FormulaMixin) else getattr(item, "formula", None)
        )
        if formula is None:
            continue
        try:
            out.append(formula_to_nl(formula).strip())
        except Exception:
            continue
    if not out:
        return None
    return " AND ".join(out)


def _pattern_summary(constraint: Any) -> str | None:
    """Pull ``pattern_name`` + ``args`` off a DetFormula wrapper, if any."""
    items = constraint if isinstance(constraint, list) else [constraint]
    parts: list[str] = []
    for item in items:
        pattern = getattr(item, "pattern_name", None)
        args = getattr(item, "args", None)
        if pattern:
            if args:
                arg_repr = ", ".join(repr(a) for a in args)
                parts.append(f"{pattern}({arg_repr})")
            else:
                parts.append(str(pattern))
    if not parts:
        return None
    return " AND ".join(parts)


# ---------------------------------------------------------------------------
# Session-log lookup — "what was the most recent violation of this contract?"
# ---------------------------------------------------------------------------


def find_last_violation(
    constraint_name: str, sessions_dir: Path | None = None
) -> dict | None:
    """Scan ``sessions_dir`` for the most recent BLOCKED / OBSERVED
    event matching ``constraint_name``.

    Returns the raw JSONL record dict (with ``ts``, ``agent_id``,
    ``action``, ``constraint``, ``result``) for the latest match, or
    ``None`` if no match.
    """
    if sessions_dir is None:
        from sponsio.runtime.session_log import _resolve_default_base_dir

        sessions_dir = _resolve_default_base_dir()
    if not sessions_dir.exists():
        return None

    target = constraint_name.lower()
    best: dict | None = None
    best_ts: float = 0.0
    for path in sessions_dir.rglob("*.jsonl"):
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if (rec.get("constraint") or "").lower() != target:
                        continue
                    result = rec.get("result") or {}
                    if result.get("action") not in {"blocked", "observed", "retrying"}:
                        continue
                    ts = float(rec.get("ts") or 0)
                    if ts >= best_ts:
                        best_ts = ts
                        best = rec
        except OSError:
            continue
    return best


# ---------------------------------------------------------------------------
# Rendering.
# ---------------------------------------------------------------------------


def render_explain(
    *,
    console: Console,
    contract: Any,
    index: int,
    last_violation: dict | None = None,
    config_path: Path | None = None,
) -> None:
    """Emit the explain view to ``console``."""
    alias = short_contract_alias(getattr(contract, "desc", "") or "", index)
    desc = getattr(contract, "desc", None) or "(unnamed)"

    # 1. Header banner.  Leading blank line so the banner doesn't
    # land on the same row as the user's previous shell prompt —
    # ``$ sponsio explain C1`` sat flush against the ``━━━ ◒◓
    # Sponsio ━━━ explain C1 ━━━`` rule with no breathing room.
    console.print()
    console.print(header_banner(tagline=f"explain {alias}"))
    console.print()

    # 2. Title line: alias + desc.
    title = Text.assemble(
        ("  ", ""),
        (alias, f"bold {PALETTE['brand']}"),
        ("  ", ""),
        (desc, f"bold {PALETTE['fg']}"),
    )
    console.print(title)
    console.print()

    # 3. Source / pattern section.
    console.print(indent(section_rule("contract")))
    pattern_a = _pattern_summary(contract.assumption) if contract.assumption else None
    pattern_e = _pattern_summary(contract.guarantee)
    if pattern_a:
        console.print(indent(_label_line("assume", pattern_a)))
    if pattern_e:
        console.print(indent(_label_line("enforce", pattern_e)))
    if config_path:
        console.print(
            indent(
                Text.assemble(
                    ("source: ", PALETTE["metadata"]),
                    (str(config_path), PALETTE["metadata"]),
                )
            )
        )
    console.print()

    # 4. Compiled formula section.
    console.print(indent(section_rule("compiled (LTL)")))
    nl_a = _compile_to_nl(contract.assumption) if contract.assumption else None
    nl_e = _compile_to_nl(contract.guarantee)
    if nl_a:
        console.print(indent(_label_line("assume", nl_a, dim=True)))
    else:
        console.print(
            indent(_label_line("assume", "(unconditional — always active)", dim=True))
        )
    if nl_e:
        console.print(indent(_label_line("enforce", nl_e, dim=True)))
    console.print()

    # 5. Last activity from the session log.
    console.print(indent(section_rule("recent activity")))
    if last_violation:
        ts = last_violation.get("ts") or 0
        rec_action = (last_violation.get("result") or {}).get("action", "?").upper()
        rec_msg = (last_violation.get("result") or {}).get("message") or ""
        from datetime import datetime, timezone

        ts_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%SZ"
        )
        agent = last_violation.get("agent_id") or "?"
        action = last_violation.get("action") or "?"
        status_color = STATUS.get(rec_action, PALETTE["violation"])
        console.print(
            indent(
                Text.assemble(
                    (f"{SYMBOLS['fail']} ", f"bold {status_color}"),
                    (rec_action, f"bold {status_color}"),
                    ("  ", ""),
                    (ts_str, PALETTE["metadata"]),
                    ("  ", ""),
                    ("agent ", PALETTE["metadata"]),
                    (agent, PALETTE["fg"]),
                    ("  ", ""),
                    ("on ", PALETTE["metadata"]),
                    (action, PALETTE["fg"]),
                )
            )
        )
        if rec_msg:
            console.print(indent(Text(_trim(rec_msg, 140), style=PALETTE["metadata"])))
    else:
        console.print(
            indent(
                Text(
                    "no recorded violations of this contract in ~/.sponsio/sessions/",
                    style=PALETTE["metadata"],
                )
            )
        )
    console.print()

    # 6. How to resolve.
    console.print(indent(section_rule("how to resolve")))
    for line in _resolution_hints(contract):
        console.print(indent(Text(f"• {line}", style=PALETTE["fg"])))
    console.print()

    # 7. CTA.
    ctas = ["sponsio report --since 24h"]
    if last_violation:
        # Likely the user wants to dig further into the trace.
        ctas.append("sponsio host trace --follow")
    console.print(cta_line(ctas))


def _label_line(label: str, body: str, *, dim: bool = False) -> Text:
    """``label   body`` row used in the compiled / source sections."""
    body_style = PALETTE["metadata"] if dim else PALETTE["fg"]
    return Text.assemble(
        (f"{label:<8}", PALETTE["metadata"]),
        (body, body_style),
    )


def _trim(s: str, n: int) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _resolution_hints(contract: Any) -> list[str]:
    """Generic hints derived from contract shape.

    Cloud's overlay replaces this with LLM-judged contextual fixes; in
    OSS we lean on what's structurally inferable from the pattern type.
    """
    hints: list[str] = []
    pattern = _detect_pattern_kind(contract)
    if pattern in {"must_precede", "always_followed_by"}:
        hints.append("make sure the prerequisite tool fires before the target action")
    elif pattern in {"rate_limit", "cooldown"}:
        hints.append("space out calls to this tool (or batch them)")
    elif pattern in {"arg_blacklist", "no_data_leak", "scope_limit"}:
        hints.append("inspect the argument value — it matched a forbidden pattern")
    elif pattern in {"idempotent", "loop_detection", "bounded_retry"}:
        hints.append("the tool is being called more times than the contract permits")
    elif pattern in {"destructive_action_gate", "must_confirm"}:
        hints.append(
            "obtain explicit confirmation / approval before invoking this tool"
        )
    if not hints:
        hints.append("review the contract source and the recent violation message")
    hints.append("inspect the full violating trace with `sponsio host trace --follow`")
    return hints


def _detect_pattern_kind(contract: Any) -> str | None:
    """Pull the deterministic pattern name off the enforcement, if any."""
    items = (
        contract.guarantee
        if isinstance(contract.guarantee, list)
        else [contract.guarantee]
    )
    for item in items:
        name = getattr(item, "pattern_name", None)
        if name:
            return str(name)
    return None


# ---------------------------------------------------------------------------
# JSON form — same data, machine-readable.
# ---------------------------------------------------------------------------


def explain_to_dict(
    contract: Any, index: int, last_violation: dict | None = None
) -> dict:
    """Structured form of the explain view for ``--format=json``."""
    return {
        "alias": short_contract_alias(getattr(contract, "desc", "") or "", index),
        "desc": getattr(contract, "desc", None),
        "assumption": {
            "pattern": _pattern_summary(contract.assumption)
            if contract.assumption
            else None,
            "compiled_nl": _compile_to_nl(contract.assumption)
            if contract.assumption
            else None,
        },
        "guarantee": {
            "pattern": _pattern_summary(contract.guarantee),
            "compiled_nl": _compile_to_nl(contract.guarantee),
        },
        "alpha": getattr(contract, "alpha", None),
        "beta": getattr(contract, "beta", None),
        "activate_at": getattr(contract, "activate_at", None),
        "last_violation": last_violation,
        "resolution_hints": _resolution_hints(contract),
    }

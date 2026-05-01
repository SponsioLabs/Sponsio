"""Rich helpers for the in-process live monitor (terminal.py).

These render :class:`~sponsio.runtime.monitor.MonitorEvent` records as
single-line Rich ``Text`` objects suitable for streaming to stderr
during a user's agent run. The shape differs from the trace-tree
components in :mod:`sponsio.render.components`:

* Each MonitorEvent line stands alone (no parent event-row to nest under).
* The constraint name is the primary identifier (vs. an alias in trace).
* No latency column — these fire at agent-tool-call cadence, not
  contract-check cadence; latency would be misleading.

Public surface: ``render_event(event, contract_label_map)`` and
``render_banner(contracts, console)``. The dedup / verbosity logic
stays in :class:`sponsio.runtime.terminal.TerminalReporter`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.text import Text

from sponsio.render.components import (
    contracts_table,
    header_banner,
    indent,
    section_rule,
)
from sponsio.render.derive import short_contract_alias
from sponsio.render.tokens import PALETTE, STATUS, SYMBOLS

if TYPE_CHECKING:
    from sponsio.runtime.monitor import MonitorEvent


# ---------------------------------------------------------------------------
# Constants — pre-styled prefixes used many times per session.
# ---------------------------------------------------------------------------

_LEAD = "  "  # 2-space indent matches the spec's banner content position


def _bold(text: str, color: str) -> tuple[str, str]:
    return (text, f"bold {color}")


# ---------------------------------------------------------------------------
# Per-event renderers — one per case in TerminalReporter._format.
# ---------------------------------------------------------------------------


def render_assume_satisfied(
    constraint_name: str, contract_label: str | None = None
) -> Text:
    """Single-line announcement: assumption satisfied, contract active.

    Combines two concerns the old reporter emitted as separate lines —
    one Rich row reads better and is harder to lose in a fast scroll.
    """
    parts: list[tuple[str, str]] = [
        (_LEAD, ""),
        (f"{SYMBOLS['active']} ", PALETTE["active"]),
        ("assume ", PALETTE["fg"]),
        (f'"{constraint_name}"', PALETTE["metadata"]),
        (" → ", PALETTE["metadata"]),
        _bold("READY", STATUS["READY"]),
    ]
    if contract_label:
        parts.extend(
            [
                (" · ", PALETTE["metadata"]),
                ("contract ", PALETTE["metadata"]),
                _bold(contract_label, PALETTE["brand"]),
                (" → ", PALETTE["metadata"]),
                _bold("ACTIVE", STATUS["ACTIVE"]),
            ]
        )
    return Text.assemble(*parts)


def render_assume_unsatisfied(constraint_name: str) -> Text:
    """``⚙ assume "X" → not yet satisfied`` (verbosity ≥ 2 only)."""
    return Text.assemble(
        (_LEAD, ""),
        (f"{SYMBOLS['active']} ", PALETTE["metadata"]),
        ("assume ", PALETTE["metadata"]),
        (f'"{constraint_name}"', PALETTE["metadata"]),
        (" → not yet satisfied", PALETTE["metadata"]),
    )


def render_violation(
    constraint_name: str,
    action: str,
    *,
    status: str = "BLOCKED",
) -> Text:
    """``✗ enforce "X" on "Y" → BLOCKED``."""
    color = STATUS.get(status.upper(), PALETTE["violation"])
    return Text.assemble(
        (_LEAD, ""),
        (f"{SYMBOLS['fail']} ", f"bold {color}"),
        ("enforce ", PALETTE["fg"]),
        (f'"{constraint_name}"', PALETTE["metadata"]),
        (" on ", PALETTE["metadata"]),
        (action, PALETTE["fg"]),
        (" → ", PALETTE["metadata"]),
        _bold(status.upper(), color),
    )


def render_observed(constraint_name: str, action: str) -> Text:
    """Shadow-mode violation: ``⚠ enforce "X" on "Y" → WARN (observe)``."""
    return Text.assemble(
        (_LEAD, ""),
        ("⚠ ", f"bold {PALETTE['warning']}"),
        ("enforce ", PALETTE["fg"]),
        (f'"{constraint_name}"', PALETTE["metadata"]),
        (" on ", PALETTE["metadata"]),
        (action, PALETTE["fg"]),
        (" → ", PALETTE["metadata"]),
        _bold("WARN", PALETTE["warning"]),
        (" (observe)", PALETTE["metadata"]),
    )


def render_pass(
    constraint_name: str,
    action: str,
    *,
    pipeline: str = "det",
    score: float | None = None,
) -> Text:
    """``✓ enforce "X" on "Y" → PASS`` (verbosity ≥ 2)."""
    kind = "sto" if pipeline == "sto" else "enforce"
    parts: list[tuple[str, str]] = [
        (_LEAD, ""),
        (f"{SYMBOLS['pass']} ", f"bold {PALETTE['success']}"),
        (f"{kind} ", PALETTE["fg"]),
        (f'"{constraint_name}"', PALETTE["metadata"]),
        (" on ", PALETTE["metadata"]),
        (action, PALETTE["fg"]),
    ]
    if score is not None:
        parts.append((f" score {score:.2f}", PALETTE["metadata"]))
    parts.extend([(" → ", PALETTE["metadata"]), _bold("PASS", PALETTE["success"])])
    return Text.assemble(*parts)


def render_sto_retry(
    constraint_name: str,
    action: str,
    *,
    score: float | None = None,
) -> Text:
    """``⚠ sto "X" on "Y" score N.NN → retrying with feedback``."""
    parts: list[tuple[str, str]] = [
        (_LEAD, ""),
        ("⚠ ", f"bold {PALETTE['warning']}"),
        ("sto ", PALETTE["fg"]),
        (f'"{constraint_name}"', PALETTE["metadata"]),
        (" on ", PALETTE["metadata"]),
        (action, PALETTE["fg"]),
    ]
    if score is not None:
        parts.append((f" score {score:.2f}", PALETTE["metadata"]))
    parts.extend(
        [
            (" → ", PALETTE["metadata"]),
            _bold("retrying with feedback", PALETTE["warning"]),
        ]
    )
    return Text.assemble(*parts)


# ---------------------------------------------------------------------------
# Dispatch — single entry point for TerminalReporter.
# ---------------------------------------------------------------------------


def render_event(
    event: "MonitorEvent",
    *,
    verbosity: int = 1,
    contract_label_map: dict[str, str] | None = None,
    seen_satisfied: set[str] | None = None,
) -> list[Text]:
    """Render a MonitorEvent into zero or more Rich ``Text`` lines.

    The dedup / first-time-only suppression for satisfied assumptions
    happens here too, by mutating ``seen_satisfied`` — that lets the
    caller use a plain ``set()`` as state without subclassing anything.

    Returns an empty list when the event should be hidden at the
    current verbosity level.
    """
    contract_label_map = contract_label_map or {}
    seen_satisfied = seen_satisfied if seen_satisfied is not None else set()

    action_str = event.result.action
    is_violation = action_str in ("blocked", "escalated", "retrying", "observed")
    is_observed = action_str == "observed"

    # Verbosity 0: violations only.
    if verbosity == 0 and not is_violation:
        return []

    name = event.constraint_name
    is_assumption = name.startswith("assumption: ")
    desc = name.removeprefix("assumption: ") if is_assumption else name

    # ---- Det pipeline ---------------------------------------------------

    if event.pipeline == "det":
        if is_assumption:
            if is_violation:
                # Unsatisfied assumption: contract dormant, not a real
                # violation. Hide unless verbosity is cranked up.
                if verbosity < 2:
                    return []
                return [render_assume_unsatisfied(desc)]

            # Satisfied — only the *first* time, even if it satisfies
            # again on subsequent steps.
            if desc in seen_satisfied:
                return []
            seen_satisfied.add(desc)
            label = contract_label_map.get(desc)
            return [render_assume_satisfied(desc, contract_label=label)]

        # Enforcement.
        if is_violation:
            if is_observed:
                return [render_observed(desc, event.action)]
            status = action_str.upper() if action_str != "blocked" else "BLOCKED"
            return [render_violation(desc, event.action, status=status)]

        # Enforcement pass: noisy at v<2.
        if verbosity < 2:
            return []
        return [render_pass(desc, event.action, pipeline="det")]

    # ---- Sto pipeline ---------------------------------------------------

    if event.pipeline == "sto":
        score = (
            event.sto_result.score
            if event.sto_result is not None and event.sto_result.score is not None
            else None
        )
        if is_violation:
            return [render_sto_retry(desc, event.action, score=score)]
        if verbosity < 2:
            return []
        return [render_pass(desc, event.action, pipeline="sto", score=score)]

    return []


# ---------------------------------------------------------------------------
# Banner — printed once at guard init.
# ---------------------------------------------------------------------------


def render_banner(contracts: list, *, console: Console) -> None:
    """Print the contracts-armed banner to ``console``.

    Terse, scannable: short alias + label + READY/ACTIVE state. Bare
    contracts (no assumption) are ACTIVE from step 0; conditional ones
    sit at READY until their assumption fires. The reporter announces
    the READY → ACTIVE transition live as the agent runs.
    """
    if not contracts:
        return
    console.print()
    console.print(header_banner(tagline="contract enforcement armed"))
    console.print()
    console.print(indent(section_rule(f"contracts armed ({len(contracts)})")))
    rows: list[tuple[str, str, str]] = []
    for i, c in enumerate(contracts):
        alias = short_contract_alias(_contract_label(c), i)
        label = _contract_label(c) or "(unnamed)"
        is_bare = not (getattr(c, "assumptions", []) or [])
        status = "ACTIVE" if is_bare else "READY"
        rows.append((alias, label, status))
    console.print(indent(contracts_table(rows)))
    console.print()


def build_label_map(contracts: list) -> dict[str, str]:
    """Map ``assumption.desc`` → contract label so the reporter can
    name which contract just went live when its assumption fires."""
    out: dict[str, str] = {}
    for c in contracts:
        for a in getattr(c, "assumptions", []) or []:
            assume_desc = getattr(a, "desc", str(a))
            label = _contract_label(c)
            if assume_desc and label:
                out[assume_desc] = label
    return out


def _contract_label(c) -> str:
    """Best-effort short label: ``Contract.desc`` → first enforcement's
    desc → empty string. Matches the existing TerminalReporter heuristic
    so banner labels stay stable across the rewrite."""
    desc = getattr(c, "desc", None)
    if desc:
        return str(desc)
    enforcements = getattr(c, "enforcements", []) or []
    if enforcements:
        return str(getattr(enforcements[0], "desc", "") or "")
    return getattr(getattr(c, "agent", None), "id", "") or ""

"""Reusable Rich primitives for the redesigned CLI output.

Every component is a pure function: take data, return a Rich
renderable. Composition (which zone, what order, when to print) lives
in the renderer modules; this file only knows about *one piece*.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from rich.padding import Padding
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from sponsio.render.tokens import PALETTE, STATUS, SYMBOLS, service_color

# ---------------------------------------------------------------------------
# Banners and rules.
# ---------------------------------------------------------------------------


def header_banner(
    brand: str = "Sponsio",
    tagline: str = "runtime contract enforcement",
) -> Rule:
    """Top-of-output banner: ``━━━ Sponsio ━━━ tagline ━━━━━━━━━``."""
    title = Text.assemble(
        ("━━━ ", PALETTE["rule"]),
        (brand, f"bold {PALETTE['brand']}"),
        (" ━━━ ", PALETTE["rule"]),
        (tagline, PALETTE["fg"]),
        (" ", ""),
    )
    return Rule(
        title=title,
        characters=SYMBOLS["rule_heavy"],
        style=PALETTE["rule"],
        align="left",
    )


def verdict_banner(status: str, label: str = "VERDICT") -> Rule:
    """Bottom banner with a status word colored per ``STATUS`` table."""
    color = STATUS.get(status.upper(), PALETTE["fg"])
    title = Text.assemble(
        ("━━━ ", PALETTE["rule"]),
        (label, f"bold {PALETTE['brand']}"),
        (" ━━━ ", PALETTE["rule"]),
        (status.upper(), f"bold {color}"),
        (" ", ""),
    )
    return Rule(
        title=title,
        characters=SYMBOLS["rule_heavy"],
        style=PALETTE["rule"],
        align="left",
    )


def section_rule(label: str) -> Rule:
    """Inside-zone divider: ``label ──────────``."""
    title = Text(f"{label} ", style=PALETTE["fg"])
    return Rule(
        title=title,
        characters=SYMBOLS["rule_light"],
        style=PALETTE["rule"],
        align="left",
    )


# ---------------------------------------------------------------------------
# Header metadata grid.
# ---------------------------------------------------------------------------


def header_meta(pairs: Sequence[tuple[str, str]]) -> Table:
    """Render an arbitrary list of ``(label, value)`` pairs as a borderless
    grid, three per row. The mode/status pair (last column) is highlighted.
    """
    t = Table.grid(padding=(0, 4), pad_edge=False)
    cols_per_row = 3
    for _ in range(cols_per_row * 2):
        t.add_column()  # alternating label, value
    rows: list[list[Text]] = []
    current: list[Text] = []
    for i, (label, value) in enumerate(pairs):
        is_last_col = (i % cols_per_row) == (cols_per_row - 1)
        # Highlight the status-ish column (rightmost by convention).
        value_style = f"bold {PALETTE['brand']}" if is_last_col else PALETTE["fg"]
        current.append(Text(label, style=PALETTE["metadata"]))
        current.append(Text(str(value), style=value_style))
        if (i + 1) % cols_per_row == 0:
            rows.append(current)
            current = []
    # Pad the trailing row so add_row gets the expected column count.
    if current:
        while len(current) < cols_per_row * 2:
            current.append(Text(""))
        rows.append(current)
    for row in rows:
        t.add_row(*row)
    return t


# ---------------------------------------------------------------------------
# Contracts list.
# ---------------------------------------------------------------------------


def contracts_table(rows: Iterable[tuple[str, str, str]]) -> Table:
    """Render rows of ``(alias, name, status)``.

    Status word is colored per ``STATUS`` (READY=green, BLOCKED=red, etc.).
    Alias is the ``#1``-style short ID; the real contract name is the
    primary label.
    """
    t = Table.grid(padding=(0, 2), pad_edge=False, expand=True)
    t.add_column(width=4, style=f"bold {PALETTE['brand']}")
    t.add_column(ratio=1)
    t.add_column(width=10, justify="right")
    for alias, name, status in rows:
        color = STATUS.get(status.upper(), PALETTE["metadata"])
        t.add_row(
            alias,
            name,
            Text(status.upper(), style=f"bold {color}"),
        )
    return t


# ---------------------------------------------------------------------------
# Per-contract violation rows (used by `sponsio report`).
# ---------------------------------------------------------------------------


def contract_stats_table(
    rows: Iterable[
        tuple[str, str, str, int, int, int, str]
    ],  # alias, name, pipeline, blocked, observed, retrying, sample
) -> Table:
    """Wider view: contract + pipeline + counts + sample message snippet.

    Used by the `sponsio report` overview. Pipeline is color-coded
    (det=metadata grey, sto=violet) so a mixed library reads at a glance.
    """
    t = Table(
        show_header=True,
        header_style=f"bold {PALETTE['metadata']}",
        border_style=PALETTE["rule"],
        pad_edge=False,
        expand=True,
    )
    t.add_column("#", style=f"bold {PALETTE['brand']}", width=4)
    t.add_column("contract", style=PALETTE["fg"], ratio=2, no_wrap=False)
    t.add_column("pipe", width=5)
    t.add_column("blocked", justify="right", style=f"bold {PALETTE['violation']}")
    t.add_column("observed", justify="right", style=PALETTE["warning"])
    t.add_column("retrying", justify="right", style=PALETTE["warning"])
    t.add_column("sample", style=PALETTE["metadata"], ratio=3, no_wrap=True)
    for alias, name, pipeline, blocked, observed, retrying, sample in rows:
        pipe_color = PALETTE["active"] if pipeline == "sto" else PALETTE["metadata"]
        t.add_row(
            alias,
            name,
            Text(pipeline, style=pipe_color),
            str(blocked),
            str(observed),
            str(retrying),
            sample or "—",
        )
    return t


# ---------------------------------------------------------------------------
# Trace event lines (consumed by phase 2 — terminal.py live output).
# ---------------------------------------------------------------------------


def event_line(
    timestamp: str,
    tool: str,
    args: str,
    latency: str,
    service: str,
    branch: str = "├─",
) -> Text:
    """One ``├─ tool args  +Nms  service`` row in the trace tree."""
    parts: list[tuple[str, str]] = [
        (f"{timestamp:<6}", PALETTE["metadata"]),
        (f"  {branch} ", PALETTE["metadata"]),
        (tool, f"bold {PALETTE['fg']}"),
    ]
    if args:
        parts.append((f" {args}", PALETTE["metadata"]))
    if latency:
        parts.append((f"  {latency}".rjust(8), PALETTE["metadata"]))
    parts.append(("  ", ""))
    parts.append((service, service_color(service)))
    return Text.assemble(*parts)


def assume_line(contract_alias: str, summary: str, latency: str = "") -> Text:
    """Nested ``│  └─ ⚙ assume[#1] summary  ✓  Nµs`` line."""
    return Text.assemble(
        ("    │  └─ ", PALETTE["metadata"]),
        (f"{SYMBOLS['active']} ", PALETTE["active"]),
        (f"assume[{contract_alias}]  ", PALETTE["fg"]),
        (summary, PALETTE["fg"]),
        (f"  {SYMBOLS['pass']}", f"bold {PALETTE['success']}"),
        (f"  {latency}".rjust(10) if latency else "", PALETTE["metadata"]),
    )


def state_transition_line(contract_alias: str, new_state: str) -> Text:
    """``contract #1 → ACTIVE`` line, dimmed except for the new state."""
    color = STATUS.get(new_state.upper(), PALETTE["success"])
    return Text.assemble(
        ("    │     ", PALETTE["metadata"]),
        ("contract ", PALETTE["metadata"]),
        (contract_alias, f"bold {PALETTE['brand']}"),
        (f" {SYMBOLS['cta']} ", PALETTE["metadata"]),
        (new_state.upper(), f"bold {color}"),
    )


def enforce_violation_line(
    contract_alias: str, summary: str, status: str = "BLOCKED", latency: str = ""
) -> Text:
    """``✗ enforce[#1] summary  BLOCKED  Nµs`` line."""
    color = STATUS.get(status.upper(), PALETTE["violation"])
    return Text.assemble(
        ("            ", ""),
        (f"{SYMBOLS['fail']} ", f"bold {color}"),
        (f"enforce[{contract_alias}]  ", PALETTE["fg"]),
        (summary, PALETTE["fg"]),
        (f"  {status.upper()}", f"bold {color}"),
        (f"  {latency}".rjust(10) if latency else "", PALETTE["metadata"]),
    )


# ---------------------------------------------------------------------------
# Verdict / summary lines.
# ---------------------------------------------------------------------------


def verdict_summary(headline: str, *, violations: int, warnings: int) -> Text:
    """Single line below the verdict banner."""
    return Text.assemble(
        (headline, PALETTE["fg"]),
        (" · ", PALETTE["metadata"]),
        (f"{violations} violations", PALETTE["fg"]),
        (" · ", PALETTE["metadata"]),
        (f"{warnings} warnings", PALETTE["fg"]),
    )


def perf_line(
    *,
    total_checks: int,
    deterministic_pct: float,
    llm_calls: int,
) -> Text:
    return Text.assemble(
        (f"{total_checks} checks", PALETTE["fg"]),
        ("   ", ""),
        (f"{deterministic_pct:.0f}% deterministic", PALETTE["fg"]),
        ("   ", ""),
        (f"{llm_calls} LLM calls", PALETTE["fg"]),
    )


def latency_line(
    *,
    p50: str | None,
    p99: str | None,
    max_: str | None,
    qps_human: str | None = None,
) -> Text:
    parts: list[tuple[str, str]] = []
    if p50 is not None:
        parts.extend([("p50  ", PALETTE["metadata"]), (p50, PALETTE["fg"])])
    if p99 is not None:
        parts.extend([("   p99  ", PALETTE["metadata"]), (p99, PALETTE["fg"])])
    if max_ is not None:
        parts.extend([("   max  ", PALETTE["metadata"]), (max_, PALETTE["fg"])])
    if qps_human:
        parts.extend([("   ", ""), (qps_human, f"bold {PALETTE['brand']}")])
    return Text.assemble(*parts) if parts else Text("")


# ---------------------------------------------------------------------------
# Call-to-action footer.
# ---------------------------------------------------------------------------


def cta_line(commands: Sequence[str]) -> Text:
    """``  → cmd1     cmd2     cmd3`` — each cmd separated by 5 spaces."""
    arrow = Text(f"  {SYMBOLS['cta']} ", style=f"bold {PALETTE['brand']}")
    body = Text("     ".join(commands), style=PALETTE["fg"])
    return arrow + body


def indent(renderable: Any, spaces: int = 2) -> Padding:
    """Two-space left-pad helper for any Rich renderable.

    Uses ``Padding`` rather than ``Text`` concatenation so Tables, Rules,
    and Groups render correctly — concatenation falls back to ``str()``
    on non-Text types and emits the repr.
    """
    return Padding(renderable, (0, 0, 0, spaces))

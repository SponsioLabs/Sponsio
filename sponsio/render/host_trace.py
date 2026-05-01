"""Rich palette wrapper for ``sponsio host trace --follow`` output.

The host trace stream is line-oriented: the per-host adapter yields
``(level, raw_line)`` tuples and we color each one by level using the
shared token palette. Adding structure (timestamps, parsing) is out of
scope here — the host adapters' raw lines are the contract.

Phase 2 keeps this thin; the value is consistency with the rest of the
redesigned CLI surface (same colors as `sponsio report`), not richer
parsing.
"""

from __future__ import annotations

import sys

from rich.console import Console
from rich.text import Text

from sponsio.render.tokens import PALETTE


# ---------------------------------------------------------------------------
# Level → token mapping. Each adapter yields levels from this set.
# ---------------------------------------------------------------------------

LEVEL_COLORS: dict[str, str] = {
    "call": PALETTE["warning"],  # the agent invoked a tool
    "ok": PALETTE["success"],  # tool succeeded
    "block": PALETTE["violation"],  # Sponsio denied the call
    "text": PALETTE["brand"],  # assistant text
    "user": PALETTE["fg"],  # user text
    "error": PALETTE["violation"],  # adapter / runtime error
}

# Optional leading glyph per level — gives a scannable left margin
# without polluting the line content.
LEVEL_GLYPHS: dict[str, str] = {
    "call": "→",
    "ok": "←",
    "block": "✗",
    "text": "⋯",
    "user": "›",
    "error": "!",
}


def make_stdout_console(colorize: bool | None = None) -> Console:
    """Build a Rich Console for the host_trace command.

    ``colorize=None`` means auto: respect ``NO_COLOR`` and stdout TTY.
    """
    import os

    if colorize is False or os.environ.get("NO_COLOR"):
        return Console(color_system=None, force_terminal=False, highlight=False)
    if colorize is True:
        return Console(
            color_system="truecolor",
            force_terminal=True,
            highlight=False,
        )
    return Console(highlight=False, force_terminal=sys.stdout.isatty() or None)


def print_line(console: Console, level: str, line: str) -> None:
    """Print one host-trace line with the level's glyph and color.

    Unknown levels render in default foreground without a glyph — the
    safer fallback than crashing if an adapter introduces a new level.
    """
    color = LEVEL_COLORS.get(level)
    glyph = LEVEL_GLYPHS.get(level)
    if glyph and color:
        text = Text.assemble(
            (f" {glyph} ", f"bold {color}"),
            (line, color),
        )
    elif color:
        text = Text(line, style=color)
    else:
        text = Text(line)
    console.print(text)

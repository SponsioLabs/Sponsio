"""Tests for the host_trace palette wrapper.

The host trace stream is line-oriented; we only assert the palette
mapping, glyph prefixes, and graceful fallback for unknown levels.
"""

from __future__ import annotations

import re

from rich.console import Console

from sponsio.render.host_trace import (
    LEVEL_COLORS,
    LEVEL_GLYPHS,
    print_line,
)
from sponsio.render.tokens import PALETTE


def _capture(level: str, line: str) -> str:
    console = Console(
        record=True, width=120, force_terminal=True, color_system="truecolor"
    )
    print_line(console, level, line)
    return console.export_text(styles=True)


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def test_levels_cover_every_adapter_emission():
    """The set of LEVEL_COLORS must match what host adapters yield —
    if a new level appears in the wild, the test fails loudly so we
    add it here rather than silently downgrading to no-color."""
    expected = {"call", "ok", "block", "text", "user", "error"}
    assert expected == set(LEVEL_COLORS.keys())
    # Every level also has a glyph.
    assert expected == set(LEVEL_GLYPHS.keys())


def test_level_colors_come_from_palette():
    """No raw hex — all level colors are PALETTE tokens."""
    palette_values = set(PALETTE.values())
    for level, color in LEVEL_COLORS.items():
        assert color in palette_values, f"{level}: {color} not a PALETTE value"


def test_call_level_uses_warning_color_and_arrow_glyph():
    out = _capture("call", "tool execute_sql args={...}")
    plain = _strip_ansi(out)
    assert "→" in plain
    assert "tool execute_sql" in plain
    # PALETTE['warning'] = #FCD34D → ANSI 38;2;252;211;77
    assert "38;2;252;211;77" in out


def test_block_level_uses_violation_color():
    out = _capture("block", "denied: destructive SQL during freeze")
    plain = _strip_ansi(out)
    assert "✗" in plain
    assert "denied" in plain
    assert "38;2;252;165;165" in out  # PALETTE['violation']


def test_ok_level_uses_success_color():
    out = _capture("ok", "tool returned 200 OK")
    assert "38;2;134;239;172" in out  # PALETTE['success']


def test_unknown_level_falls_back_to_plain_text_no_glyph():
    """An adapter that yields an unrecognised level should not crash —
    the line must still print, just without color or glyph."""
    out = _capture("debug", "internal trace log")
    plain = _strip_ansi(out)
    assert "internal trace log" in plain
    # No glyph prefix added.
    assert plain.lstrip().startswith("internal")


def test_text_level_uses_brand_color():
    """Assistant text uses the brand cyan so it stands apart from tool call/ok."""
    out = _capture("text", "Hello, I am a helpful assistant.")
    assert "38;2;125;211;252" in out  # PALETTE['brand']

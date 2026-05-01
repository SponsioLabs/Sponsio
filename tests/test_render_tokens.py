"""Tests for the render layer's design invariants.

The point of ``sponsio/render/tokens.py`` is to be the *only* place
where raw hex colors live. Pattern enforcement lives here so accidents
during future PRs surface as a failing test instead of as a tasteful-
looking divergence in the CLI output.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sponsio.render import pick_format
from sponsio.render.tokens import (
    PALETTE,
    SERVICE_COLORS,
    STATUS,
    SYMBOLS,
    service_color,
)


# ---------------------------------------------------------------------------
# Token shape.
# ---------------------------------------------------------------------------


def test_palette_has_required_semantic_keys():
    """Every semantic role the renderer uses must exist in the palette."""
    required = {
        "brand",
        "success",
        "violation",
        "warning",
        "active",
        "metadata",
        "muted",
        "rule",
        "fg",
    }
    assert required <= PALETTE.keys()


def test_palette_values_are_hex_triplets():
    """Token values must be ``#RRGGBB`` — Rich accepts this directly."""
    pattern = re.compile(r"^#[0-9A-Fa-f]{6}$")
    for name, value in PALETTE.items():
        assert pattern.fullmatch(value), f"{name}: {value!r} not #RRGGBB"


def test_service_color_falls_back_to_muted():
    """Unknown services should render as muted, not crash or color clash."""
    assert service_color("clearly_not_a_service") == PALETTE["muted"]


def test_service_color_returns_brand_for_known():
    assert service_color("postgres") == "#336791"
    assert service_color("openai") == "#10A37F"


def test_local_protocol_services_share_muted_token():
    """fs/shell/mcp/http should resolve to the muted token, not a copy."""
    for svc in ("fs", "shell", "mcp", "http"):
        assert SERVICE_COLORS[svc] == PALETTE["muted"]


def test_status_words_map_to_palette_tokens():
    """Every STATUS color must come from the palette — no orphan hex."""
    palette_values = set(PALETTE.values())
    for word, color in STATUS.items():
        assert color in palette_values, f"{word}: {color!r} not in PALETTE"


def test_symbols_present():
    required = {
        "pass",
        "fail",
        "active",
        "cta",
        "tree_branch",
        "rule_heavy",
        "rule_light",
    }
    assert required <= SYMBOLS.keys()


# ---------------------------------------------------------------------------
# The "no raw hex outside tokens.py" invariant.
# ---------------------------------------------------------------------------


_HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}\b")
_ALLOWED_HEX_FILES = {
    "sponsio/render/tokens.py",  # the source of truth
}


def _walk_render_files() -> list[Path]:
    root = Path(__file__).parent.parent / "sponsio" / "render"
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def test_no_raw_hex_outside_tokens():
    """Any hex literal in render/*.py except tokens.py is a regression."""
    repo_root = Path(__file__).parent.parent
    offenders: list[str] = []
    for path in _walk_render_files():
        rel = path.relative_to(repo_root).as_posix()
        if rel in _ALLOWED_HEX_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        for match in _HEX_RE.finditer(text):
            # Allow hex inside docstring example blocks (rare, but
            # legitimate when documenting expected output).
            line = text[: match.start()].count("\n")
            offenders.append(f"{rel}:{line + 1}  {match.group()}")
    assert not offenders, "raw hex outside tokens.py:\n  " + "\n  ".join(offenders)


# ---------------------------------------------------------------------------
# pick_format — environment-aware routing.
# ---------------------------------------------------------------------------


def test_pick_format_explicit_choices_pass_through(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    for choice in ("rich", "markdown", "html", "json", "plain"):
        assert pick_format(choice, isatty=True) == choice


def test_pick_format_md_aliases_to_markdown(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    assert pick_format("md", isatty=True) == "markdown"


def test_pick_format_auto_picks_rich_on_tty(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("CONTINUOUS_INTEGRATION", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("BUILDKITE", raising=False)
    assert pick_format("auto", isatty=True) == "rich"


def test_pick_format_auto_picks_markdown_off_tty(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert pick_format("auto", isatty=False) == "markdown"


def test_pick_format_auto_picks_markdown_in_ci(monkeypatch):
    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert pick_format("auto", isatty=True) == "markdown"


def test_pick_format_auto_respects_no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("CI", raising=False)
    assert pick_format("auto", isatty=True) == "plain"


def test_pick_format_unknown_raises(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(ValueError, match="unknown format"):
        pick_format("unknown")

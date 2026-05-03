"""Single source of truth for colors, symbols, and styles.

Every other module in ``sponsio/render/`` imports from here. Adding a
new color or symbol means adding a token here with a usage rationale —
not sprinkling raw hex codes through the codebase.

The palette and service colors are taken from the v1 CLI redesign
spec; the values are also referenced by tests that enforce the "no
raw hex outside tokens.py" invariant.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Semantic palette — what each color *means*, not what it is.
# ---------------------------------------------------------------------------

PALETTE: dict[str, str] = {
    "brand": "#7DD3FC",  # cyan-300   — banner titles, CTA arrow
    "success": "#86EFAC",  # green-300  — ✓, ACTIVE, PASS
    "violation": "#FCA5A5",  # red-300    — ✗, BLOCKED, VIOLATED
    "warning": "#FCD34D",  # amber-300  — soft fail, watchlist
    "active": "#C4B5FD",  # violet-300 — contract state transitions
    "metadata": "#64748B",  # slate-500  — timestamps, latencies, hints
    "muted": "#94A3B8",  # slate-400  — fallback service label, dim text
    "rule": "#475569",  # slate-600  — banner & divider lines
    "fg": "#E2E8F0",  # slate-200  — default foreground
}

# ---------------------------------------------------------------------------
# Service brand colors — capped to ~30% saturation so labels never
# overpower verdicts. Unknown services fall back to ``muted``.
# ---------------------------------------------------------------------------

SERVICE_COLORS: dict[str, str] = {
    # LLM providers
    "openai": "#10A37F",
    "anthropic": "#D97757",
    "gemini": "#4285F4",
    "mistral": "#FA520F",
    # Databases
    "postgres": "#336791",
    "mysql": "#00758F",
    "mongodb": "#47A248",
    "redis": "#DC382D",
    # Code & comms
    "github": "#8B949E",
    "gitlab": "#FC6D26",
    "slack": "#611F69",
    "gmail": "#EA4335",
    # Cloud
    "aws": "#FF9900",
    "gcp": "#4285F4",
    "azure": "#0078D4",
    # Local / protocol — use the muted token directly so re-tinting the
    # palette automatically re-tints these.
    "fs": PALETTE["muted"],
    "shell": PALETTE["muted"],
    "mcp": PALETTE["muted"],
    "http": PALETTE["muted"],
    # Business-domain services (matched by ``_TOOL_KEYWORD_TO_SERVICE``
    # in derive.py — covers vendor / payment / mail / hitl / ops tools
    # that the per-provider prefix table can't enumerate).
    "payments": "#D32F2F",  # red — high-risk financial actions
    "banking": "#D32F2F",
    "vendor": "#1976D2",  # blue
    "mail": "#7B1FA2",  # magenta
    "hitl": "#F57C00",  # orange — human in the loop
    "ops": "#0097A7",  # teal
}


def service_color(name: str) -> str:
    """Return the brand color for ``name``, or the muted fallback."""
    return SERVICE_COLORS.get(name, PALETTE["muted"])


# ---------------------------------------------------------------------------
# Symbol vocabulary — see spec §3.1. Use ONLY these.
# ---------------------------------------------------------------------------

SYMBOLS: dict[str, str] = {
    "pass": "✓",
    "fail": "✗",
    "active": "⚙",
    "cta": "→",
    "tree_branch": "├─",
    "tree_continue": "│",
    "tree_end": "└─",
    "rule_heavy": "━",
    "rule_light": "─",
    # Brand mark — the half-filled circle pair from the original
    # `print_banner`. Sits between the heavy rule and the brand name.
    "logo": "◒◓",
}


# ---------------------------------------------------------------------------
# Verdict status words — see spec §3.2. Always uppercase, always one of
# these. Add a new word here before using it.
# ---------------------------------------------------------------------------

STATUS = {
    "READY": PALETTE["success"],
    "ACTIVE": PALETTE["success"],
    "PASS": PALETTE["success"],
    "BLOCKED": PALETTE["violation"],
    "WARN": PALETTE["warning"],
    "EXPIRED": PALETTE["metadata"],
}


# ---------------------------------------------------------------------------
# SVG export theme — applied when Console.save_svg() is called.
# ---------------------------------------------------------------------------

SVG_THEME = {
    "background": "#0F172A",  # slate-900
    "foreground": PALETTE["fg"],
}

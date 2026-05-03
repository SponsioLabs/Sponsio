"""View-layer field derivation.

The renderer often wants fields that don't exist on Sponsio's domain
model — e.g. "what service does this tool belong to" or "render the
args dict as a one-line summary". Computing these in the renderer
keeps view concerns out of the domain model; only promote a field to
``sponsio.models`` when something *other* than rendering needs it.
"""

from __future__ import annotations

import re
from typing import Any

from sponsio.render.tokens import SERVICE_COLORS

# ---------------------------------------------------------------------------
# Tool → service mapping.
# ---------------------------------------------------------------------------

# Tool-name -> transport label. The session-view's ``service`` column
# shows the **transport** the tool uses to actually run — one of
# ``function`` / ``mcp`` / ``shell`` / ``http``. Other axes (resource,
# business domain) belong in additional columns; folding them into
# ``service`` makes the label semantics inconsistent across scenarios.
#
# Default for anything not on the table is ``function`` — the
# overwhelmingly common case in modern agent SDKs (OpenAI, Anthropic,
# Vercel AI SDK, Claude Agent SDK, LangChain) is in-process function-
# call dispatch from a structured ``tool_use`` block.
_TOOL_PREFIX_TO_SERVICE: list[tuple[str, str]] = [
    # Shell exec — runtime spawns a subprocess.
    ("bash", "shell"),
    ("shell.", "shell"),
    ("run_tests", "shell"),
    ("execute_command", "shell"),
    # Model Context Protocol — runtime speaks JSON-RPC to a separate
    # MCP server (stdio or HTTP+SSE).
    ("user_instruction", "mcp"),
    ("user_message", "mcp"),
    ("mcp.", "mcp"),
    ("mcp__", "mcp"),
    # Raw HTTP fetch — thin wrapper around the network stack rather
    # than a typed function-call handler.
    ("http.", "http"),
    ("fetch", "http"),
    ("web_fetch", "http"),
    ("web_search", "http"),
]


def service_for_tool(tool: str | None) -> str:
    """Infer the transport label for a tool name.

    Returns one of ``func`` / ``mcp`` / ``shell`` / ``http``.
    Default is ``func`` — short for in-process function-call dispatch
    from a structured tool_use block (the modal SDK behaviour).
    """
    if not tool:
        return "unknown"
    lowered = tool.lower()
    for prefix, transport in _TOOL_PREFIX_TO_SERVICE:
        if lowered.startswith(prefix):
            return transport
    return "func"


def has_known_service(tool: str | None) -> bool:
    """True if ``service_for_tool`` would return a colored brand."""
    return service_for_tool(tool) in SERVICE_COLORS


# ---------------------------------------------------------------------------
# Args summary.
# ---------------------------------------------------------------------------

_TRUNCATE_DEFAULT = 60


def args_summary(args: Any, max_len: int = _TRUNCATE_DEFAULT) -> str:
    """Render ``args`` as a one-line summary for a trace event row.

    Heuristics:
        * dict       → ``key1=val1 key2=val2`` (longest values truncated)
        * list/tuple → comma-joined repr-ish
        * str        → quoted, truncated
        * None       → ``""``
        * other      → ``str(args)``, truncated

    The output never contains newlines — callers pad it onto a single
    terminal line.
    """
    if args is None:
        return ""
    if isinstance(args, dict):
        parts: list[str] = []
        for k, v in args.items():
            v_str = _flatten(v)
            if len(v_str) > max_len:
                v_str = v_str[: max_len - 1] + "…"
            parts.append(f"{k}={v_str}")
        return " ".join(parts)
    if isinstance(args, (list, tuple)):
        return _truncate(", ".join(_flatten(x) for x in args), max_len)
    if isinstance(args, str):
        return _truncate(f'"{args}"', max_len)
    return _truncate(str(args), max_len)


def _flatten(v: Any) -> str:
    """Render ``v`` as a single-line string with newlines normalised."""
    if isinstance(v, str):
        return v.replace("\n", " ").strip()
    if v is None:
        return ""
    return str(v)


def _truncate(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


# ---------------------------------------------------------------------------
# Time / latency helpers.
# ---------------------------------------------------------------------------


def relative_time(start_ts: float, ts: float) -> tuple[int, int]:
    """Return ``(seconds, milliseconds_in_second)`` since ``start_ts``.

    Used to format the leftmost timestamp column in trace output, e.g.
    ``00.380``. Negative input clamps to zero (a clock-skew safety net).
    """
    delta = max(0.0, ts - start_ts)
    seconds = int(delta)
    millis = int(round((delta - seconds) * 1000))
    if millis == 1000:  # Float-rounding edge case.
        seconds += 1
        millis = 0
    return seconds, millis


def format_relative_time(start_ts: float, ts: float) -> str:
    """Return a 6-char relative timestamp.

    ``SS.mmm`` for sessions under 100 seconds (the common case; matches
    the spec mockup of ``00.380``). For longer sessions falls back to
    ``MMM:SS`` — drops millisecond precision but stays 6 chars wide so
    column alignment never breaks.
    """
    seconds, millis = relative_time(start_ts, ts)
    if seconds < 100:
        return f"{seconds:02d}.{millis:03d}"
    minutes = seconds // 60
    secs_in_min = seconds % 60
    return f"{minutes:03d}:{secs_in_min:02d}"


def format_latency_ms(ms: float | int | None) -> str:
    """``+<n>ms`` for the event latency column.

    Sub-millisecond durations format as ``+<n>µs`` so a fast demo
    replay (where most spans complete in <1 ms) doesn't render as a
    column of misleading ``+0ms`` rows.
    """
    if ms is None:
        return ""
    if 0 < ms < 1:
        return f"+{int(round(ms * 1000))}µs"
    return f"+{int(ms)}ms"


def format_latency_us(us: float | int | None) -> str:
    """Right-padded ``<n>µs`` for sub-millisecond contract checks."""
    if us is None:
        return ""
    if us >= 1000:
        return f"{us / 1000:.1f}ms"
    return f"{int(us)}µs"


# ---------------------------------------------------------------------------
# Short identifiers — derive a stable display ID without changing storage.
# ---------------------------------------------------------------------------


def short_session_id(filename_stem: str, prefix: str = "sess") -> str:
    """Derive a stable ``sess_<8hex>`` from a session log filename stem.

    Sponsio's on-disk format is ``<YYYYMMDD_HHMMSS>_<pid>``; that's
    great for sorting but ugly for banners. We hash the stem to get a
    short, stable display ID.
    """
    import hashlib

    h = hashlib.blake2b(filename_stem.encode("utf-8"), digest_size=4).hexdigest()
    return f"{prefix}_{h}"


_CONSTRAINT_ALIAS_RE = re.compile(r"[^a-z0-9_-]+")


def short_contract_alias(name: str, index: int, *, prefix: str = "C") -> str:
    """``C1``-style display alias, kept alongside the real contract name.

    Sponsio contracts have meaningful string names; promoting an opaque
    numeric ID into the domain model would be a regression. The alias
    is purely for layout alignment in banners and tree views, matching
    the v1 CLI mockup style.
    """
    return f"{prefix}{index + 1}"

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

# Ordered: more specific prefixes win. The last entry per service is a
# catch-all keyword search performed only if no prefix matches.
_TOOL_PREFIX_TO_SERVICE: list[tuple[str, str]] = [
    ("execute_sql", "postgres"),
    ("query_sql", "postgres"),
    ("postgres.", "postgres"),
    ("psql.", "postgres"),
    ("mysql.", "mysql"),
    ("mongo.", "mongodb"),
    ("redis.", "redis"),
    ("github.", "github"),
    ("gh.", "github"),
    ("gitlab.", "gitlab"),
    ("slack.", "slack"),
    ("gmail.", "gmail"),
    ("aws.", "aws"),
    ("s3.", "aws"),
    ("ec2.", "aws"),
    ("gcp.", "gcp"),
    ("azure.", "azure"),
    ("openai.", "openai"),
    ("anthropic.", "anthropic"),
    ("gemini.", "gemini"),
    ("mistral.", "mistral"),
    ("read_file", "fs"),
    ("write_file", "fs"),
    ("edit_file", "fs"),
    ("list_dir", "fs"),
    ("file.", "fs"),
    ("fs.", "fs"),
    ("bash", "shell"),
    ("shell.", "shell"),
    ("run_tests", "shell"),
    ("execute_command", "shell"),
    ("mcp.", "mcp"),
    ("mcp__", "mcp"),
    ("http.", "http"),
    ("fetch", "http"),
    ("web_fetch", "http"),
    ("web_search", "http"),
]


def service_for_tool(tool: str | None) -> str:
    """Infer the service label for a tool name. Falls back to ``"unknown"``."""
    if not tool:
        return "unknown"
    lowered = tool.lower()
    for prefix, service in _TOOL_PREFIX_TO_SERVICE:
        if lowered.startswith(prefix):
            return service
    # Last-chance keyword search for SQL operations buried in args:
    # e.g. ``run("DROP TABLE foo")`` looks like shell at first.
    if "sql" in lowered:
        return "postgres"
    return "unknown"


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
    """Right-padded ``+<n>ms`` for the event latency column."""
    if ms is None:
        return ""
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


def short_contract_alias(name: str, index: int) -> str:
    """``#1``-style display alias, kept alongside the real contract name.

    The spec wanted ``C1`` / ``C2`` but Sponsio's contracts have
    meaningful string names — promoting an opaque numeric ID into the
    domain model would be a regression. The alias is purely for layout
    alignment in banners.
    """
    return f"#{index + 1}"

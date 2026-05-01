"""Sponsio's terminal rendering layer.

Public surface: ``pick_format(...)`` decides whether to use the Rich
path, the existing markdown path, or raw JSON, given the user's
``--format`` flag and the actual stdout context (TTY? CI? piped?).

The Rich path lives here. The markdown / html / json file-renderers
live in :mod:`sponsio.reporting.renderer` and stay untouched — they're
optimised for share/paste/CI consumption, not interactive terminals.
"""

from __future__ import annotations

import os
import sys


def pick_format(requested: str, *, isatty: bool | None = None) -> str:
    """Resolve a ``--format`` choice against the runtime environment.

    Args:
        requested: The user's flag value (``"auto"`` / ``"rich"`` /
            ``"markdown"`` / ``"md"`` / ``"html"`` / ``"json"`` /
            ``"plain"``).
        isatty: Override stdout detection (tests pass ``True`` /
            ``False`` to assert routing without monkeypatching).

    Returns:
        The concrete renderer name to dispatch on. Always one of
        ``"rich"``, ``"markdown"``, ``"html"``, ``"json"``, or
        ``"plain"``.

    Auto-resolution rules:
        * ``CI=true`` env var       → ``markdown``  (fits PR comments)
        * stdout is not a tty       → ``markdown``  (piped output)
        * ``NO_COLOR`` env set      → ``plain``
        * otherwise                 → ``rich``
    """
    requested = requested.lower()
    if requested == "md":
        requested = "markdown"
    if requested in {"rich", "markdown", "html", "json", "plain"}:
        return requested
    if requested != "auto":
        raise ValueError(f"unknown format: {requested!r}")

    if os.environ.get("NO_COLOR"):
        return "plain"
    if _is_ci_environment():
        return "markdown"
    if isatty is None:
        isatty = sys.stdout.isatty()
    if not isatty:
        return "markdown"
    return "rich"


def _is_ci_environment() -> bool:
    """Detect common CI runners. Conservative — only checks the few
    that actually pipe output without ``isatty=False``."""
    for var in ("CI", "CONTINUOUS_INTEGRATION", "GITHUB_ACTIONS", "BUILDKITE"):
        v = os.environ.get(var)
        if v and v.lower() not in {"", "0", "false", "no"}:
            return True
    return False


__all__ = ["pick_format"]

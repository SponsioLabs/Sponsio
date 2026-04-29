"""Unified host registry — one place to declare every IDE/agent runtime
Sponsio plugs into via shell hooks (Cursor, Claude Code, OpenClaw, …).

Each host is described by a :class:`HookHost` instance with the data
needed to (a) install Sponsio as a hook handler, (b) translate that
host's hook payload into Sponsio's ``evaluate_event`` shape, and (c)
render a guard outcome back into the host's reply schema.

The CLI wraps this registry behind ``sponsio host install``,
``sponsio host guard``, ``sponsio host list``, and
``sponsio host uninstall`` — see :mod:`sponsio.cli`.

Existing per-host commands (``sponsio cursor install-hooks``,
``sponsio plugin init``, ``sponsio plugin guard``) still work; the
``host`` group is purely additive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# Host capability descriptor
# ---------------------------------------------------------------------------


@dataclass
class HookHost:
    """Static description of one IDE/agent host Sponsio can plug into."""

    name: str
    """Public id, used in ``sponsio host install <name>``."""

    description: str
    """One-line summary surfaced by ``sponsio host list``."""

    config_path_user: Path
    """Path to the host's user-scope hook config file."""

    config_path_project: Path | None
    """Path to the host's project-scope hook config (relative to cwd),
    or ``None`` if the host has no project-scope concept."""

    detect_paths: tuple[Path, ...]
    """Paths whose existence implies the host is installed on this
    machine.  ``sponsio host install auto`` iterates the registry and
    installs any host where at least one of these exists."""

    install_fn: Callable[..., "HostInstallResult"]
    """``install(host: HookHost, scope: str, fail_closed: bool, force: bool,
    binary: str) -> HostInstallResult``.  Writes/merges the host's hook
    config to point at ``binary <runtime_command>``."""

    uninstall_fn: Callable[..., "HostUninstallResult"]
    """``uninstall(host: HookHost, scope: str) -> HostUninstallResult``.
    Removes Sponsio entries from the host's hook config; leaves any
    user-authored hooks untouched."""

    runtime_fn: Callable[..., int]
    """``run(host: HookHost, hook_event: str | None, stdin_text: str | None) -> int``.
    Called by ``sponsio host guard <name>``; reads stdin, evaluates,
    writes the host-shaped reply, returns process exit code."""

    runtime_events: tuple[str, ...] = ()
    """Hook event names this host distinguishes (Cursor: preToolUse,
    beforeShellExecution, …).  Empty tuple = single event protocol
    (Claude Code's PreToolUse/PostToolUse is signalled in the JSON
    body, not via a separate ``--event`` argument)."""

    status_fn: Callable[..., dict] | None = None
    """Optional ``status(host: HookHost) -> dict`` returning a
    structured report of what Sponsio has deployed for this host.
    ``sponsio host status <name>`` calls this and renders the result.
    Hosts without a ``status_fn`` fall back to a generic file-presence
    check on ``config_path_user``.  Each report key is conventionally
    ``{"ok": bool, "detail": str}``; one reserved key ``libraries``
    holds a list of contract-library summaries when applicable."""

    trace_fn: Callable[..., object] | None = None
    """Optional ``trace(host, *, follow: bool, container: str | None)
    -> Iterator[tuple[level, line]]`` — yields a human-readable
    stream of agent activity (tool calls, results, sponsio blocks).
    ``sponsio host trace <name>`` calls this and prints each yielded
    line with level-appropriate colour.  Hosts that don't expose a
    structured session log have no trace adapter today."""

    extras: dict = field(default_factory=dict)
    """Host-specific options that the install/uninstall/runtime
    functions read.  Keeps the dataclass closed-shape while allowing
    per-host configuration (e.g. Claude Code's ``settings.json`` key
    naming)."""


@dataclass
class HostInstallResult:
    """Outcome of a single host install."""

    host: str
    config_path: Path
    written: bool
    note: str = ""


@dataclass
class HostUninstallResult:
    """Outcome of a single host uninstall."""

    host: str
    config_path: Path
    removed_entries: int
    note: str = ""


# ---------------------------------------------------------------------------
# Registry — populated by import side-effect from the per-host modules.
# ---------------------------------------------------------------------------


_REGISTRY: dict[str, HookHost] = {}


def register(host: HookHost) -> None:
    """Add ``host`` to the registry.  Last write wins on re-register."""
    _REGISTRY[host.name] = host


def get(name: str) -> HookHost:
    """Look up a registered host by id; raises :class:`KeyError` if
    unknown.  Use :func:`available` for a friendly list."""
    if name not in _REGISTRY:
        raise KeyError(
            f"unknown host {name!r}; available: {', '.join(sorted(_REGISTRY)) or '(none)'}"
        )
    return _REGISTRY[name]


def available() -> list[HookHost]:
    """All registered hosts in insertion order."""
    return list(_REGISTRY.values())


def detect_installed() -> list[HookHost]:
    """Hosts whose ``detect_paths`` resolve on this machine — used by
    ``sponsio host install auto`` to pick a sensible default set."""
    found = []
    for h in _REGISTRY.values():
        for p in h.detect_paths:
            if p.exists():
                found.append(h)
                break
    return found


# ---------------------------------------------------------------------------
# Per-host specs
# ---------------------------------------------------------------------------
#
# The actual install / runtime callables live in the existing per-host
# modules (sponsio.integrations.cursor, sponsio.integrations.claude_code,
# sponsio.integrations.openclaw_install) so this file stays a registry,
# not a mega-module.  We import lazily inside the closures to keep the
# CLI cold-start cheap.


def _cursor_install(*args, **kwargs):
    from sponsio.integrations.cursor_install import install as _impl

    return _impl(*args, **kwargs)


def _cursor_uninstall(*args, **kwargs):
    from sponsio.integrations.cursor_install import uninstall as _impl

    return _impl(*args, **kwargs)


def _cursor_runtime(host, hook_event, stdin_text):
    from sponsio.integrations.cursor import run_cursor_stdin

    # Cursor's CLI surface defaults to ``preToolUse`` — match that.
    return run_cursor_stdin(hook_event or "preToolUse", stdin_text)


def _claude_install(*args, **kwargs):
    from sponsio.integrations.claude_code_install import install as _impl

    return _impl(*args, **kwargs)


def _claude_uninstall(*args, **kwargs):
    from sponsio.integrations.claude_code_install import uninstall as _impl

    return _impl(*args, **kwargs)


def _claude_runtime(host, hook_event, stdin_text):
    # Claude Code's hook protocol is single-stream: the event name is
    # carried inside the JSON payload (``hook_event_name`` field), not
    # as a separate ``--event`` argument.  Just route stdin straight
    # through.
    from sponsio.guard_stdin import run_stdin

    return run_stdin(stdin_text)


def _openclaw_install(*args, **kwargs):
    from sponsio.integrations.openclaw_install import install as _impl

    return _impl(*args, **kwargs)


def _openclaw_uninstall(*args, **kwargs):
    from sponsio.integrations.openclaw_install import uninstall as _impl

    return _impl(*args, **kwargs)


def _openclaw_runtime(host, hook_event, stdin_text):
    # OpenClaw uses the same stdin/stdout protocol as Claude Code; the
    # ``host`` field on the inbound JSON drives library routing.
    from sponsio.guard_stdin import run_stdin

    return run_stdin(stdin_text)


def _openclaw_status(host):
    from sponsio.integrations.openclaw_install import status as _impl

    return _impl(host)


def _openclaw_trace(host, **kwargs):
    from sponsio.integrations.openclaw_install import trace as _impl

    return _impl(host, **kwargs)


# Order matters: ``sponsio host list`` prints in registry order.
register(
    HookHost(
        name="cursor",
        description="Cursor IDE 1.7+ — deny-capable preToolUse / before* hooks.",
        config_path_user=Path.home() / ".cursor" / "hooks.json",
        config_path_project=Path(".cursor") / "hooks.json",
        detect_paths=(Path.home() / ".cursor",),
        install_fn=_cursor_install,
        uninstall_fn=_cursor_uninstall,
        runtime_fn=_cursor_runtime,
        runtime_events=(
            "preToolUse",
            "beforeShellExecution",
            "beforeMCPExecution",
            "beforeReadFile",
            "beforeTabFileRead",
            "beforeSubmitPrompt",
            "postToolUse",
            "afterShellExecution",
            "afterMCPExecution",
            "afterFileEdit",
            "subagentStart",
            "subagentStop",
        ),
    )
)


register(
    HookHost(
        name="claude-code",
        description="Claude Code CLI — user-level PreToolUse / PostToolUse hooks via settings.json.",
        config_path_user=Path.home() / ".claude" / "settings.json",
        config_path_project=Path(".claude") / "settings.json",
        detect_paths=(
            Path.home() / ".claude",
            Path.home() / ".claude" / "settings.json",
        ),
        install_fn=_claude_install,
        uninstall_fn=_claude_uninstall,
        runtime_fn=_claude_runtime,
        # Claude Code carries the event in the JSON body, not via CLI.
        runtime_events=(),
    )
)


register(
    HookHost(
        name="openclaw",
        description="OpenClaw — agent runtime that loads ``openclaw.plugin.json`` plugins.",
        config_path_user=Path.home()
        / ".sponsio"
        / "plugins"
        / "_host_openclaw"
        / "sponsio.yaml",
        config_path_project=None,
        detect_paths=(Path.home() / ".openclaw",),
        install_fn=_openclaw_install,
        uninstall_fn=_openclaw_uninstall,
        runtime_fn=_openclaw_runtime,
        runtime_events=(),
        status_fn=_openclaw_status,
        trace_fn=_openclaw_trace,
    )
)

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

    user_config_fn: Callable[[], Path]
    """Returns the host's user-scope hook config file. Resolved on every
    access (see :attr:`config_path_user`), never at import: the path
    depends on ``HOME``, and a value fixed when this module loaded kept
    pointing at the real ``~/.claude/settings.json`` after a caller
    (a test, a sandboxed run) had moved ``HOME`` somewhere else."""

    config_path_project: Path | None
    """Path to the host's project-scope hook config (relative to cwd),
    or ``None`` if the host has no project-scope concept."""

    detect_fn: Callable[[], tuple[Path, ...]]
    """Returns the paths whose existence implies the host is installed
    on this machine (see :attr:`detect_paths`)."""

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

    host_config_fn: Callable[[str], Path] | None = None
    """``host_config(scope) -> Path``: the host-owned file the installer
    edits for ``scope`` (``settings.json``, ``hooks.json``,
    ``openclaw.json``). Every change to it is backed up first (see
    :mod:`sponsio.integrations.config_backup`); ``sponsio host
    uninstall`` uses this to offer the most recent backup back."""

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

    @property
    def config_path_user(self) -> Path:
        """The host's user-scope hook config file, under the current
        ``HOME``."""
        return self.user_config_fn()

    @property
    def detect_paths(self) -> tuple[Path, ...]:
        """Paths whose existence implies the host is installed on this
        machine.  ``sponsio host install auto`` iterates the registry and
        installs any host where at least one of these exists."""
        return self.detect_fn()


@dataclass
class HostInstallResult:
    """Outcome of a single host install."""

    host: str
    config_path: Path
    written: bool
    note: str = ""
    backup: Path | None = None
    """Copy of the host config as it was before this install changed it."""


@dataclass
class HostUninstallResult:
    """Outcome of a single host uninstall."""

    host: str
    config_path: Path
    removed_entries: int
    note: str = ""
    backup: Path | None = None
    """Copy of the host config as it was before this uninstall changed it."""


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
# The command a hook runs
# ---------------------------------------------------------------------------


def resolve_binary(override: str | None = None) -> str:
    """The absolute path of the ``sponsio`` the hook should invoke.

    Hosts launch hook subprocesses from a bare PATH (launchd's, or the
    IDE's), so the command has to be absolute, and it has to be *this*
    install: the one that ran ``sponsio host install``. Resolving
    ``sys.argv[0]`` against the working directory is what makes that
    hold for a relative invocation (``./venv/bin/sponsio host install``).
    An earlier version only accepted an already-absolute ``argv[0]`` and
    otherwise took whatever ``sponsio`` was first on PATH, which on a
    machine with a second (older, or broken) install wrote that one into
    the hook. Claude Code treats a hook that exits non-zero as a
    non-blocking error and runs the tool, so a hook pointing at a broken
    binary is the guard silently off.

    When the process was started as ``python -m sponsio.cli`` there is
    no console script to point at; the interpreter plus module is used.
    PATH is the last resort, and only when nothing else resolved.
    """
    import shutil
    import sys

    if override:
        return override
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0:
        candidate = Path(argv0)
        if candidate.suffix == ".py" or argv0 in ("-m", "-c"):
            return f"{sys.executable} -m sponsio.cli"
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            resolved = None
        if resolved is not None and resolved.is_file():
            return str(resolved)
    return shutil.which("sponsio") or "sponsio"


DEFAULT_SHIM_DEADLINE_S = 2.0
"""The shim's wall-clock budget, written into the hook command."""

CLAUDE_CODE_HOOK_TIMEOUT_S = 10
"""``timeout`` on the Claude Code hook entries: well above the shim's
own deadline (2 s, plus 1.5 s to start a daemon and one retry), so the
shim always answers before the host gives up on it."""


def shim_command(
    host: str,
    *,
    event: str | None = None,
    mode: str = "enforce",
    socket_path: Path | None = None,
    plugin_root: Path | None = None,
    state_dir: Path | None = None,
    python: str | None = None,
    sponsio: str | None = None,
    deadline_s: float = DEFAULT_SHIM_DEADLINE_S,
) -> str:
    """The hook command: the shim under this interpreter, fully pinned.

    ``<python> -I -S <shim> <host> [--event E] --socket S --plugin-root R
    --state-dir D --deadline N [--mode observe] [--sponsio CMD]``.

    Every value is absolute and fixed at install time. The shim reads
    no environment variable, so an ``env`` block in the host's settings
    (which the agent under guard can edit) changes nothing about where
    the hook connects, which libraries a daemon it starts serves, or
    the mode. ``python`` defaults to the installing interpreter: ``-I -S``
    skips site and user-site, which is most of interpreter start-up,
    and the daemon the shim may start runs the same interpreter without
    those flags, so it imports ``sponsio`` from this install.

    ``sponsio`` overrides how the shim starts a daemon (``--binary`` on
    ``host install``); by default it is ``<python> -m sponsio.cli``.
    """
    import shlex
    import sys

    from sponsio.shim import shim_path

    interpreter = python or sys.executable
    parts = [shlex.quote(interpreter), "-I", "-S", shlex.quote(str(shim_path())), host]
    if event:
        parts += ["--event", event]
    sock = socket_path if socket_path is not None else _default_socket_path()
    parts += ["--socket", shlex.quote(str(sock))]
    root = plugin_root if plugin_root is not None else _default_plugin_root()
    parts += ["--plugin-root", shlex.quote(str(root))]
    state = state_dir if state_dir is not None else _default_state_dir()
    parts += ["--state-dir", shlex.quote(str(state))]
    parts += ["--deadline", f"{deadline_s:g}"]
    if mode == "observe":
        parts += ["--mode", "observe"]
    if sponsio:
        parts += ["--sponsio", shlex.quote(sponsio)]
    return " ".join(parts)


def socket_path_limit() -> int:
    """Longest AF_UNIX path this platform binds, in bytes, without the NUL."""
    import sys

    return 103 if sys.platform == "darwin" else 107


def socket_path_problem(path: Path | str) -> str | None:
    """Why a hook pinned to ``path`` could never reach a daemon, or ``None``.

    A shim hook denies every call it cannot forward, so writing one whose
    socket can never be bound turns the host's tools off. The installers
    refuse instead, and ``sponsio doctor`` reports existing ones. The
    daemon binds the admin socket beside it too (``<stem>.admin<suffix>``,
    six bytes longer) and does not start if either cannot be bound.
    """
    import os

    from sponsio.daemon.server import admin_socket_for

    for candidate in (Path(path), admin_socket_for(path)):
        size = len(os.fsencode(str(candidate)))
        if size > socket_path_limit():
            return (
                f"socket path is {size} bytes, longer than this platform can bind "
                f"({socket_path_limit()}): {candidate}"
            )
    return None


def is_shim_command(command: str) -> bool:
    """Does this hook command run the Sponsio shim?"""
    return "sponsio_shim.py" in command


def is_sponsio_hook_command(command: str, host: str) -> bool:
    """Any generation of Sponsio hook command for ``host``: the shim, the
    ``host guard`` entry, or the legacy ``plugin guard`` / ``cursor
    guard`` entries. The installers replace these on reinstall."""
    if is_shim_command(command):
        return True
    if f"host guard {host}" in command:
        return True
    if host == "claude-code" and "plugin guard" in command:
        return True
    if host == "cursor" and "cursor guard --event" in command:
        return True
    return False


def _default_socket_path() -> Path:
    from sponsio.daemon.client import default_socket_path

    return default_socket_path().expanduser().resolve()


def _default_plugin_root() -> Path:
    from sponsio.guard_stdin import library_root

    return library_root().expanduser().resolve()


def _default_state_dir() -> Path:
    from sponsio.daemon.guard_service import default_state_dir

    return default_state_dir().expanduser().resolve()


def hook_command(binary: str, *args: str, mode: str = "enforce") -> str:
    """``<binary> host guard <host> ...``, in the mode the operator chose.

    The legacy hook command (the installers now write
    :func:`shim_command`). The entry point decides enforce / observe
    from ``SPONSIO_GUARD_MODE`` (then ``SPONSIO_MODE``, then
    ``enforce``), never from the host library's ``defaults.mode``: a
    hook that read the library could be switched off by editing a file
    the agent can reach. So ``--mode observe`` is carried on the command
    line itself, through ``env``, which works whether the host runs the
    command through a shell or exec()s it directly. ``enforce`` is the
    hook's own default and adds nothing.
    """
    cmd = " ".join((binary, *args))
    if mode == "observe":
        return f"env SPONSIO_GUARD_MODE=observe {cmd}"
    return cmd


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
    # as a separate ``--event`` argument.  The payload carries no
    # ``host`` field either, so the hook names the host itself: the
    # installer wrote ``host guard claude-code``, and that is what
    # routes the call to ``_host_claude_code`` (where ``--strict`` and
    # the per-host rules live) rather than the legacy ``_host`` bucket.
    from sponsio.guard_stdin import run_stdin

    return run_stdin(
        stdin_text, default_host=getattr(host, "name", None) or "claude-code"
    )


def _openclaw_install(*args, **kwargs):
    from sponsio.integrations.openclaw_install import install as _impl

    return _impl(*args, **kwargs)


def _openclaw_uninstall(*args, **kwargs):
    from sponsio.integrations.openclaw_install import uninstall as _impl

    return _impl(*args, **kwargs)


def _openclaw_runtime(host, hook_event, stdin_text):
    # OpenClaw uses the same stdin/stdout protocol as Claude Code; the
    # ``host`` field on the inbound JSON drives library routing; when a
    # payload omits it, the host the hook was installed for applies.
    from sponsio.guard_stdin import run_stdin

    return run_stdin(stdin_text, default_host=getattr(host, "name", None) or "openclaw")


def _openclaw_json() -> Path:
    from sponsio.integrations.openclaw_install import _openclaw_json_path

    return _openclaw_json_path()


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
        user_config_fn=lambda: Path.home() / ".cursor" / "hooks.json",
        config_path_project=Path(".cursor") / "hooks.json",
        host_config_fn=lambda scope: (
            (Path.cwd() if scope == "project" else Path.home())
            / ".cursor"
            / "hooks.json"
        ),
        detect_fn=lambda: (Path.home() / ".cursor",),
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
        user_config_fn=lambda: Path.home() / ".claude" / "settings.json",
        config_path_project=Path(".claude") / "settings.json",
        host_config_fn=lambda scope: (
            (Path.cwd() if scope == "project" else Path.home())
            / ".claude"
            / "settings.json"
        ),
        detect_fn=lambda: (
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
        user_config_fn=lambda: (
            Path.home() / ".sponsio" / "plugins" / "_host_openclaw" / "sponsio.yaml"
        ),
        config_path_project=None,
        host_config_fn=lambda scope: _openclaw_json(),
        detect_fn=lambda: (Path.home() / ".openclaw",),
        install_fn=_openclaw_install,
        uninstall_fn=_openclaw_uninstall,
        runtime_fn=_openclaw_runtime,
        runtime_events=(),
        status_fn=_openclaw_status,
        trace_fn=_openclaw_trace,
    )
)

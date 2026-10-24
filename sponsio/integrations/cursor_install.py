"""Cursor IDE install/uninstall — write or merge entries in
``hooks.json`` so Cursor invokes Sponsio for every relevant hook
event.

Surfaced behind ``sponsio host install cursor`` and
``sponsio host uninstall cursor``.  The legacy ``sponsio cursor
install-hooks`` command stays available and goes through the same
code path.
"""

from __future__ import annotations

import json
from pathlib import Path

from sponsio.integrations import config_backup
from sponsio.integrations.hosts import (
    HookHost,
    HostInstallResult,
    HostUninstallResult,
    _default_socket_path,
    is_sponsio_hook_command,
    set_daemon_mode,
    shim_command,
    socket_path_problem,
)

# Events Sponsio wires up.  Keep this in sync with the
# ``runtime_events`` tuple on the cursor :class:`HookHost`.
_INSTALLED_EVENTS_FAILCLOSED = (
    "preToolUse",
    "beforeShellExecution",
    "beforeMCPExecution",
    "beforeReadFile",
)
_INSTALLED_EVENTS_SOFT = (
    "beforeSubmitPrompt",
    "postToolUse",
)


def _ensure_bundled_library(bucket: str) -> None:
    """Bootstrap ``~/.sponsio/plugins/<bucket>/sponsio.yaml`` if it
    isn't already there.

    Each host now owns its own bucket (``_host_cursor``,
    ``_host_cursor_subagent``, ``_host_claude_code``,
    ``_host_claude_code_subagent``, ``_host_openclaw``) so per-IDE
    rules can diverge from the legacy shared ``_host`` library.
    The bundled starter yaml is dropped in on first install only —
    subsequent installs leave the user's edits alone (idempotent)."""
    import os as _os

    from sponsio.plugin.registry import read_bundled

    root_env = _os.environ.get("SPONSIO_PLUGIN_ROOT")
    root = (
        Path(root_env).expanduser()
        if root_env
        else Path.home() / ".sponsio" / "plugins"
    )
    target = root / bucket / "sponsio.yaml"
    if target.exists():
        return
    try:
        text = read_bundled(bucket)
    except (FileNotFoundError, ModuleNotFoundError):
        return
    # Stamped like ``sponsio host install``'s own refresh writes it, so a
    # later upgrade tells the shipped contracts from the user's.
    from sponsio.cli.groups.plugin import _stamp_bundled_source

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_stamp_bundled_source(text, bucket), encoding="utf-8")


def _ensure_subagent_library() -> None:
    """Backward-compat shim — bootstraps the cursor host buckets.

    Pre-host-bucketing this only seeded ``_host_subagent``. Now we
    seed both the main and sub-agent buckets for Cursor; callers
    that imported this private name keep working.
    """
    _ensure_bundled_library("_host_cursor")
    _ensure_bundled_library("_host_cursor_subagent")


def install(
    host: HookHost,
    *,
    scope: str = "user",
    fail_closed: bool = True,
    force: bool = False,
    binary: str | None = None,
    mode: str = "enforce",
) -> HostInstallResult:
    """Write or merge Cursor hook entries pointing at the Sponsio shim.

    Each command is :func:`sponsio.integrations.hosts.shim_command` for
    its event. ``mode`` goes into the daemon's config for Cursor
    (:func:`set_daemon_mode`). ``binary`` is what the shim starts a
    daemon with when none answers.

    Side effect: bootstraps ``_host_subagent`` library if absent, so
    Cursor Task-spawned subagent tool calls (routed there by
    ``cursor.py``) have a contract library to evaluate against."""
    _ensure_subagent_library()
    if scope == "project":
        target = Path.cwd() / ".cursor" / "hooks.json"
    else:
        target = host.config_path_user

    sponsio_hooks: dict[str, list[dict]] = {}
    for event in _INSTALLED_EVENTS_FAILCLOSED:
        sponsio_hooks[event] = [
            {
                "command": shim_command("cursor", event=event, sponsio=binary),
                "failClosed": fail_closed,
            }
        ]
    for event in _INSTALLED_EVENTS_SOFT:
        sponsio_hooks[event] = [
            {"command": shim_command("cursor", event=event, sponsio=binary)}
        ]

    problem = socket_path_problem(_default_socket_path())
    if problem:
        return HostInstallResult(
            host=host.name,
            config_path=target,
            written=False,
            note=(
                f"refusing to install: {problem}. A hook that cannot reach "
                "the daemon denies every tool call. Set SPONSIO_DAEMON_SOCKET "
                "to a shorter path and re-run"
            ),
        )

    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and not force:
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except json.JSONDecodeError:
            return HostInstallResult(
                host=host.name,
                config_path=target,
                written=False,
                note=(
                    f"{target} exists but is not valid JSON — refusing to "
                    "merge.  Re-run with force=True to overwrite, or fix "
                    "the file by hand."
                ),
            )
        merged = dict(existing)
        merged.setdefault("version", 1)
        existing_hooks = (
            merged.get("hooks") if isinstance(merged.get("hooks"), dict) else {}
        )
        for event_name, entries in sponsio_hooks.items():
            keep: list[dict] = []
            for prior in existing_hooks.get(event_name, []) or []:
                if (
                    isinstance(prior, dict)
                    and isinstance(prior.get("command"), str)
                    and is_sponsio_hook_command(prior["command"], "cursor")
                ):
                    continue
                keep.append(prior)
            existing_hooks[event_name] = keep + entries
        merged["hooks"] = existing_hooks
        out = merged
        note = "merged into existing hooks.json"
    else:
        out = {"version": 1, "hooks": sponsio_hooks}
        note = "wrote fresh hooks.json"

    saved = config_backup.write(target, json.dumps(out, indent=2) + "\n")
    note = f"{note}; mode {mode} {set_daemon_mode('cursor', mode)}"
    return HostInstallResult(
        host=host.name,
        config_path=target,
        written=True,
        note=note,
        backup=saved,
    )


def uninstall(host: HookHost, *, scope: str = "user") -> HostUninstallResult:
    """Strip Sponsio hook entries from the cursor ``hooks.json``,
    leaving any non-Sponsio entries untouched."""
    if scope == "project":
        target = Path.cwd() / ".cursor" / "hooks.json"
    else:
        target = host.config_path_user

    if not target.exists():
        return HostUninstallResult(
            host=host.name,
            config_path=target,
            removed_entries=0,
            note="no hooks.json found — nothing to uninstall",
        )

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        return HostUninstallResult(
            host=host.name,
            config_path=target,
            removed_entries=0,
            note="hooks.json is malformed — refusing to edit",
        )

    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    removed = 0
    for event_name, entries in list(hooks.items()):
        if not isinstance(entries, list):
            continue
        kept: list[dict] = []
        for entry in entries:
            cmd = isinstance(entry, dict) and entry.get("command")
            if isinstance(cmd, str) and is_sponsio_hook_command(cmd, "cursor"):
                removed += 1
                continue
            kept.append(entry)
        if kept:
            hooks[event_name] = kept
        else:
            del hooks[event_name]

    if not removed:
        saved = None
        note = "no sponsio entries in hooks.json"
    elif hooks:
        data["hooks"] = hooks
        saved = config_backup.write(target, json.dumps(data, indent=2) + "\n")
        note = f"removed {removed} sponsio entries; kept {len(hooks)} other event(s)"
    else:
        # Whole file was sponsio-only — remove it for cleanliness.
        saved = config_backup.remove(target)
        note = f"removed {removed} sponsio entries; deleted now-empty hooks.json"

    return HostUninstallResult(
        host=host.name,
        config_path=target,
        removed_entries=removed,
        note=note,
        backup=saved,
    )

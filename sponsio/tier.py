"""Which enforcement tier this machine is in, from facts.

A tier is a deployment with a stated guarantee (docs/concepts/
threat-model.md). This module checks what makes each guarantee hold
and reports the tier as the highest one whose every guarantee does:

* Tier 1 (dev): host hooks go through the daemon, which fails closed
  and keeps tamper evidence. The daemon runs as the agent's user.
* Tier 2 (team): the agent's OS user cannot change Sponsio. The daemon
  runs as its own user, the admin socket and rule store are its own,
  the shim and engine are root-owned, the host's hooks come from
  managed settings, and the host sandbox is on.
* Tier 3 (enterprise): the agent reaches tools and the network only
  through Sponsio's gateway and egress proxy.

Every check reads the filesystem or the daemon's socket; none trusts
anything the agent's user could have written to claim a tier.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Guarantee:
    tier: int
    name: str
    ok: bool
    detail: str
    fix: str = ""
    required: bool = True
    """A guarantee that is not required leaves the tier standing when it
    fails, and marks it degraded."""


@dataclass
class TierReport:
    tier: int
    guarantees: list[Guarantee] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    """Per-host remarks, such as a host that stays at Tier 1."""

    def missing(self, tier: int) -> list[Guarantee]:
        return [
            g for g in self.guarantees if g.tier == tier and not g.ok and g.required
        ]

    @property
    def degraded(self) -> list[Guarantee]:
        return [
            g
            for g in self.guarantees
            if 0 < g.tier <= self.tier and not g.ok and not g.required
        ]

    def as_dict(self) -> dict:
        return {
            "tier": self.tier,
            "degraded": bool(self.degraded),
            "guarantees": [g.__dict__ for g in self.guarantees],
            "notes": list(self.notes),
        }


MIN_CLAUDE_CODE = "2.1.295"


def _claude_dir() -> Path:
    if sys.platform == "darwin":
        return Path("/Library/Application Support/ClaudeCode")
    return Path("/etc/claude-code")


def managed_settings_paths() -> tuple[Path, ...]:
    """Claude Code's managed settings files, in the order it merges them:
    ``managed-settings.json``, then ``managed-settings.d/*.json``."""
    base = _claude_dir()
    dropins = sorted((base / "managed-settings.d").glob("*.json"))
    return (base / "managed-settings.json", *dropins)


def mdm_settings_path() -> Path | None:
    """A managed source Claude Code ranks above the files (macOS MDM)."""
    if sys.platform == "darwin":
        return Path("/Library/Managed Preferences/com.anthropic.claudecode.plist")
    return None


def _merged_managed(paths: tuple[Path, ...]) -> tuple[dict, list[Path]]:
    """The files' settings merged in order (later keys win; hook lists
    are concatenated), and which files were read."""
    merged: dict = {}
    read: list[Path] = []
    for p in paths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        read.append(p)
        for key, value in data.items():
            if key == "hooks" and isinstance(value, dict):
                hooks = merged.setdefault("hooks", {})
                for event, entries in value.items():
                    if isinstance(entries, list):
                        hooks.setdefault(event, []).extend(entries)
            else:
                merged[key] = value
    return merged, read


def _version_tuple(v: object) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return ()


def _higher_source_ignores_files(mdm: Path | None) -> str | None:
    """Why Claude Code ignores the managed settings files, or ``None``."""
    if mdm is None or not mdm.exists():
        return None
    try:
        import plistlib

        data = plistlib.loads(mdm.read_bytes())
    except Exception:  # noqa: BLE001 - unreadable profile: assume it wins
        data = {}
    if isinstance(data, dict) and data.get("managedSourcesBehavior") == "merge":
        return None
    return (
        f"an MDM profile ({mdm}) ranks above the managed settings files, so "
        f"Claude Code ignores them"
    )


def _root_owned_readonly(path: Path) -> tuple[bool, str]:
    try:
        st = path.stat()
    except OSError:
        return False, f"{path} is missing"
    if st.st_uid != 0:
        return False, f"{path} is owned by uid {st.st_uid}, not root"
    if os.access(path, os.W_OK) or os.access(path.parent, os.W_OK):
        return False, f"{path} (or its directory) is writable by this user"
    return True, f"{path} is root-owned and read-only to this user"


def assess(
    *,
    hooks_through_daemon: bool,
    socket_path: Path,
    admin_socket_path: Path,
    shim_path: Path,
    engine_dir: Path,
    plugin_root: Path,
    managed_settings: tuple[Path, ...] | None = None,
    uid: int | None = None,
    mdm_settings: Path | None = None,
    cursor_hooks: Path | None = None,
) -> TierReport:
    uid = os.getuid() if uid is None else uid
    g: list[Guarantee] = []

    g.append(
        Guarantee(
            1,
            "Host hooks go through the daemon and fail closed",
            hooks_through_daemon,
            "a hook or plugin forwards to the daemon"
            if hooks_through_daemon
            else "no host hook forwards to the daemon",
            "" if hooks_through_daemon else "run `sponsio host install <host>`",
        )
    )

    try:
        daemon_uid: int | None = socket_path.stat().st_uid
    except OSError:
        daemon_uid = None
    separate = daemon_uid is not None and daemon_uid != uid
    g.append(
        Guarantee(
            2,
            "The daemon runs as its own user",
            separate,
            "no daemon socket at " + str(socket_path)
            if daemon_uid is None
            else f"the daemon socket is owned by uid {daemon_uid}"
            + ("" if separate else " (this user)"),
            "" if separate else "`sudo sponsio install --system` (P1)",
        )
    )
    admin_ok = admin_socket_path.exists() and not os.access(admin_socket_path, os.W_OK)
    g.append(
        Guarantee(
            2,
            "This user cannot reach the admin socket",
            admin_ok,
            f"{admin_socket_path} "
            + ("is closed to this user" if admin_ok else "is reachable or missing"),
            "" if admin_ok else "system install: admin.sock 0600 owned by _sponsio",
        )
    )
    for label, path in (("shim", shim_path), ("engine", engine_dir)):
        ok, detail = _root_owned_readonly(path)
        g.append(
            Guarantee(
                2,
                f"The {label} is root-owned and read-only",
                ok,
                detail,
                "" if ok else "system install puts it under a root-owned prefix",
            )
        )
    try:
        store_uid: int | None = plugin_root.stat().st_uid
    except OSError:
        store_uid = None
    store_ok = store_uid is not None and store_uid != uid and store_uid == daemon_uid
    g.append(
        Guarantee(
            2,
            "The rule store belongs to the daemon's user",
            store_ok,
            f"{plugin_root} is missing"
            if store_uid is None
            else f"{plugin_root} is owned by uid {store_uid}",
            ""
            if store_ok
            else "system install moves buckets to the daemon's state dir",
        )
    )

    paths = (
        managed_settings if managed_settings is not None else managed_settings_paths()
    )
    merged, read = _merged_managed(tuple(paths))
    ignored = _higher_source_ignores_files(
        mdm_settings if mdm_settings is not None else mdm_settings_path()
    )
    where = ", ".join(str(p) for p in read) or "no Claude Code managed settings"
    hooks_ok = "sponsio_shim" in json.dumps(merged.get("hooks", {})) and not ignored
    g.append(
        Guarantee(
            2,
            "Host hooks come from managed settings",
            hooks_ok,
            ignored or where,
            ""
            if hooks_ok
            else (
                "add `managedSourcesBehavior: merge` to the MDM profile, or put "
                "Sponsio's settings in it"
                if ignored
                else "`sudo sponsio install --system`"
            ),
        )
    )
    only = bool(merged.get("allowManagedHooksOnly")) and not ignored
    g.append(
        Guarantee(
            2,
            "Only managed hooks load",
            only,
            "allowManagedHooksOnly is on"
            if only
            else "user and project hooks still load, and could change a tool's input",
            "" if only else "reinstall without --allow-user-hooks",
            required=False,
        )
    )
    sandbox = merged.get("sandbox") if not ignored else None
    sandbox_ok = (
        isinstance(sandbox, dict)
        and sandbox.get("enabled") is True
        and sandbox.get("failIfUnavailable") is True
        and sandbox.get("allowUnsandboxedCommands") is False
    )
    g.append(
        Guarantee(
            2,
            "The host sandbox is on and cannot be bypassed",
            sandbox_ok,
            "enabled, fails when unavailable, no unsandboxed commands"
            if sandbox_ok
            else "not enabled in managed settings, or a command may run outside it",
            "" if sandbox_ok else "`sudo sponsio install --system`",
        )
    )
    version_ok = (
        _version_tuple(merged.get("requiredMinimumVersion"))
        >= _version_tuple(MIN_CLAUDE_CODE)
        and not ignored
    )
    g.append(
        Guarantee(
            2,
            f"Claude Code older than {MIN_CLAUDE_CODE} will not start",
            version_ok,
            f"requiredMinimumVersion is {merged.get('requiredMinimumVersion')}"
            if version_ok
            else "older versions ignore onFailure: block on hooks",
            "" if version_ok else "`sudo sponsio install --system`",
        )
    )

    notes: list[str] = []
    cursor = cursor_hooks if cursor_hooks is not None else cursor_hooks_path()
    try:
        cursor_text = cursor.read_text(encoding="utf-8") if cursor else ""
    except OSError:
        cursor_text = ""
    if "sponsio_shim" in cursor_text:
        notes.append(
            "Cursor stays at Tier 1 with its system hooks: nothing documented "
            "stops a user from switching them off, the @cursor/sdk local runtime "
            "loads no hooks, and a prompt right after startup can run before they "
            'load. Turn on "Block until hooks are ready" in Cursor\'s admin '
            "settings."
        )

    g.append(
        Guarantee(
            3,
            "Tool calls go through Sponsio's gateway",
            False,
            "the MCP gateway is available (`sponsio gateway`), but nothing yet "
            "keeps the agent off other routes",
            "run the agent inside `sponsio run --sandbox` (P3.3)",
        )
    )
    g.append(
        Guarantee(
            3,
            "Network egress goes through Sponsio's proxy",
            False,
            "not available in this release",
            "P3.4",
        )
    )

    tier = 0
    for t in (1, 2, 3):
        if all(x.ok for x in g if x.tier == t and x.required):
            tier = t
        else:
            break
    return TierReport(tier, g, notes)


def cursor_hooks_path() -> Path | None:
    if sys.platform == "darwin":
        return Path("/Library/Application Support/Cursor/hooks.json")
    if sys.platform == "linux":
        return Path("/etc/cursor/hooks.json")
    return None


def assess_this_machine(hooks_through_daemon: bool) -> TierReport:
    import sponsio
    from sponsio.daemon.client import default_socket_path
    from sponsio.daemon.server import admin_socket_for
    from sponsio.shim import shim_path
    from sponsio.system_install import installed_layout

    lay = installed_layout()
    if lay is not None:
        # The rule store sits inside the daemon's private state dir, which
        # this user cannot enter; the state dir's own owner is the answer.
        return assess(
            hooks_through_daemon=hooks_through_daemon,
            socket_path=lay.socket,
            admin_socket_path=lay.admin_socket,
            shim_path=lay.shim,
            engine_dir=lay.prefix,
            plugin_root=lay.state,
        )
    sock = default_socket_path()
    return assess(
        hooks_through_daemon=hooks_through_daemon,
        socket_path=sock,
        admin_socket_path=admin_socket_for(sock),
        shim_path=shim_path(),
        engine_dir=Path(sponsio.__file__).parent,
        plugin_root=Path.home() / ".sponsio" / "plugins",
    )

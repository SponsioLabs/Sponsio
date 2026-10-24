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


@dataclass
class TierReport:
    tier: int
    guarantees: list[Guarantee] = field(default_factory=list)

    def missing(self, tier: int) -> list[Guarantee]:
        return [g for g in self.guarantees if g.tier == tier and not g.ok]

    def as_dict(self) -> dict:
        return {
            "tier": self.tier,
            "guarantees": [g.__dict__ for g in self.guarantees],
        }


def managed_settings_paths() -> tuple[Path, ...]:
    """Where Claude Code reads managed (system) settings."""
    if sys.platform == "darwin":
        return (Path("/Library/Application Support/ClaudeCode/managed-settings.json"),)
    return (Path("/etc/claude-code/managed-settings.json"),)


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

    managed = None
    for p in (
        managed_settings if managed_settings is not None else managed_settings_paths()
    ):
        try:
            managed = (p, json.loads(p.read_text(encoding="utf-8")))
            break
        except (OSError, ValueError):
            continue
    text = json.dumps(managed[1]) if managed else ""
    hooks_ok = (
        bool(managed)
        and "sponsio_shim" in text
        and bool(managed[1].get("allowManagedHooksOnly"))
    )
    g.append(
        Guarantee(
            2,
            "Host hooks come from managed settings only",
            hooks_ok,
            f"{managed[0]}" if managed else "no Claude Code managed settings",
            "" if hooks_ok else "system install writes managed-settings.json (P1.6)",
        )
    )
    sandbox = managed[1].get("sandbox") if managed else None
    sandbox_ok = isinstance(sandbox, dict) and sandbox.get("enabled") is True
    g.append(
        Guarantee(
            2,
            "The host sandbox is on",
            sandbox_ok,
            "sandbox enabled in managed settings" if sandbox_ok else "not enabled",
            ""
            if sandbox_ok
            else "system install enables it in managed settings (P1.6)",
        )
    )

    for name in (
        "Tool calls go through Sponsio's gateway",
        "Network egress goes through Sponsio's proxy",
    ):
        g.append(Guarantee(3, name, False, "not available in this release", "P3"))

    tier = 0
    for t in (1, 2, 3):
        if all(x.ok for x in g if x.tier == t):
            tier = t
        else:
            break
    return TierReport(tier, g)


def assess_this_machine(hooks_through_daemon: bool) -> TierReport:
    import sponsio
    from sponsio.daemon.client import default_socket_path
    from sponsio.daemon.server import admin_socket_for
    from sponsio.shim import shim_path

    sock = default_socket_path()
    return assess(
        hooks_through_daemon=hooks_through_daemon,
        socket_path=sock,
        admin_socket_path=admin_socket_for(sock),
        shim_path=shim_path(),
        engine_dir=Path(sponsio.__file__).parent,
        plugin_root=Path.home() / ".sponsio" / "plugins",
    )

"""OpenClaw install/uninstall — bootstrap the
``~/.sponsio/plugins/_host_openclaw/sponsio.yaml`` library so OpenClaw
agents see Sponsio's defaults.

Surfaced behind ``sponsio host install openclaw``.

Unlike Cursor / Claude Code, OpenClaw doesn't ship a hook config file
of its own — agents load Sponsio via an ``openclaw.plugin.json``
plugin manifest distributed separately, and that manifest invokes
``sponsio plugin guard --stdin``.  This installer's job is the
contract-library half of the setup: write the OpenClaw-shaped
fallback library so the plugin has rules to evaluate against.
"""

from __future__ import annotations

import os
from pathlib import Path

from sponsio.integrations.hosts import (
    HookHost,
    HostInstallResult,
    HostUninstallResult,
)


def _library_root() -> Path:
    override = os.environ.get("SPONSIO_PLUGIN_ROOT")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".sponsio" / "plugins"


def install(
    host: HookHost,
    *,
    scope: str = "user",
    fail_closed: bool = True,  # accepted for API parity; OpenClaw plugin owns this
    force: bool = False,
    binary: str | None = None,  # accepted for API parity
) -> HostInstallResult:
    from sponsio.plugin.registry import read_bundled

    target_dir = _library_root() / "_host_openclaw"
    target = target_dir / "sponsio.yaml"

    try:
        src_text = read_bundled("_host_openclaw")
    except (FileNotFoundError, ModuleNotFoundError) as e:
        return HostInstallResult(
            host=host.name,
            config_path=target,
            written=False,
            note=f"bundled _host_openclaw library missing ({e}); reinstall sponsio",
        )

    if target.exists() and not force:
        return HostInstallResult(
            host=host.name,
            config_path=target,
            written=False,
            note="library already exists — pass force=True to overwrite",
        )

    target_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(src_text, encoding="utf-8")
    return HostInstallResult(
        host=host.name,
        config_path=target,
        written=True,
        note=(
            "wrote OpenClaw fallback contract library; install the "
            "OpenClaw Sponsio plugin separately to wire the runtime hook"
        ),
    )


def uninstall(host: HookHost, *, scope: str = "user") -> HostUninstallResult:
    target = _library_root() / "_host_openclaw" / "sponsio.yaml"
    if not target.exists():
        return HostUninstallResult(
            host=host.name,
            config_path=target,
            removed_entries=0,
            note="no _host_openclaw library found — nothing to uninstall",
        )
    target.unlink()
    return HostUninstallResult(
        host=host.name,
        config_path=target,
        removed_entries=1,
        note="removed OpenClaw fallback library",
    )

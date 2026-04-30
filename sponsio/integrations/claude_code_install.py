"""Claude Code install/uninstall — write hook entries into Claude
Code's user-level ``~/.claude/settings.json`` (or
``./.claude/settings.json`` for project scope).

Surfaced behind ``sponsio host install claude-code``.

Hook payload schema (Claude Code):

    {
      "PreToolUse": [
        { "matcher": "*",
          "hooks": [{ "type": "command", "command": "<binary> host guard claude-code --stdin" }] }
      ]
    }

Reference: https://code.claude.com/docs/en/hooks
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from sponsio.integrations.hosts import (
    HookHost,
    HostInstallResult,
    HostUninstallResult,
)

_INSTALLED_EVENTS = ("PreToolUse", "PostToolUse")


def _resolve_binary(override: str | None) -> str:
    if override:
        return override
    candidate = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if candidate and candidate.is_absolute() and candidate.exists():
        return str(candidate)
    resolved = shutil.which("sponsio")
    return resolved or "sponsio"


def _ensure_bundled_library(bucket: str) -> None:
    """Bootstrap ``~/.sponsio/plugins/<bucket>/sponsio.yaml`` if absent.

    Each host now owns its own bucket under ``~/.sponsio/plugins/`` so
    per-IDE rules can diverge from the legacy shared ``_host`` library.
    The bundled starter is dropped in on first install only — subsequent
    installs leave the user's edits alone."""
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
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _ensure_subagent_library() -> None:
    """Backward-compat shim — bootstraps the claude-code host buckets.

    Pre-host-bucketing this only seeded ``_host_subagent``. Now we seed
    both the main and sub-agent buckets for Claude Code; the old name
    is preserved in case any caller imported it directly.
    """
    _ensure_bundled_library("_host_claude_code")
    _ensure_bundled_library("_host_claude_code_subagent")


def install(
    host: HookHost,
    *,
    scope: str = "user",
    fail_closed: bool = True,
    force: bool = False,
    binary: str | None = None,
) -> HostInstallResult:
    """Write or merge Sponsio entries into Claude Code's settings.json.

    ``fail_closed`` is currently advisory — Claude Code's hook protocol
    treats any non-zero exit as a non-blocking error (deny is signalled
    via JSON, not exit code).  Sponsio's stdin handler always emits the
    correct deny shape on a violation regardless of this flag.

    Side effect: bootstraps ``_host_subagent`` library if absent, so
    Task-spawned subagent calls have a stricter library to route to.
    """
    _ensure_subagent_library()
    if scope == "project":
        target = Path.cwd() / ".claude" / "settings.json"
    else:
        target = host.config_path_user

    bin_cmd = _resolve_binary(binary)
    cmd = f"{bin_cmd} host guard claude-code --stdin"

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
                    f"{target} exists but is not valid JSON — refusing "
                    "to merge.  Re-run with force=True to overwrite."
                ),
            )
        merged = dict(existing)
        hooks_root = (
            merged.get("hooks") if isinstance(merged.get("hooks"), dict) else {}
        )
        for event in _INSTALLED_EVENTS:
            entries = (
                hooks_root.get(event) if isinstance(hooks_root.get(event), list) else []
            )
            keep: list[dict] = []
            for prior in entries:
                if not isinstance(prior, dict):
                    keep.append(prior)
                    continue
                # Match Sponsio's own entries by sniffing the embedded
                # command — ``sponsio host guard`` (new) or
                # ``sponsio plugin guard`` (legacy).
                inner = (
                    prior.get("hooks") if isinstance(prior.get("hooks"), list) else []
                )
                is_sponsio = any(
                    isinstance(h, dict)
                    and isinstance(h.get("command"), str)
                    and (
                        "host guard claude-code" in h["command"]
                        or "plugin guard" in h["command"]
                    )
                    for h in inner
                )
                if is_sponsio:
                    continue
                keep.append(prior)
            keep.append(
                {
                    "matcher": "*",
                    "hooks": [{"type": "command", "command": cmd}],
                }
            )
            hooks_root[event] = keep
        merged["hooks"] = hooks_root
        out = merged
        note = "merged into existing settings.json"
    else:
        out = {
            "hooks": {
                event: [
                    {
                        "matcher": "*",
                        "hooks": [{"type": "command", "command": cmd}],
                    }
                ]
                for event in _INSTALLED_EVENTS
            }
        }
        note = "wrote fresh settings.json"

    target.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return HostInstallResult(
        host=host.name,
        config_path=target,
        written=True,
        note=note,
    )


def uninstall(host: HookHost, *, scope: str = "user") -> HostUninstallResult:
    if scope == "project":
        target = Path.cwd() / ".claude" / "settings.json"
    else:
        target = host.config_path_user

    if not target.exists():
        return HostUninstallResult(
            host=host.name,
            config_path=target,
            removed_entries=0,
            note="no settings.json found — nothing to uninstall",
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
            note="settings.json is malformed — refusing to edit",
        )

    hooks_root = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    removed = 0
    for event in _INSTALLED_EVENTS:
        entries = (
            hooks_root.get(event) if isinstance(hooks_root.get(event), list) else []
        )
        kept: list[dict] = []
        for prior in entries:
            if not isinstance(prior, dict):
                kept.append(prior)
                continue
            inner = prior.get("hooks") if isinstance(prior.get("hooks"), list) else []
            is_sponsio = any(
                isinstance(h, dict)
                and isinstance(h.get("command"), str)
                and (
                    "host guard claude-code" in h["command"]
                    or "plugin guard" in h["command"]
                )
                for h in inner
            )
            if is_sponsio:
                removed += 1
                continue
            kept.append(prior)
        if kept:
            hooks_root[event] = kept
        elif event in hooks_root:
            del hooks_root[event]

    if hooks_root:
        data["hooks"] = hooks_root
    else:
        data.pop("hooks", None)

    if data:
        target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        note = f"removed {removed} sponsio entries from settings.json"
    else:
        target.unlink()
        note = f"removed {removed} sponsio entries; deleted now-empty settings.json"

    return HostUninstallResult(
        host=host.name,
        config_path=target,
        removed_entries=removed,
        note=note,
    )

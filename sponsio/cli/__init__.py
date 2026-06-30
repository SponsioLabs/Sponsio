"""Sponsio CLI.

Historically a single ``sponsio/cli.py`` module; now a package split
into :mod:`sponsio.cli.commands` (one module per top-level command) and
:mod:`sponsio.cli.groups` (one module per command group). The root
``cli`` group lives in :mod:`sponsio.cli.app`; cross-command helpers in
:mod:`sponsio.cli._shared`.

Importing a command/group module registers it on the shared ``cli``
group as a side effect. Every command, group, and the internal helpers
that other modules and tests import from ``sponsio.cli`` are re-exported
here, so ``from sponsio.cli import X`` keeps working regardless of which
submodule ``X`` now lives in.
"""

from __future__ import annotations

from sponsio.cli._shared import _resolve_entry
from sponsio.cli.app import cli

# Top-level commands (importing each registers it on `cli`).
from sponsio.cli.commands.check import check
from sponsio.cli.commands.demo import demo
from sponsio.cli.commands.doctor import doctor
from sponsio.cli.commands.eval import eval_cmd
from sponsio.cli.commands.explain import explain
from sponsio.cli.commands.export import export_cmd
from sponsio.cli.commands.export_sessions import export_sessions_cmd
from sponsio.cli.commands.init import init
from sponsio.cli.commands.mode import _patch_mode_in_yaml, cmd_mode
from sponsio.cli.commands.onboard import onboard
from sponsio.cli.commands.packs import packs
from sponsio.cli.commands.patterns import patterns
from sponsio.cli.commands.prompt import cmd_prompt
from sponsio.cli.commands.replay import replay
from sponsio.cli.commands.report import report
from sponsio.cli.commands.scan import (
    _drop_contract_indices,
    _filter_invalid_contracts,
    scan,
)
from sponsio.cli.commands.serve import serve
from sponsio.cli.commands.validate import validate

# Command groups.
from sponsio.cli.groups.cursor import cursor
from sponsio.cli.groups.daemon import daemon
from sponsio.cli.groups.host import _refresh_per_host_bundles, host
from sponsio.cli.groups.plugin import _stamp_bundled_source, plugin
from sponsio.cli.groups.skill import (
    _SKILL_TOOL_DIRS,
    _packaged_skill_source,
    _verify_skill_install_target,
    skill,
)


def main():
    cli()


__all__ = [
    "cli",
    "main",
    # commands
    "demo",
    "patterns",
    "packs",
    "validate",
    "check",
    "explain",
    "replay",
    "report",
    "serve",
    "scan",
    "export_cmd",
    "export_sessions_cmd",
    "eval_cmd",
    "init",
    "doctor",
    "onboard",
    "cmd_mode",
    "cmd_prompt",
    # groups
    "skill",
    "plugin",
    "host",
    "daemon",
    "cursor",
    # helpers imported elsewhere
    "_resolve_entry",
    "_patch_mode_in_yaml",
    "_filter_invalid_contracts",
    "_drop_contract_indices",
    "_stamp_bundled_source",
    "_refresh_per_host_bundles",
    "_SKILL_TOOL_DIRS",
    "_packaged_skill_source",
    "_verify_skill_install_target",
]

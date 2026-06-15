"""Sponsio CLI.

This was historically a single ``sponsio/cli.py`` module; it is now a
package split into :mod:`sponsio.cli.commands` and
:mod:`sponsio.cli.groups`. Every public command, group, and the handful
of internal helpers that other modules and tests import from
``sponsio.cli`` are re-exported here, so ``from sponsio.cli import X``
keeps working regardless of which submodule ``X`` now lives in.
"""

from __future__ import annotations

from sponsio.cli.app import cli

# Command groups carved into sponsio/cli/groups/. Importing each module
# registers its group + subcommands on `cli`; the name is re-exported
# for back-compat (`from sponsio.cli import daemon`).
from sponsio.cli.groups.cursor import cursor
from sponsio.cli.groups.daemon import daemon
from sponsio.cli.groups.plugin import _stamp_bundled_source, plugin
from sponsio.cli.groups.skill import (
    _SKILL_TOOL_DIRS,
    _packaged_skill_source,
    _verify_skill_install_target,
    skill,
)

# Still-monolithic commands/groups (carved out incrementally).
from sponsio.cli._monolith import (
    _drop_contract_indices,
    _filter_invalid_contracts,
    _patch_mode_in_yaml,
    _refresh_per_host_bundles,
    _resolve_entry,
    check,
    cmd_mode,
    cmd_prompt,
    demo,
    doctor,
    eval_cmd,
    explain,
    export_cmd,
    export_sessions_cmd,
    host,
    init,
    main,
    onboard,
    packs,
    patterns,
    replay,
    report,
    scan,
    serve,
    validate,
)

__all__ = [
    "cli",
    "main",
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
    "skill",
    "plugin",
    "host",
    "daemon",
    "cursor",
    "_SKILL_TOOL_DIRS",
    "_packaged_skill_source",
    "_verify_skill_install_target",
    "_resolve_entry",
    "_patch_mode_in_yaml",
    "_filter_invalid_contracts",
    "_drop_contract_indices",
    "_stamp_bundled_source",
    "_refresh_per_host_bundles",
]

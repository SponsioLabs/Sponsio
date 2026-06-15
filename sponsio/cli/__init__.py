"""Sponsio CLI.

This was historically a single ``sponsio/cli.py`` module; it is now a
package split into :mod:`sponsio.cli.commands` and
:mod:`sponsio.cli.groups`. Every public command, group, and the handful
of internal helpers that other modules and tests import from
``sponsio.cli`` are re-exported here, so ``from sponsio.cli import X``
keeps working regardless of which submodule ``X`` now lives in.
"""

from __future__ import annotations

from sponsio.cli._monolith import (
    _SKILL_TOOL_DIRS,
    _drop_contract_indices,
    _filter_invalid_contracts,
    _packaged_skill_source,
    _patch_mode_in_yaml,
    _refresh_per_host_bundles,
    _resolve_entry,
    _stamp_bundled_source,
    _verify_skill_install_target,
    check,
    cli,
    cmd_mode,
    cmd_prompt,
    cursor,
    daemon,
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
    plugin,
    replay,
    report,
    scan,
    serve,
    skill,
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

"""Helpers shared across more than one CLI command/group module.

Kept deliberately small: only things genuinely used by multiple
command modules live here, so the per-command modules stay focused and
there is one obvious home for cross-cutting helpers.
"""

from __future__ import annotations


def _contract_guarantee(entry):
    """Read the guarantee block out of a YAML/dict contract entry.

    Reads the canonical ``G`` (short) / ``guarantee`` (long) keys. No
    legacy alias support. the rename is hard.
    """
    if not isinstance(entry, dict):
        return None
    return entry.get("G") or entry.get("guarantee")

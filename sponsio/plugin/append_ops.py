"""Shared validation + merge logic for additive plugin-yaml appends.

Two callers share this module:

* ``sponsio plugin append`` (CLI, direct file path)
* ``plugin.append`` daemon RPC handler

Both want the same structural-additive guarantees: only new
``contracts:`` entries land, no ``customized:`` / ``disabled:`` /
``include:`` smuggling, no desc collisions, post-merge file passes
``load_config`` validation.  Pulling the logic into one module so
the daemon handler doesn't drift from the CLI behaviour.

Design:

* :class:`AppendError` carries a ``code`` so the daemon handler can
  surface it as a structured RPC error with the right
  ``"validation"`` / ``"not_found"`` / ``"internal"`` taxonomy.
* :func:`merge_staging_into_target` is the all-in-one entry point.
  Pure on its inputs (paths + content); doesn't know about click,
  daemons, or stdout — those are caller concerns.
"""

from __future__ import annotations

import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# Per-target lock table.  ``merge_staging_into_target`` is a
# read-modify-write on the target file, so two concurrent calls on
# the same target would race: both read the same starting state,
# both write back, the later writer overwrites the earlier writer's
# new contracts.  Keeping a lock per resolved target path lets
# different targets proceed in parallel while serialising same-target
# writes.  Cross-process serialisation (multi-daemon-worker) is not a
# concern today (single-process daemon); when we add that we'll add
# fcntl.flock on the target.
_target_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(target_path: Path) -> threading.Lock:
    with _locks_guard:
        return _target_locks.setdefault(target_path.resolve(), threading.Lock())


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


REJECTED_AGENT_KEYS: tuple[str, ...] = (
    "customized",
    "include",
    "tool_rename",
    "workspace",
    "judge",
    "tweaks",  # dropped legacy alias — surface the rename hint
    "overrides",  # dropped legacy alias
)


class AppendError(Exception):
    """Structural / validation / not-found failure during an append.

    ``code`` mirrors the daemon RPC error taxonomy:

    * ``"validation"`` — staging structure rejected (illegal keys,
      collision, missing desc, etc.).  Caller should fix the input.
    * ``"not_found"`` — target file doesn't exist.  Caller should
      bootstrap the bucket first.
    * ``"internal"`` — post-write load_config failure or file I/O
      error.  Caller should investigate.
    """

    def __init__(self, message: str, *, code: str = "validation") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AppendResult:
    """Outcome of a successful (or dry-run) merge."""

    agent_id: str
    appended_count: int
    descs: list[str]
    target_path: str
    dry_run: bool


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_staging(staging_doc: Any) -> tuple[str, list[dict]]:
    """Validate a parsed staging document; return ``(agent_id, contracts)``.

    Raises :class:`AppendError` (``code="validation"``) on any
    structural violation.  Pure function — no I/O — easy to unit-test.
    """
    if not isinstance(staging_doc, dict):
        raise AppendError("staging file must be a YAML mapping at the top level")

    rejected_top = {k for k in staging_doc.keys() if k not in ("version", "agents")}
    if rejected_top:
        raise AppendError(
            f"staging file may only contain `version:` and `agents:` "
            f"at the top level — found disallowed key(s): "
            f"{sorted(rejected_top)}"
        )

    agents = staging_doc.get("agents") or {}
    if not isinstance(agents, dict) or not agents:
        raise AppendError("staging file must define exactly one agent under `agents:`")
    if len(agents) > 1:
        raise AppendError(
            f"staging file targets multiple agents {sorted(agents)} — "
            f"split into one staging file per agent"
        )

    agent_id, agent_data = next(iter(agents.items()))
    if not isinstance(agent_data, dict):
        raise AppendError(f"agent {agent_id!r}: value must be a mapping")

    illegal_agent_keys = sorted(
        k for k in agent_data.keys() if k in REJECTED_AGENT_KEYS
    )
    if illegal_agent_keys:
        legacy = [k for k in illegal_agent_keys if k in ("tweaks", "overrides")]
        if legacy:
            raise AppendError(
                f"agent {agent_id!r}: `{legacy[0]}:` is no longer accepted "
                f"as a yaml key.  This command only adds `contracts:` — "
                f"customizations belong in `customized:` and must be "
                f"authored by the user, not appended by an agent."
            )
        raise AppendError(
            f"agent {agent_id!r}: staging may only set `contracts:` — "
            f"found disallowed key(s): {illegal_agent_keys}.  These would "
            f"alter existing governance and must be hand-edited by the user."
        )

    contracts = agent_data.get("contracts") or []
    if not isinstance(contracts, list) or not contracts:
        raise AppendError(f"agent {agent_id!r}: `contracts:` must be a non-empty list")

    for i, c in enumerate(contracts):
        if not isinstance(c, dict):
            raise AppendError(f"agent {agent_id!r}: contracts[{i}] must be a mapping")
        if "disabled" in c:
            raise AppendError(
                f"agent {agent_id!r}: contracts[{i}] has `disabled:` — "
                f"`disabled:` belongs in a `customized:` entry, not a "
                f"contract.  `customized:` cannot be appended via this "
                f"command; ask the user to edit the file directly."
            )
        if not c.get("desc"):
            raise AppendError(
                f"agent {agent_id!r}: contracts[{i}] must carry a `desc:` "
                f"so it can be located later by `customized:` match clauses"
            )

    return agent_id, contracts


def existing_descs(target_doc: Any, agent_id: str) -> set[str]:
    """``desc:`` strings already present for ``agent_id`` in ``target_doc``."""
    agents = (target_doc or {}).get("agents") or {}
    agent_data = agents.get(agent_id)
    if not isinstance(agent_data, dict):
        return set()
    contracts = agent_data.get("contracts") or []
    if not isinstance(contracts, list):
        return set()
    return {c["desc"] for c in contracts if isinstance(c, dict) and c.get("desc")}


# ---------------------------------------------------------------------------
# All-in-one merge
# ---------------------------------------------------------------------------


def merge_staging_into_target(
    target_path: Path,
    staging_text: str,
    *,
    dry_run: bool = False,
) -> AppendResult:
    """Validate ``staging_text`` and merge it into ``target_path``.

    Both the CLI's direct-write mode and the daemon's RPC handler call
    this.  When called inside the daemon, ``target_path`` is owned by
    the daemon UID (kernel guarantees the agent UID can't have written
    here directly); when called from the CLI in dev mode, ``target_path``
    is owned by the user.  The function doesn't care — it just does
    the parse + validate + merge + atomic write + reload-validate.

    Raises :class:`AppendError` for any failure; the caller surfaces it.

    Thread-safe: per-target lock serialises read-modify-write on the
    same target file so concurrent ``plugin.append`` calls don't lose
    rules to a last-writer-wins race.
    """
    import yaml

    if not target_path.exists():
        raise AppendError(
            f"target not found: {target_path}.  Bootstrap the bucket "
            f"first with `sponsio plugin install <name>` or "
            f"`sponsio host install <host>`.",
            code="not_found",
        )

    try:
        staging_doc = yaml.safe_load(staging_text) or {}
    except yaml.YAMLError as e:
        raise AppendError(f"staging is not valid YAML: {e}") from e

    agent_id, new_contracts = validate_staging(staging_doc)

    # Validation above is pure on the staging input — safe to do
    # outside the lock.  The lock guards the read → merge → write
    # window where the file's prior content matters.
    with _lock_for(target_path):
        return _merge_locked(target_path, agent_id, new_contracts, dry_run)


def _merge_locked(
    target_path: Path,
    agent_id: str,
    new_contracts: list[dict],
    dry_run: bool,
) -> AppendResult:
    """The locked critical section: read target, check collisions,
    merge, atomic write, reload-validate.  Caller already holds the
    target's lock.
    """
    import yaml

    target_text = target_path.read_text(encoding="utf-8")
    target_doc = yaml.safe_load(target_text) or {}

    collisions = sorted(
        c["desc"]
        for c in new_contracts
        if c["desc"] in existing_descs(target_doc, agent_id)
    )
    if collisions:
        raise AppendError(
            f"agent {agent_id!r}: {len(collisions)} contract `desc:` "
            f"value(s) already exist in the target — this command only "
            f"adds new rules, never modifies existing ones.  "
            f"Colliding desc(s): {collisions}.  "
            f"To change an existing rule, ask the user to edit the file "
            f"directly or to add a `customized:` entry."
        )

    descs = [c["desc"] for c in new_contracts]

    if dry_run:
        return AppendResult(
            agent_id=agent_id,
            appended_count=len(new_contracts),
            descs=descs,
            target_path=str(target_path),
            dry_run=True,
        )

    agents = target_doc.setdefault("agents", {})
    agent_block = agents.setdefault(agent_id, {})
    if not isinstance(agent_block, dict):
        raise AppendError(
            f"target file's `agents.{agent_id}` is not a mapping; "
            f"refusing to overwrite a malformed entry"
        )
    contracts_list = agent_block.setdefault("contracts", [])
    if not isinstance(contracts_list, list):
        raise AppendError(f"target file's `agents.{agent_id}.contracts` is not a list")
    contracts_list.extend(new_contracts)

    rendered = yaml.safe_dump(target_doc, sort_keys=False)
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=".sponsio.append.", dir=str(target_path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(rendered)
        os.replace(tmp_path_str, target_path)
    except Exception:
        if os.path.exists(tmp_path_str):
            os.unlink(tmp_path_str)
        raise

    # Re-validate the merged file via the loader; an internal
    # contradiction here is on us, not the staging input.
    from sponsio.config import ConfigError, load_config

    try:
        load_config(target_path)
    except ConfigError as e:
        raise AppendError(
            f"merge succeeded on disk but the resulting file fails to "
            f"validate: {e}.  Inspect {target_path} and roll back if needed.",
            code="internal",
        ) from e

    return AppendResult(
        agent_id=agent_id,
        appended_count=len(new_contracts),
        descs=descs,
        target_path=str(target_path),
        dry_run=False,
    )

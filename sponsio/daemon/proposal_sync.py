"""Decide proposals in the cloud console instead of on the machine.

With ``cloud: {project: ..., proposals: true}`` in ``daemon.yaml`` the
daemon uploads every proposal it holds, with its local status, and gets
back the decisions a person made in the console that it has not applied
yet. It applies each one through the same :meth:`ProposalStore.decide`
the admin socket uses, so the change and its audit record are the same;
``decided_by`` names the person instead of a local peer.

A decision applies only when the daemon's own stored params hash to the
``params_sha256`` it carries: the console shows the params it was sent,
and this check makes those the params that run. Anything else is refused
and recorded as ``proposal_cloud_refused``.

The daemon pulls; nothing is pushed to it. It authenticates with its own
cloud key (``sponsio admin cloud-key set``), which needs ``author``. The
cloud lets only a signed-in person decide, never an API key, so a key the
agent can read cannot approve what that agent proposed.
"""

from __future__ import annotations

import datetime as _dt
import json
import socket
from typing import Any

from sponsio.daemon import audit
from sponsio.daemon.proposals import (
    PENDING,
    ProposalError,
    ProposalStore,
    params_sha256,
)

PROPOSAL_CLOUD_REFUSED = "proposal_cloud_refused"
"""Audit event: a cloud decision was not applied, and why."""

_UPLOADED = (
    "id",
    "kind",
    "params",
    "status",
    "preview",
    "submitted_at",
    "submitted_by",
    "decided_by",
    "result",
)


# The cloud takes at most 500 per sync. Pending ones go oldest first, so
# an agent filing proposals by the hundred delays its own newest, not the
# one already waiting; decided ones only while the console may still be
# showing them as waiting.
MAX_PENDING = 400
MAX_DECIDED = 90
DECIDED_FOR = _dt.timedelta(days=7)
MAX_PARAMS_BYTES = 256 * 1024


def _upload(store: ProposalStore) -> list[dict[str, Any]]:
    since = (_dt.datetime.now(_dt.timezone.utc) - DECIDED_FOR).isoformat()
    pending: list[dict[str, Any]] = []
    decided: list[dict[str, Any]] = []
    for p in store.list():  # oldest first
        if len(json.dumps(p.get("params"), default=str)) > MAX_PARAMS_BYTES:
            continue  # decided on the machine, not sent
        if p.get("status") == PENDING:
            pending.append(p)
        elif str(p.get("decided_at") or "") >= since:
            decided.append(p)
    chosen = pending[:MAX_PENDING] + decided[-MAX_DECIDED:]
    return [{k: p.get(k) for k in _UPLOADED} for p in chosen]


def sync_once(
    store: ProposalStore,
    client: Any,
    *,
    project: str | None,
    machine: str | None = None,
    refused: set[str] | None = None,
) -> dict[str, Any]:
    """One round: upload, apply what was decided, upload the outcome.

    ``refused`` remembers decisions already refused in this process, so a
    decision that will never apply is recorded once, not every interval.
    """
    machine = machine or socket.gethostname()
    refused = refused if refused is not None else set()
    answer = client.sync_proposals(project, machine=machine, proposals=_upload(store))
    applied: list[str] = []
    rejected: list[str] = []
    refusals: list[dict[str, str]] = []

    def refuse(pid: str, reason: str) -> None:
        refusals.append({"id": pid, "reason": reason})
        if pid not in refused:
            refused.add(pid)
            audit.append(store.state_dir, PROPOSAL_CLOUD_REFUSED, id=pid, reason=reason)

    for decision in answer.get("decisions") or []:
        pid = decision.get("id")
        if not isinstance(pid, str):
            continue
        try:
            record = store.get(pid)
        except ProposalError:
            refuse(pid, "no such proposal on this machine")
            continue
        if record.get("status") != PENDING:
            continue
        if decision.get("params_sha256") != params_sha256(record["params"]):
            refuse(pid, "the decision is for different params than this proposal")
            continue
        approve = decision.get("approve") is True
        decided_by = decision.get("decided_by")
        try:
            store.decide(
                pid,
                approve,
                None,
                decided_by=decided_by
                if isinstance(decided_by, dict)
                else {"via": "sponsio-cloud"},
            )
        except Exception as e:  # noqa: BLE001 - one bad proposal must not stop the rest
            refuse(pid, f"could not apply: {e}")
            continue
        (applied if approve else rejected).append(pid)

    if applied or rejected:
        # Say what happened now rather than one interval later.
        client.sync_proposals(project, machine=machine, proposals=_upload(store))
    return {
        "uploaded": len(_upload(store)),
        "applied": applied,
        "rejected": rejected,
        "refused": refusals,
        "conflicts": answer.get("conflicts") or [],
    }

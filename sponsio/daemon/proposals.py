"""Rule changes the agent proposes and an operator approves.

The agent socket cannot change rules. What it can do is submit a
proposal (``proposal.submit``): the change is validated as a dry run,
stored under ``<state_dir>/proposals/<id>.json``, and applied only when
``proposal.approve`` arrives on the admin socket. This is the local form
of the product's propose-then-approve loop.

At Tier 1 the human and the agent share an OS user, so ``sponsio plugin
append`` submits and approves in one step; both calls are audited with
the caller's peer identity. At Tier 2 only root reaches the admin
socket, and approval waits for a person (P1.4 adds the review CLI).

Only ``plugin.append`` can be proposed today.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any, Callable

from sponsio.daemon import audit
from sponsio.daemon.peercred import Peer

PROPOSAL_SUBMITTED = "proposal_submitted"
PROPOSAL_APPROVED = "proposal_approved"
PROPOSAL_REJECTED = "proposal_rejected"

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"


class ProposalError(Exception):
    def __init__(self, message: str, *, code: str = "validation") -> None:
        super().__init__(message)
        self.code = code


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds")


class ProposalStore:
    """Proposals on disk, one JSON file each.

    ``appliers`` maps a proposal kind to ``apply(params, dry_run) ->
    result``; :meth:`submit` runs it with ``dry_run=True`` so an invalid
    change is refused at once instead of after someone approved it.
    """

    def __init__(
        self,
        state_dir: Path,
        appliers: dict[str, Callable[[dict[str, Any], bool], dict[str, Any]]],
    ) -> None:
        self.state_dir = Path(state_dir)
        self.dir = self.state_dir / "proposals"
        self._appliers = appliers
        self._lock = threading.Lock()

    def _path(self, proposal_id: str) -> Path:
        if not proposal_id.isalnum():
            raise ProposalError(f"no proposal {proposal_id!r}", code="not_found")
        return self.dir / f"{proposal_id}.json"

    def _write(self, record: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        path = self._path(record["id"])
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
        os.replace(tmp, path)

    def get(self, proposal_id: str) -> dict[str, Any]:
        try:
            return json.loads(self._path(proposal_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ProposalError(
                f"no proposal {proposal_id!r}", code="not_found"
            ) from None

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        if not self.dir.is_dir():
            return []
        out = []
        for path in sorted(self.dir.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if status is None or record.get("status") == status:
                out.append(record)
        out.sort(key=lambda r: r.get("submitted_at", ""))
        return out

    def submit(
        self,
        kind: str,
        params: dict[str, Any],
        peer: Peer | None,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Validate and store a proposal. With ``dry_run`` nothing is
        stored: the result carries the ``preview`` only."""
        apply = self._appliers.get(kind)
        if apply is None:
            raise ProposalError(
                f"cannot propose {kind!r}; one of: {', '.join(sorted(self._appliers))}"
            )
        preview = apply(params, True)
        if dry_run:
            return {"id": None, "kind": kind, "status": None, "preview": preview}
        record = {
            "id": secrets.token_hex(6),
            "kind": kind,
            "params": params,
            "status": PENDING,
            "preview": preview,
            "submitted_at": _now(),
            "submitted_by": peer.as_dict() if peer else None,
        }
        with self._lock:
            self._write(record)
        audit.append(
            self.state_dir,
            PROPOSAL_SUBMITTED,
            id=record["id"],
            kind=kind,
            peer=record["submitted_by"],
        )
        return record

    def decide(
        self, proposal_id: str, approve: bool, peer: Peer | None
    ) -> dict[str, Any]:
        with self._lock:
            record = self.get(proposal_id)
            if record.get("status") != PENDING:
                raise ProposalError(
                    f"proposal {proposal_id} is already {record.get('status')}",
                    code="conflict",
                )
            if approve:
                record["result"] = self._appliers[record["kind"]](
                    record["params"], False
                )
            record["status"] = APPROVED if approve else REJECTED
            record["decided_at"] = _now()
            record["decided_by"] = peer.as_dict() if peer else None
            self._write(record)
        audit.append(
            self.state_dir,
            PROPOSAL_APPROVED if approve else PROPOSAL_REJECTED,
            id=proposal_id,
            kind=record["kind"],
            peer=record["decided_by"],
        )
        return record

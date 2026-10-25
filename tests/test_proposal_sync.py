"""Proposals decided in the cloud console, applied by the daemon.

The daemon uploads what it holds and applies what a person decided in the
console, through the same ProposalStore.decide the admin socket uses. A
decision applies only to params that hash to what it names; anything else
is refused and recorded once.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sponsio.daemon import audit
from sponsio.daemon.config import DaemonConfig
from sponsio.daemon.proposal_sync import PROPOSAL_CLOUD_REFUSED, sync_once
from sponsio.daemon.proposals import ProposalStore, params_sha256

PARAMS = {"target": "_host_claude_code", "staging_yaml": "agents: {}\n"}
WHO = {"via": "sponsio-cloud", "email": "ops@example.com", "user_id": "u1"}


class FakeCloud:
    """Answers sync with whatever decisions the test sets."""

    def __init__(self) -> None:
        self.decisions: list[dict] = []
        self.uploads: list[list[dict]] = []

    def sync_proposals(self, project, *, machine, proposals):
        self.uploads.append(proposals)
        return {"decisions": list(self.decisions), "conflicts": []}


@pytest.fixture
def store(tmp_path: Path):
    applied: list[dict] = []

    def apply(params, dry_run):
        if not dry_run:
            applied.append(params)
        return {"dry_run": dry_run, "target": params["target"]}

    s = ProposalStore(tmp_path / "state", {"plugin.append": apply})
    s.applied = applied
    return s


def _decision(record, *, approve=True, sha=None):
    return {
        "id": record["id"],
        "approve": approve,
        "params_sha256": sha or params_sha256(record["params"]),
        "decided_by": WHO,
    }


def test_every_proposal_is_uploaded_with_its_status(store):
    a = store.submit("plugin.append", PARAMS, None)
    b = store.submit("plugin.append", {**PARAMS, "target": "_host_cursor"}, None)
    store.decide(b["id"], False, None)
    cloud = FakeCloud()

    sync_once(store, cloud, project="p", machine="m1")

    sent = {p["id"]: p for p in cloud.uploads[0]}
    assert sent[a["id"]]["status"] == "pending"
    assert sent[b["id"]]["status"] == "rejected"
    assert sent[a["id"]]["params"] == PARAMS


def test_an_approval_in_the_console_applies_and_names_the_person(store):
    record = store.submit("plugin.append", PARAMS, None)
    cloud = FakeCloud()
    cloud.decisions = [_decision(record)]

    out = sync_once(store, cloud, project="p", machine="m1")

    assert out["applied"] == [record["id"]]
    assert store.applied == [PARAMS]
    stored = store.get(record["id"])
    assert stored["status"] == "approved"
    assert stored["decided_by"] == WHO
    approved = audit.read(store.state_dir, "proposal_approved")
    assert approved[-1]["peer"] == WHO
    # The outcome goes back in the same round.
    assert cloud.uploads[-1][0]["status"] == "approved"


def test_a_rejection_in_the_console_applies_nothing(store):
    record = store.submit("plugin.append", PARAMS, None)
    cloud = FakeCloud()
    cloud.decisions = [_decision(record, approve=False)]

    out = sync_once(store, cloud, project="p", machine="m1")

    assert out["rejected"] == [record["id"]]
    assert store.applied == []
    assert store.get(record["id"])["status"] == "rejected"


def test_a_decision_for_other_params_is_refused_once(store):
    """The console showed the params it was sent. If what it approved does
    not hash to this proposal, it was not this change that was approved."""
    record = store.submit("plugin.append", PARAMS, None)
    cloud = FakeCloud()
    cloud.decisions = [_decision(record, sha="0" * 64)]
    refused: set[str] = set()

    first = sync_once(store, cloud, project="p", machine="m1", refused=refused)
    sync_once(store, cloud, project="p", machine="m1", refused=refused)

    assert first["applied"] == [] and store.applied == []
    assert store.get(record["id"])["status"] == "pending"
    assert first["refused"][0]["id"] == record["id"]
    assert len(audit.read(store.state_dir, PROPOSAL_CLOUD_REFUSED)) == 1


def test_a_decision_for_a_proposal_this_machine_never_made_is_refused(store):
    cloud = FakeCloud()
    cloud.decisions = [{"id": "abc123", "approve": True, "params_sha256": "0" * 64}]

    out = sync_once(store, cloud, project="p", machine="m1")

    assert out["refused"][0]["reason"] == "no such proposal on this machine"
    assert store.applied == []


def test_a_proposal_decided_on_the_machine_is_not_decided_again(store):
    record = store.submit("plugin.append", PARAMS, None)
    store.decide(record["id"], False, None)
    cloud = FakeCloud()
    cloud.decisions = [_decision(record)]

    out = sync_once(store, cloud, project="p", machine="m1")

    assert out["applied"] == [] and store.applied == []
    assert store.get(record["id"])["status"] == "rejected"


def test_a_decision_that_fails_to_apply_stays_pending(tmp_path):
    def broken(params, dry_run):
        if not dry_run:
            raise RuntimeError("bucket is not writable")
        return {}

    store = ProposalStore(tmp_path / "state", {"plugin.append": broken})
    record = store.submit("plugin.append", PARAMS, None)
    cloud = FakeCloud()
    cloud.decisions = [_decision(record)]

    out = sync_once(store, cloud, project="p", machine="m1")

    assert "bucket is not writable" in out["refused"][0]["reason"]
    assert store.get(record["id"])["status"] == "pending"


def test_the_hash_is_the_one_the_cloud_computes():
    """sponsio_cloud hashes json.dumps(params, sort_keys=True) with the
    default separators. Pinned so neither side drifts alone."""
    import hashlib

    assert (
        params_sha256({"b": 1, "a": "x"})
        == hashlib.sha256(b'{"a": "x", "b": 1}').hexdigest()
    )


def test_daemon_yaml_turns_sync_on_and_keeps_it():
    cfg = DaemonConfig.from_dict({"cloud": {"project": "p", "proposals": True}})
    assert cfg.cloud_proposals is True
    again = DaemonConfig.from_dict(cfg.to_dict())
    assert again.cloud_proposals is True and again.cloud_project == "p"
    assert (
        DaemonConfig.from_dict({"cloud": {"proposals": "yes"}}).cloud_proposals is False
    )

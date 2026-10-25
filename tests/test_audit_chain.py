"""The audit chain after the log is changed behind the daemon's back
(second review, 2026-10-24): once changed, ``verify`` keeps saying so,
however many records the daemon writes after it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sponsio.daemon import audit
from sponsio.daemon.proposals import ProposalStore


def _verify_like_rpc(sd: Path) -> audit.ChainReport:
    return audit.verify(sd, expected_head=audit.head(sd))


def _lines(sd: Path) -> list[str]:
    return audit.audit_path(sd).read_text().splitlines(keepends=True)


def _rechain(records: list[dict], start_prev: str = audit.GENESIS) -> list[str]:
    """What someone who knows the format writes: a valid chain."""
    prev, out = start_prev, []
    for i, rec in enumerate(records, start=1):
        rec = {k: v for k, v in rec.items() if k not in ("seq", "prev_hash", "hash")}
        rec["seq"], rec["prev_hash"] = i, prev
        rec["hash"] = prev = audit.record_hash(rec)
        out.append(json.dumps(rec, separators=(",", ":")) + "\n")
    return out


def _delete(sd):
    audit.audit_path(sd).unlink()


def _cut_tail(sd):
    audit.audit_path(sd).write_text("".join(_lines(sd)[:3]))


def _cut_middle(sd):
    lines = _lines(sd)
    audit.audit_path(sd).write_text("".join(lines[:2] + lines[3:]))


def _strip_chain(sd):
    out = []
    for line in _lines(sd):
        rec = json.loads(line)
        for k in ("seq", "prev_hash", "hash"):
            rec.pop(k)
        rec["event"] = "daemon_start"
        out.append(json.dumps(rec) + "\n")
    audit.audit_path(sd).write_text("".join(out))


def _rewrite_rechained(sd):
    recs = [json.loads(line) for line in _lines(sd)]
    recs[1]["event"] = "daemon_start"
    audit.audit_path(sd).write_text("".join(_rechain(recs)))


def _cut_and_forge_longer(sd):
    recs = [json.loads(line) for line in _lines(sd)][:3]
    recs += [{"ts": "x", "event": "daemon_start", "pid": 1} for _ in range(6)]
    audit.audit_path(sd).write_text("".join(_rechain(recs)))


@pytest.mark.parametrize(
    "change",
    [
        _delete,
        _cut_tail,
        _cut_middle,
        _strip_chain,
        _rewrite_rechained,
        _cut_and_forge_longer,
    ],
)
def test_a_changed_log_never_verifies_again(tmp_path, change):
    sd = tmp_path / "state"
    for i in range(5):
        audit.append(sd, "tamper_attempt", n=i)
    change(sd)
    for i in range(3):
        # Each verify RPC is followed by the admin_call record it writes.
        report = _verify_like_rpc(sd)
        assert not report.ok, f"{change.__name__}, verify #{i + 1}: {report}"
        audit.append(sd, "admin_call", method="audit.verify")
    cuts = audit.read(sd, audit.AUDIT_LOG_CUT)
    assert len(cuts) == (0 if change is _cut_middle else 1)


def test_records_another_process_adds_are_not_a_cut(tmp_path):
    sd = tmp_path / "state"
    audit.append(sd, "daemon_start")
    remembered = audit.head(sd)
    # Another process appends under the same lock, then this one again.
    audit._heads.pop(str(sd))
    audit.append(sd, "admin_call", method="x")
    audit._heads[str(sd)] = remembered
    audit.append(sd, "tamper_attempt")
    assert audit.read(sd, audit.AUDIT_LOG_CUT) == []
    assert _verify_like_rpc(sd).ok


def test_a_record_after_a_torn_line_is_kept(tmp_path):
    sd = tmp_path / "state"
    audit.append(sd, "daemon_start", socket="s")
    audit.append(sd, "admin_call", method="m")
    with open(audit.audit_path(sd), "ab") as fh:
        fh.write(b'{"ts":"2026-10-24T00:00:00.000+00:00","event":"adm')
    rec = audit.append(sd, "tamper_attempt", tool="Bash")
    assert rec is not None and rec["seq"] == 3
    assert [r["event"] for r in audit.read(sd)] == [
        "daemon_start",
        "admin_call",
        "tamper_attempt",
    ]
    # The torn line itself is still reported.
    assert any("not JSON" in p for p in audit.verify(sd).problems)


def test_keys_that_are_not_strings_hash_as_they_read_back(tmp_path):
    sd = tmp_path / "state"
    assert audit.append(sd, "cloud_verified", counts={2: "a", 10: "b"})
    assert audit.append(sd, "cloud_verified", counts={1: "a", "b": 2})
    assert audit.append(sd, "cloud_verified", value={"x"})
    report = audit.verify(sd)
    assert report.ok, report.problems


def test_an_approval_records_what_was_applied(tmp_path):
    sd = tmp_path / "state"

    def applier(params, dry_run):
        return {"descs": list(params["descs"]), "dry_run": dry_run}

    store = ProposalStore(sd, {"plugin.append": applier})
    rec = store.submit("plugin.append", {"descs": ["benign"]}, None)
    p = sd / "proposals" / f"{rec['id']}.json"
    data = json.loads(p.read_text())
    data["params"]["descs"] = ["allow everything"]
    p.write_text(json.dumps(data))
    decided = store.decide(rec["id"], True, None)
    (approved,) = audit.read(sd, "proposal_approved")
    assert approved["change"] == decided["result"]
    assert approved["change"]["descs"] == ["allow everything"]
    assert len(approved["params_sha256"]) == 64

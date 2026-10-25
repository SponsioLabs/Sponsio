"""Operator-side pieces of Tier 2: the hash-chained audit log (P1.7),
proposal review from the CLI (P1.4), the daemon's own cloud key and
signed rule files (P1.5)."""

from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest
from click.testing import CliRunner

from sponsio.cli import cli
from sponsio.daemon import audit
from sponsio.daemon.audit_export import record_to_otlp
from sponsio.daemon.client import DaemonClient
from sponsio.daemon.guard_service import GuardService
from sponsio.daemon.handlers import register_default_handlers
from sponsio.daemon.server import DaemonServer
from sponsio.guard_stdin import GuardPolicy

BUCKET = "_host_claude_code"
LIBRARY = """
version: "1"
agents:
  _host_claude_code:
    contracts:
      - desc: "no rm -rf"
        G:
          pattern: arg_blacklist
          args: [Bash, command, ["rm\\\\s+-rf"]]
"""
STAGING = """
agents:
  _host_claude_code:
    contracts:
      - desc: "at most 3 Bash calls"
        G: { pattern: rate_limit, args: [Bash, 3] }
"""


@pytest.fixture
def tmp():
    d = Path(tempfile.mkdtemp(prefix="spa-", dir="/tmp"))
    lib = d / "plugins" / BUCKET / "sponsio.yaml"
    lib.parent.mkdir(parents=True)
    lib.write_text(LIBRARY)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _service(tmp: Path) -> GuardService:
    return GuardService(
        root=tmp / "plugins",
        state_dir=tmp / "state",
        policy=GuardPolicy(mode="enforce", on_error="deny"),
    )


@pytest.fixture
def daemon(tmp):
    svc = _service(tmp)
    server = DaemonServer(tmp / "sock", accept_timeout_s=0.05)
    register_default_handlers(server, guard_service=svc)
    server.start()
    yield server, svc
    server.stop(join_timeout_s=0.5)


def _bash(command: str) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "session_id": "s1",
    }


# ---------------------------------------------------------------------------
# P1.7 audit chain
# ---------------------------------------------------------------------------


def _lines(state: Path) -> list[str]:
    return audit.audit_path(state).read_text().splitlines()


def _write_lines(state: Path, lines: list[str]) -> None:
    audit.audit_path(state).write_text("".join(line + "\n" for line in lines))


def test_records_form_a_chain(tmp):
    state = tmp / "state"
    for i in range(5):
        audit.append(state, "thing", n=i)
    recs = audit.read(state)
    assert [r["seq"] for r in recs] == [1, 2, 3, 4, 5]
    assert recs[0]["prev_hash"] == audit.GENESIS
    assert all(b["prev_hash"] == a["hash"] for a, b in zip(recs, recs[1:]))
    report = audit.verify(state, expected_head=audit.head(state))
    assert report.ok and report.chained == 5 and report.head == (5, recs[-1]["hash"])


def test_an_edited_record_is_found(tmp):
    state = tmp / "state"
    for i in range(4):
        audit.append(state, "thing", n=i)
    lines = _lines(state)
    rec = json.loads(lines[1])
    rec["n"] = 99
    lines[1] = json.dumps(rec, separators=(",", ":"))
    _write_lines(state, lines)
    report = audit.verify(state)
    assert not report.ok and "edited" in report.problems[0]


def test_a_removed_record_is_found(tmp):
    state = tmp / "state"
    for i in range(4):
        audit.append(state, "thing", n=i)
    lines = _lines(state)
    del lines[2]
    _write_lines(state, lines)
    report = audit.verify(state)
    assert not report.ok
    assert any("removed or replaced" in p for p in report.problems)


def test_records_cut_from_the_end_are_found_against_the_daemons_head(tmp):
    state = tmp / "state"
    for i in range(4):
        audit.append(state, "thing", n=i)
    head = audit.head(state)
    _write_lines(state, _lines(state)[:-1])
    assert audit.verify(state).ok  # the chain alone cannot tell
    report = audit.verify(state, expected_head=head)
    assert not report.ok and "removed from the end" in report.problems[0]


def test_records_from_another_process_after_the_head_are_fine(tmp):
    state = tmp / "state"
    audit.append(state, "daemon_thing")
    head = audit.head(state)
    code = (
        "import sys; from pathlib import Path; from sponsio.daemon import audit;"
        "audit.append(Path(sys.argv[1]), 'cli_thing')"
    )
    subprocess.run([sys.executable, "-c", code, str(state)], check=True)
    report = audit.verify(state, expected_head=head)
    assert report.ok and report.chained == 2


def test_records_from_before_the_chain_are_not_a_break(tmp):
    state = tmp / "state"
    state.mkdir(parents=True)
    audit.audit_path(state).write_text('{"ts":"x","event":"old","pid":1}\n')
    audit.append(state, "new")
    report = audit.verify(state)
    assert report.ok and (report.unchained, report.chained) == (1, 1)


def test_concurrent_writers_keep_one_chain(tmp):
    state = tmp / "state"
    code = (
        "import sys; from pathlib import Path; from sponsio.daemon import audit\n"
        "for i in range(40): audit.append(Path(sys.argv[1]), 'p', i=i)"
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", code, str(state)]) for _ in range(3)
    ]
    threads = [
        threading.Thread(
            target=lambda: [audit.append(state, "t", i=i) for i in range(40)]
        )
        for _ in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for p in procs:
        assert p.wait(timeout=60) == 0
    report = audit.verify(state)
    assert report.ok, report.problems
    assert report.chained == 240


def test_the_daemon_verifies_its_own_log(daemon, tmp):
    server, _svc = daemon
    admin = DaemonClient(socket_path=server.admin_socket_path, timeout_s=5.0)
    audit.append(tmp / "state", "thing")
    out = admin.call("audit.verify", {})
    assert out["ok"] and out["chained"] >= 1
    # The call itself is audited after the check; cut both records off.
    _write_lines(tmp / "state", _lines(tmp / "state")[:-2])
    out = admin.call("audit.verify", {})
    assert not out["ok"]


def test_audit_verify_cli_exit_code(tmp):
    state = tmp / "state"
    for i in range(3):
        audit.append(state, "thing", n=i)
    runner = CliRunner()
    ok = runner.invoke(cli, ["admin", "audit", "verify", "--state-dir", str(state)])
    assert ok.exit_code == 0 and "chain intact" in ok.output
    lines = _lines(state)
    del lines[1]
    _write_lines(state, lines)
    bad = runner.invoke(cli, ["admin", "audit", "verify", "--state-dir", str(state)])
    assert bad.exit_code == 1 and "removed or replaced" in bad.output


def test_each_record_is_exported_with_its_chain_fields(tmp):
    state = tmp / "state"
    seen: list[dict] = []
    audit.set_exporter(state, seen.append)
    try:
        audit.append(state, "thing", n=1)
    finally:
        audit.set_exporter(state, None)
    audit.append(state, "after")
    assert [r["event"] for r in seen] == ["thing"]
    span = record_to_otlp(seen[0])["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    attrs = {a["key"]: a["value"] for a in span["attributes"]}
    assert span["name"] == "sponsio.audit.thing"
    assert attrs["sponsio.audit.hash"]["stringValue"] == seen[0]["hash"]
    assert attrs["sponsio.audit.seq"]["intValue"] == "1"
    assert span["traceId"] == seen[0]["hash"][:32]


# ---------------------------------------------------------------------------
# P1.4 proposal review
# ---------------------------------------------------------------------------


def test_proposals_are_reviewed_and_approved_from_the_cli(daemon, tmp):
    server, _svc = daemon
    lib = tmp / "plugins" / BUCKET / "sponsio.yaml"
    before = lib.read_text()
    agent = DaemonClient(socket_path=server.socket_path, timeout_s=5.0)
    p = agent.call(
        "proposal.submit",
        {
            "kind": "plugin.append",
            "params": {
                "target": BUCKET,
                "staging_yaml": STAGING,
                "root": str(tmp / "plugins"),
            },
        },
    )
    runner = CliRunner()
    sock = ["--socket", str(server.socket_path)]
    listed = runner.invoke(cli, ["admin", "proposals", "list", *sock])
    assert p["id"] in listed.output and "at most 3 Bash calls" in listed.output
    assert lib.read_text() == before

    done = runner.invoke(
        cli, ["admin", "proposals", "approve", p["id"], "--yes", *sock]
    )
    assert done.exit_code == 0, done.output
    assert "at most 3 Bash calls" in lib.read_text()
    (approved,) = audit.read(tmp / "state", "proposal_approved")
    assert approved["change"]["descs"] == ["at most 3 Bash calls"]
    assert approved["peer"]["uid"] == os.getuid()


# ---------------------------------------------------------------------------
# P1.5 the daemon's cloud key
# ---------------------------------------------------------------------------


def test_the_daemon_keeps_its_own_key(daemon, tmp, monkeypatch):
    server, svc = daemon
    monkeypatch.setenv("SPONSIO_API_KEY", "from-the-environment")
    assert svc.cloud_api_key() == (None, None)  # no env var for the daemon
    runner = CliRunner()
    keyfile = tmp / "k"
    keyfile.write_text("sk-daemon-123\n")
    sock = ["--socket", str(server.socket_path)]
    out = runner.invoke(
        cli, ["admin", "cloud-key", "set", "--from-file", str(keyfile), *sock]
    )
    assert out.exit_code == 0, out.output
    assert svc.cloud_api_key() == ("sk-daemon-123", "daemon")
    path = tmp / "state" / "cloud.key"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    (rec,) = audit.read(tmp / "state", "cloud_key_set")
    assert "sk-daemon-123" not in json.dumps(audit.read(tmp / "state"))
    assert len(rec["fingerprint"]) == 12
    status = runner.invoke(cli, ["admin", "cloud-key", "status", *sock])
    assert rec["fingerprint"] in status.output and "state dir" in status.output


def test_a_tier1_daemon_falls_back_to_the_users_login(tmp):
    from sponsio.cloud.client import credentials_path

    creds = credentials_path()
    creds.parent.mkdir(parents=True, exist_ok=True)
    creds.write_text("api_key = sk-user\n")
    try:
        assert _service(tmp).cloud_api_key() == ("sk-user", "user")
    finally:
        creds.unlink()


# ---------------------------------------------------------------------------
# P1.5 signed rule files
# ---------------------------------------------------------------------------

ed25519 = pytest.importorskip(
    "cryptography.hazmat.primitives.asymmetric.ed25519", reason="sponsio[signing]"
)


def _keypair():
    from cryptography.hazmat.primitives import serialization

    private = ed25519.Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return private, base64.b64encode(public).decode()


def _require_signatures(tmp: Path, public_b64: str) -> None:
    from sponsio.daemon import config

    config.save(
        tmp / "state",
        config.DaemonConfig(signing_public_key=public_b64, signing_buckets=("*",)),
    )


def test_unsigned_rules_are_held(tmp):
    _private, public = _keypair()
    _require_signatures(tmp, public)
    svc = _service(tmp)
    out = svc.check(host="claude-code", payload=_bash("ls"))
    assert (
        not out["allowed"]
        and "cannot be verified against the configured key" in out["reason"]
    )


def test_signed_rules_apply_and_an_unsigned_edit_keeps_them(tmp):
    from sponsio.daemon.signing import sign_file

    private, public = _keypair()
    _require_signatures(tmp, public)
    lib = tmp / "plugins" / BUCKET / "sponsio.yaml"
    sign_file(lib, private)
    svc = _service(tmp)
    assert svc.check(host="claude-code", payload=_bash("ls"))["allowed"]
    assert not svc.check(host="claude-code", payload=_bash("rm -rf x"))["allowed"]

    # An edit without a new signature: the signed rules keep enforcing.
    edited = LIBRARY.replace("rm\\\\s+-rf", "never-matches")
    assert edited != LIBRARY
    lib.write_text(edited)
    assert not svc.check(host="claude-code", payload=_bash("rm -rf x"))["allowed"]
    (rec,) = audit.read(tmp / "state", "rules_unsigned")
    assert rec["bucket"] == BUCKET and rec["kept_previous"] is True

    # Signed, it is an ordinary weakening change: held for review, as any
    # rule removal is, and applied by a reload.
    sign_file(lib, private)
    held = svc.check(host="claude-code", payload=_bash("rm -rf x"))
    assert not held["allowed"]
    svc.rules_reload(BUCKET, None)
    assert svc.check(host="claude-code", payload=_bash("rm -rf x"))["allowed"]


def test_a_signature_by_another_key_is_refused(tmp):
    from sponsio.daemon.signing import sign_file

    _private, public = _keypair()
    other, _ = _keypair()
    _require_signatures(tmp, public)
    sign_file(tmp / "plugins" / BUCKET / "sponsio.yaml", other)
    out = _service(tmp).check(host="claude-code", payload=_bash("ls"))
    assert (
        not out["allowed"]
        and "cannot be verified against the configured key" in out["reason"]
    )


def test_packs_inside_the_engine_need_no_signature(tmp):
    from sponsio.daemon.signing import sign_file

    private, public = _keypair()
    _require_signatures(tmp, public)
    lib = tmp / "plugins" / BUCKET / "sponsio.yaml"
    lib.write_text(
        'version: "1"\nagents:\n  _host_claude_code:\n'
        "    include:\n      - sponsio:capability/shell\n"
    )
    sign_file(lib, private)
    assert _service(tmp).check(host="claude-code", payload=_bash("ls"))["allowed"]


def test_buckets_outside_the_list_need_no_signature(tmp):
    from sponsio.daemon import config

    _private, public = _keypair()
    config.save(
        tmp / "state",
        config.DaemonConfig(signing_public_key=public, signing_buckets=("other",)),
    )
    assert _service(tmp).check(host="claude-code", payload=_bash("ls"))["allowed"]

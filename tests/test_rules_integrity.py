"""Tamper evidence: rule changes that weaken enforcement are held.

At Tier 1 the agent can edit the files Sponsio reads. The daemon cannot
stop that, so it keeps enforcing what it loaded: a library change that
keeps every contract applies (``ruleset_strengthened``); one that drops
or changes a contract is held, recorded (``config_drift``) and announced
on the next hook reply, and applies only after ``sponsio rules reload``
on the admin socket. A restart onto a library weaker than the last
approved one denies that bucket until a reload. Deleting the trace log
and tripping a self-protection rule are recorded too.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from sponsio.daemon import audit
from sponsio.daemon.guard_service import GuardService
from sponsio.daemon.handlers import register_default_handlers
from sponsio.daemon.integrity import (
    CONFIG_DRIFT,
    RULESET_RELOADED,
    RULESET_STRENGTHENED,
    TAMPER_ATTEMPT,
    TRACE_DELETED,
    IntegrityWatcher,
    ruleset,
)
from sponsio.daemon.server import DaemonServer

BUCKET = "_host_claude_code"
HOST = "claude-code"


def _lib(*contracts: str) -> str:
    body = "".join(contracts) or "    contracts: []\n"
    head = "    contracts:\n" if contracts else ""
    return f'version: "1"\nagents:\n  {BUCKET}:\n{head}{body}'


NO_RM = """      - desc: "no rm -rf"
        G:
          pattern: arg_blacklist
          args: [Bash, command, ["rm\\\\s+-rf"]]
"""
NO_CURL = """      - desc: "no curl"
        G:
          pattern: arg_blacklist
          args: [Bash, command, ["\\\\bcurl\\\\b"]]
"""


@pytest.fixture
def tmp(monkeypatch):
    monkeypatch.setenv("SPONSIO_MODE", "enforce")
    d = Path(tempfile.mkdtemp(prefix="spr-", dir="/tmp"))
    (d / "plugins" / BUCKET).mkdir(parents=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _write(tmp: Path, text: str) -> None:
    (tmp / "plugins" / BUCKET / "sponsio.yaml").write_text(text)


def _svc(tmp: Path, **kw) -> GuardService:
    return GuardService(root=tmp / "plugins", state_dir=tmp / "state", **kw)


def _bash(cmd: str, session: str = "s1") -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": cmd},
        "session_id": session,
    }


def _allowed(svc: GuardService, cmd: str, session: str = "s1") -> bool:
    return svc.check(host=HOST, payload=_bash(cmd, session))["allowed"]


def _records(tmp: Path, kind: str) -> list[dict]:
    return audit.read(tmp / "state", kind)


# ---------------------------------------------------------------------------
# Contract ids
# ---------------------------------------------------------------------------


def _ids(tmp: Path, text: str) -> set[str]:
    from sponsio.config import load_config

    path = tmp / "lib.yaml"
    path.write_text(text)
    return set(ruleset(load_config(str(path))))


def test_ids_are_stable_and_ignore_the_description(tmp):
    assert _ids(tmp, _lib(NO_RM)) == _ids(tmp, _lib(NO_RM))
    renamed = NO_RM.replace("no rm -rf", "never rm -rf")
    assert _ids(tmp, _lib(NO_RM)) == _ids(tmp, _lib(renamed))


def test_a_parameter_change_is_a_new_id(tmp):
    changed = NO_RM.replace("rm\\\\s+-rf", "rm\\\\s+-r")
    assert _ids(tmp, _lib(NO_RM)) != _ids(tmp, _lib(changed))


def test_settings_have_ids_too(tmp):
    loose = _lib(NO_RM).replace(
        'version: "1"\n', 'version: "1"\ndefaults:\n  unconfigured: allow\n'
    )
    assert _ids(tmp, _lib(NO_RM)) < _ids(tmp, loose)


# ---------------------------------------------------------------------------
# Strengthening applies, weakening is held
# ---------------------------------------------------------------------------


def test_an_added_contract_applies_at_once(tmp):
    _write(tmp, _lib(NO_RM))
    svc = _svc(tmp)
    assert _allowed(svc, "curl x")
    _write(tmp, _lib(NO_RM, NO_CURL))
    assert not _allowed(svc, "curl x")
    (rec,) = _records(tmp, RULESET_STRENGTHENED)
    assert rec["bucket"] == BUCKET and rec["added"] == [f"{BUCKET}: no curl"]
    assert _records(tmp, CONFIG_DRIFT) == []


def test_a_removed_contract_is_held_reported_and_announced(tmp):
    _write(tmp, _lib(NO_RM, NO_CURL))
    svc = _svc(tmp)
    assert not _allowed(svc, "curl x")
    _write(tmp, _lib(NO_RM))
    res = svc.check(host=HOST, payload=_bash("curl x"))
    assert res["allowed"] is False  # the loaded rules still apply
    assert "sponsio rules reload" in res["reason"]  # the deny carries the warning
    assert "sponsio rules reload" in res["stderr"]
    (rec,) = _records(tmp, CONFIG_DRIFT)
    assert rec["applied"] is False and rec["removed"] == [f"{BUCKET}: no curl"]
    # Announced once, recorded once.
    again = svc.check(host=HOST, payload=_bash("curl x"))
    assert "stderr" not in again
    assert len(_records(tmp, CONFIG_DRIFT)) == 1
    assert svc.rules_status()["held"][BUCKET]["removed"] == [f"{BUCKET}: no curl"]


def test_reload_applies_a_held_change_and_records_who(tmp):
    _write(tmp, _lib(NO_RM, NO_CURL))
    svc = _svc(tmp)
    assert not _allowed(svc, "curl x")
    _write(tmp, _lib(NO_RM))
    assert not _allowed(svc, "curl x")
    svc.rules_reload(None, {"uid": os.getuid(), "gid": 0, "pid": 1})
    assert _allowed(svc, "curl x")
    (rec,) = _records(tmp, RULESET_RELOADED)
    assert rec["bucket"] == BUCKET and rec["peer"]["uid"] == os.getuid()
    assert svc.rules_status()["held"] == {}


def test_a_library_that_no_longer_loads_is_held(tmp):
    _write(tmp, _lib(NO_RM))
    svc = _svc(tmp)
    assert not _allowed(svc, "rm -rf /")
    _write(tmp, "")
    assert not _allowed(svc, "rm -rf /")
    assert _records(tmp, CONFIG_DRIFT)[0]["applied"] is False


def test_a_restart_onto_weaker_rules_denies_until_reload(tmp):
    _write(tmp, _lib(NO_RM, NO_CURL))
    assert not _allowed(_svc(tmp), "curl x")
    # Daemon down, library weakened, daemon back.
    _write(tmp, _lib(NO_RM))
    svc = _svc(tmp)
    res = svc.check(host=HOST, payload=_bash("ls"))
    assert res["allowed"] is False and "weaker than the last approved" in res["reason"]
    svc.rules_reload(BUCKET, None)
    assert _allowed(svc, "curl x") and not _allowed(svc, "rm -rf /")


def test_watch_applies_everything_and_still_records_it(tmp):
    _write(tmp, _lib(NO_RM, NO_CURL))
    svc = _svc(tmp, watch=True)
    assert not _allowed(svc, "curl x")
    _write(tmp, _lib(NO_RM))
    assert _allowed(svc, "curl x")
    (rec,) = _records(tmp, CONFIG_DRIFT)
    assert rec["applied"] is True


# ---------------------------------------------------------------------------
# Trace deletion, tamper attempts, file watch
# ---------------------------------------------------------------------------


def test_a_deleted_trace_log_is_restored_and_recorded(tmp):
    _write(tmp, _lib(NO_RM))
    svc = _svc(tmp)
    assert _allowed(svc, "ls") and _allowed(svc, "pwd")
    for wal in (tmp / "state" / "wal").glob("*.jsonl"):
        wal.unlink()
    assert _allowed(svc, "ls")
    (rec,) = _records(tmp, TRACE_DELETED)
    assert rec["problem"] == "deleted"
    (wal,) = (tmp / "state" / "wal").glob("*.jsonl")
    assert len(wal.read_text().splitlines()) == 3  # two restored, one new


def test_a_self_protection_denial_is_a_tamper_attempt(tmp):
    import sponsio
    from sponsio.cli import _refresh_per_host_bundles

    _refresh_per_host_bundles(HOST, tmp / "plugins")
    svc = _svc(tmp)
    target = Path(sponsio.__file__).parent / "guard_stdin.py"
    edit = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target), "old_string": "a", "new_string": "b"},
        "session_id": "s1",
    }
    assert svc.check(host=HOST, payload=edit)["allowed"] is False
    (rec,) = _records(tmp, TAMPER_ATTEMPT)
    assert rec["tool"] == "Edit" and "sponsio:capability/self-modify" in rec["packs"]


def test_approving_its_own_rule_change_is_denied_to_the_agent(tmp):
    from sponsio.cli import _refresh_per_host_bundles

    _refresh_per_host_bundles(HOST, tmp / "plugins")
    svc = _svc(tmp)
    for cmd in (
        "sponsio rules reload --yes",
        "sponsio host install claude-code --mode observe",
    ):
        assert not _allowed(svc, cmd), cmd
    assert _allowed(svc, "sponsio rules status")


def test_the_watcher_reports_a_changed_file(tmp):
    f = tmp / "module.py"
    f.write_text("x = 1\n")
    watcher = IntegrityWatcher(tmp / "state", lambda: [f])
    assert watcher.poll() == []
    f.write_text("x = 2\n")
    (rec,) = watcher.poll()
    assert rec["file"] == str(f) and rec["change"] == "modified"
    assert rec["old_sha256"] != rec["new_sha256"]
    assert watcher.poll() == []


# ---------------------------------------------------------------------------
# The CLI, over the admin socket
# ---------------------------------------------------------------------------


def test_rules_cli_shows_and_reloads_a_held_change(tmp):
    from sponsio.cli import cli

    _write(tmp, _lib(NO_RM, NO_CURL))
    svc = _svc(tmp)
    server = DaemonServer(tmp / "sock", accept_timeout_s=0.05)
    register_default_handlers(server, guard_service=svc)
    server.start()
    try:
        assert not _allowed(svc, "curl x")
        _write(tmp, _lib(NO_RM))
        assert not _allowed(svc, "curl x")
        sock = str(tmp / "sock")
        r = CliRunner().invoke(cli, ["rules", "diff", "--socket", sock])
        assert r.exit_code == 0 and "- _host_claude_code: no curl" in r.output
        r = CliRunner().invoke(cli, ["rules", "status", "--socket", sock, "--json"])
        assert BUCKET in json.loads(r.output)["held"]
        r = CliRunner().invoke(cli, ["rules", "reload", "--socket", sock, "--yes"])
        assert r.exit_code == 0 and "reloaded: _host_claude_code" in r.output
        assert _allowed(svc, "curl x")
    finally:
        server.stop(join_timeout_s=0.5)
    (rec,) = _records(tmp, RULESET_RELOADED)
    assert rec["peer"]["uid"] == os.getuid()

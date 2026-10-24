"""The daemon compares its loaded rules with the book the cloud publishes.

``cloud:`` in daemon.yaml links host buckets to a project's agents. The
daemon sends each linked bucket's contract ids (``agent_ruleset``) to
``POST /v1/rulebook/verify``; a ``weaker`` answer is audited as
``cloud_drift`` and announced on the next hook reply. The service here
is a loopback stand-in that computes the published ids the way the real
one does, from a yaml, with the same algorithm.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from click.testing import CliRunner

from sponsio.cloud.client import CloudClient
from sponsio.config import load_config
from sponsio.daemon import audit
from sponsio.daemon import config as dc
from sponsio.daemon.guard_service import GuardService
from sponsio.daemon.handlers import register_default_handlers
from sponsio.daemon.integrity import (
    CLOUD_DRIFT,
    CLOUD_VERIFIED,
    IDS_ALGO,
    agent_ruleset,
)
from sponsio.daemon.server import DaemonServer

BUCKET = "_host_claude_code"
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


def _yaml(agent: str, *contracts: str) -> str:
    return f'version: "1"\nagents:\n  {agent}:\n    contracts:\n' + "".join(contracts)


class _FakeCloud:
    """POST /v1/rulebook/verify against a published book (a yaml)."""

    def __init__(self, tmp: Path, published_yaml: str, agent: str = "quant"):
        path = tmp / "published.yaml"
        path.write_text(published_yaml)
        self.published = agent_ruleset(load_config(str(path)), agent)
        self.requests: list[dict] = []
        cloud = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def do_POST(self):  # noqa: N802 - http.server API
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                cloud.requests.append({"path": self.path, "body": body})
                local = set(body["ids"])
                missing = sorted(
                    v for k, v in cloud.published.items() if k not in local
                )
                status = (
                    "weaker"
                    if missing
                    else ("stronger" if local - set(cloud.published) else "match")
                )
                out = json.dumps(
                    {
                        "status": status,
                        "missing": missing,
                        "agent": agent,
                        "published_version": 4,
                        "extra": 0,
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def client(self) -> CloudClient:
        return CloudClient(api_key="sk-test", url=self.url, retries=0)

    def close(self):
        self.server.shutdown()


@pytest.fixture
def tmp(monkeypatch):
    monkeypatch.setenv("SPONSIO_MODE", "enforce")
    d = Path(tempfile.mkdtemp(prefix="spv-", dir="/tmp"))
    (d / "plugins" / BUCKET).mkdir(parents=True)
    (d / "state").mkdir()
    (d / "state" / "daemon.yaml").write_text(
        "cloud:\n  project: proj\n  buckets: {_host_claude_code: quant}\n"
    )
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _lib(tmp: Path, *contracts: str) -> None:
    (tmp / "plugins" / BUCKET / "sponsio.yaml").write_text(_yaml(BUCKET, *contracts))


def _svc(tmp: Path, **kw) -> GuardService:
    return GuardService(root=tmp / "plugins", state_dir=tmp / "state", **kw)


@pytest.fixture
def cloud(tmp):
    c = _FakeCloud(tmp, _yaml("quant", NO_RM, NO_CURL))
    yield c
    c.close()


def test_the_cloud_section_parses(tmp):
    cfg = dc.load(tmp / "state")
    assert cfg.cloud_project == "proj" and cfg.cloud_buckets == {BUCKET: "quant"}
    assert cfg.cloud_verify_interval_s == 300.0


def test_matching_rules_are_verified_once(tmp, cloud):
    _lib(tmp, NO_RM, NO_CURL)
    svc = _svc(tmp)
    assert svc.verify_cloud(cloud.client())[BUCKET]["status"] == "match"
    req = cloud.requests[0]
    assert req["path"] == "/v1/rulebook/verify?project=proj"
    assert req["body"]["agent"] == "quant" and req["body"]["bucket"] == BUCKET
    assert req["body"]["ids_algo"] == IDS_ALGO and len(req["body"]["ids"]) == 2
    svc.verify_cloud(cloud.client())
    # Recorded when the outcome changes, not on every poll.
    assert [r["status"] for r in audit.read(tmp / "state", CLOUD_VERIFIED)] == ["match"]


def test_weaker_rules_are_recorded_and_announced(tmp, cloud):
    _lib(tmp, NO_RM)
    svc = _svc(tmp)
    result = svc.verify_cloud(cloud.client())[BUCKET]
    assert result["status"] == "weaker" and result["missing"] == ["no curl"]
    (rec,) = audit.read(tmp / "state", CLOUD_DRIFT)
    assert rec["agent"] == "quant" and rec["missing"] == ["no curl"]
    reply = svc.check(
        host="claude-code",
        payload={
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "session_id": "s",
        },
    )
    assert "sponsio rules verify" in reply["stderr"]
    assert svc.rules_status()["cloud"][BUCKET]["status"] == "weaker"


def test_an_unreachable_cloud_is_a_status_not_a_deny(tmp):
    _lib(tmp, NO_RM)
    svc = _svc(tmp)
    dead = CloudClient(api_key="sk-test", url="http://127.0.0.1:9", retries=0)
    assert svc.verify_cloud(dead)[BUCKET]["status"] == "unreachable"
    reply = svc.check(
        host="claude-code",
        payload={
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "session_id": "s",
        },
    )
    assert reply["allowed"] is True


def test_no_key_and_no_link(tmp):
    _lib(tmp, NO_RM)
    svc = _svc(tmp)
    no_key = CloudClient(api_key="", url="http://127.0.0.1:9", retries=0)
    assert svc.verify_cloud(no_key)[BUCKET]["status"] == "no_key"
    (tmp / "state" / "daemon.yaml").write_text("mode: enforce\n")
    assert svc.verify_cloud(no_key) == {}


def test_rules_verify_cli_over_the_admin_socket(tmp, cloud, monkeypatch):
    from sponsio.cli import cli

    _lib(tmp, NO_RM)
    monkeypatch.setenv("SPONSIO_API_KEY", "sk-test")
    monkeypatch.setenv("SPONSIO_API_URL", cloud.url)
    svc = _svc(tmp)
    server = DaemonServer(tmp / "sock", accept_timeout_s=0.05)
    register_default_handlers(server, guard_service=svc)
    server.start()
    try:
        r = CliRunner().invoke(cli, ["rules", "verify", "--socket", str(tmp / "sock")])
    finally:
        server.stop(join_timeout_s=0.5)
    assert r.exit_code == 0, r.output
    assert "weaker" in r.output and "- no curl" in r.output
    assert "(cloud quant v4)" in r.output

"""One malformed message from an upstream (a reply whose ``id`` is not
hashable, e.g. a list) raises inside ``Upstream._on_message`` on the
reader thread and kills it. The process stays alive, so it is never
restarted: every later request to that server waits out its timeout."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from sponsio.daemon.gateway import GatewayError, ServerSpec, Upstream

FAKE = Path(__file__).parent / "fake_upstream.py"


def test_a_malformed_upstream_reply_does_not_wedge_the_server(monkeypatch):
    d = tempfile.mkdtemp(prefix="sp-rv6-", dir="/tmp")
    up = Upstream(
        ServerSpec(
            name="pay",
            command=(sys.executable, str(FAKE)),
            env=(("CALL_LOG", os.path.join(d, "calls.jsonl")), ("JUNK_ID", "1")),
            timeout_s=2.0,
        )
    )
    try:
        try:
            names = [t["name"] for t in up.tools()]
        except GatewayError as e:
            names = repr(e)
        assert names == ["check", "send", "big"]
    finally:
        up.close()
        shutil.rmtree(d, ignore_errors=True)

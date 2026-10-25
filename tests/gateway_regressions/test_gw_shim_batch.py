"""The shim negotiates MCP 2025-03-26 (whose transport requires a
receiver to accept JSON-RPC batches) but answers any batch with a single
``invalid request`` error carrying ``id: null``: no request in the batch
gets a reply under its own id, so a host waiting on them never hears
back."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from sponsio.shim import mcp_shim_path


def test_a_batch_gets_a_reply_per_request():
    d = Path(tempfile.mkdtemp(prefix="sp-rv5-", dir="/tmp"))
    proc = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-S",
            str(mcp_shim_path()),
            "--socket",
            str(d / "absent.sock"),
            "--no-start",
            "--timeout",
            "5",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(d)},
    )
    try:
        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26"},
                }
            )
            + "\n"
        )
        proc.stdin.flush()
        init = json.loads(proc.stdout.readline())
        assert init["result"]["protocolVersion"] == "2025-03-26"
        proc.stdin.write(
            json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "ping"}]) + "\n"
        )
        proc.stdin.flush()
        reply = json.loads(proc.stdout.readline())
        replies = reply if isinstance(reply, list) else [reply]
        assert [r.get("id") for r in replies] == [1], reply
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(d, ignore_errors=True)

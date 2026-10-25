"""A stdio MCP server for the gateway regression tests. Standard library only.

Tools: ``check``, ``send``, ``big``.

* ``check`` with ``{"reject": true}`` is refused with a JSON-RPC
  invalid-params error: per MCP that is a protocol error, the tool did
  not run (nothing is logged).
* With ``JUNK_ID`` set, every reply is preceded by a malformed one
  whose ``id`` is a list.
* ``big`` returns a text block of ``BIG_BYTES`` bytes.

Every executed ``tools/call`` is appended to ``CALL_LOG``.
"""

import json
import os
import sys

TOOLS = [
    {"name": "check", "inputSchema": {"type": "object"}},
    {"name": "send", "inputSchema": {"type": "object"}},
    {"name": "big", "inputSchema": {"type": "object"}},
]


def out(msg):
    if os.environ.get("JUNK_ID") and "id" in msg and "method" not in msg:
        # A malformed reply first: its id is not a JSON-RPC id.
        sys.stdout.write(
            json.dumps({"jsonrpc": "2.0", "id": [msg["id"]], "result": {}}) + "\n"
        )
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    msg = json.loads(line)
    method, rid = msg.get("method"), msg.get("id")
    if rid is None:
        continue
    params = msg.get("params") or {}
    if method == "initialize":
        out(
            {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "protocolVersion": params.get("protocolVersion"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "pay", "version": "0"},
                },
            }
        )
    elif method == "tools/list":
        out({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "check" and args.get("reject"):
            out(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"code": -32602, "message": "invalid params"},
                }
            )
            continue
        with open(os.environ["CALL_LOG"], "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"name": name}) + "\n")
        if name == "big":
            text = "x" * int(os.environ.get("BIG_BYTES", "10"))
        else:
            text = f"{name} done"
        out(
            {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {"content": [{"type": "text", "text": text}]},
            }
        )
    else:
        out({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "nope"}})

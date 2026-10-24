"""A stdio MCP server for the gateway tests. Standard library only.

Tools: ``check``, ``send`` (replies with the ``ACME_TOKEN`` it was
started with, to show where credentials come from) and ``wipe``. The
list comes in two pages. Every ``tools/call`` is appended to the file
named by ``CALL_LOG``, so a test can tell whether a call reached it.
"""

import json
import os
import sys

TOOLS = [
    {
        "name": "check",
        "description": "Check the request.",
        "inputSchema": {"type": "object"},
    },
    {
        "name": "send",
        "description": "Send it.",
        "inputSchema": {"type": "object", "properties": {"to": {"type": "string"}}},
    },
    {
        "name": "wipe",
        "description": "Delete everything.",
        "inputSchema": {"type": "object"},
    },
]


def reply(rid, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    msg = json.loads(line)
    method, rid = msg.get("method"), msg.get("id")
    if rid is None:
        continue
    params = msg.get("params") or {}
    if method == "initialize":
        reply(
            rid,
            {
                "protocolVersion": params.get("protocolVersion"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "acme", "version": "0"},
            },
        )
    elif method == "tools/list":
        if params.get("cursor") == "page2":
            reply(rid, {"tools": TOOLS[2:]})
        else:
            reply(rid, {"tools": TOOLS[:2], "nextCursor": "page2"})
    elif method == "tools/call":
        name = params.get("name")
        log = os.environ.get("CALL_LOG")
        if log:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps({"name": name, "arguments": params.get("arguments")})
                    + "\n"
                )
        sys.stderr.write(f"called {name}\n")
        text = (
            f"token={os.environ.get('ACME_TOKEN', '')}"
            if name == "send"
            else f"{name} done"
        )
        reply(rid, {"content": [{"type": "text", "text": text}]})
    else:
        sys.stdout.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"code": -32601, "message": "nope"},
                }
            )
            + "\n"
        )
        sys.stdout.flush()

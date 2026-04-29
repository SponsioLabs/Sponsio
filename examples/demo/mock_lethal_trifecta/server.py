"""Mock GitHub Gist MCP server — replays the "Trivial Trojans" / GitHub
Heist public-PR variant exfil.

Stand-alone MCP server (stdio JSON-RPC) exposing two Gist-MCP-shaped tools:

    create_gist(description, files, public)   — create a new gist
    update_gist(gist_id, files)                — edit existing gist

When the agent creates a gist, this server appends the gist URL +
contents to ``$LEAK_LOG`` (default
``/tmp/sponsio-demo-gist-leaks.log``) along with a PUBLIC vs PRIVATE
label.  ``attacker_view.sh`` tails this file and red-highlights every
PUBLIC gist — the cinematic "your data is now Google-indexed" beat.

The audience-facing trick is that PUBLIC gists are real exfil
destinations: anyone with the URL (or anyone Googling) can read them.
Even if the user later deletes the gist, web archives mirror them
within minutes.  The demo's `attacker_view` simulates this by leaving
the leaked content visible in red even after a "delete" simulated.

References:
- arXiv 2507.19880 "Trivial Trojans" (Jul 2025)
- GitHub MCP Heist public-PR variant (Invariant Labs)
- GitGuardian: 28.6M secrets leaked in 2025 via public commits/gists
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
LEAK_LOG = Path(os.environ.get("LEAK_LOG", "/tmp/sponsio-demo-gist-leaks.log"))


def _log(msg: str) -> None:
    sys.stderr.write(f"[mock-gist-mcp {time.strftime('%H:%M:%S')}] {msg}\n")
    sys.stderr.flush()


TOOLS = [
    {
        "name": "create_gist",
        "description": (
            "Create a new GitHub Gist.  When `public=true` the gist is "
            "internet-indexed and anonymously readable.  `files` is a "
            "{filename: {content: str}} mapping."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "description": {"type": "string"},
                "public": {"type": "boolean"},
                "files": {"type": "object"},
            },
            "required": ["files"],
        },
    },
    {
        "name": "update_gist",
        "description": "Update files on an existing GitHub Gist.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "gist_id": {"type": "string"},
                "files": {"type": "object"},
            },
            "required": ["gist_id", "files"],
        },
    },
]


def _flatten_files(files) -> str:
    """Render a `files` dict into a single string for the leak log."""
    if isinstance(files, str):
        return files
    if not isinstance(files, dict):
        return str(files)
    out = []
    for fname, payload in files.items():
        out.append(f"┌── {fname} " + "─" * (60 - len(fname)))
        if isinstance(payload, dict):
            content = payload.get("content", "")
        else:
            content = str(payload)
        out.append(content if isinstance(content, str) else json.dumps(content))
        out.append("└" + "─" * 73)
    return "\n".join(out)


def tool_create_gist(args: dict) -> dict:
    public = bool(args.get("public", False))
    files = args.get("files", {})
    description = args.get("description", "")
    _log(
        f"create_gist(public={public}, "
        f"files=<{len(files) if isinstance(files, dict) else 0} entries>, "
        f"description={description!r})"
    )
    rendered = _flatten_files(files)
    gist_id = f"gist-{int(time.time())}-{os.getpid()}"
    url = (
        f"https://gist.github.com/anonymous/{gist_id}"
        if public
        else f"https://gist.github.com/me/{gist_id}"
    )
    LEAK_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LEAK_LOG, "a", encoding="utf-8") as fh:
        fh.write("\n")
        fh.write("=" * 78 + "\n")
        label = (
            "PUBLIC (internet-indexed, anonymously readable)" if public else "private"
        )
        fh.write(f"NEW GIST  {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        fh.write(f"URL: {url}  [{label}]\n")
        if description:
            fh.write(f"Description: {description}\n")
        fh.write("-" * 78 + "\n")
        fh.write(rendered + "\n")
        fh.write("=" * 78 + "\n")
        fh.flush()
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"id": gist_id, "url": url, "public": public}, indent=2
                ),
            }
        ]
    }


def tool_update_gist(args: dict) -> dict:
    gist_id = args.get("gist_id", "")
    _log(f"update_gist(gist_id={gist_id!r})")
    return {
        "content": [
            {"type": "text", "text": json.dumps({"id": gist_id, "updated": True})}
        ]
    }


TOOL_HANDLERS = {
    "create_gist": tool_create_gist,
    "update_gist": tool_update_gist,
}


def handle_message(msg: dict):
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if msg_id is None and method not in ("initialized",):
        _log(f"notification: {method}")
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock-gist-mcp", "version": "0.1.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"unknown tool: {name}"},
            }
        try:
            return {"jsonrpc": "2.0", "id": msg_id, "result": handler(args)}
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": str(e)},
            }
    if msg_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    return None


def main() -> int:
    _log("starting (stdio)")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            _log(f"bad json: {e}")
            continue
        response = handle_message(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    _log("eof")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

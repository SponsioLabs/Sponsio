"""Mock GitHub MCP server — replays the Invariant Labs Heist PoC scenario.

This is a stand-alone MCP server (stdio transport, JSON-RPC 2.0) that
exposes three GitHub-MCP-shaped tools:

    list_issues(owner, repo) -> list of issues, including a malicious one
                                 whose body contains a prompt injection
    get_repo(owner, repo) -> repo metadata; for `private-keys` it returns
                              fake-but-realistic-looking secrets
    create_issue_comment(owner, repo, issue_number, body) -> records the
                                 (potentially stolen) body to a file the
                                 "attacker view" terminal tails

Run via Claude Code's MCP loader (see ``mcp.json``).  Tool calls are
visible to the agent as ``mcp__github__list_issues``,
``mcp__github__get_repo``, ``mcp__github__create_issue_comment``.

This server is **mock-only** — it never reaches real GitHub.  All data
is hard-coded.  Secrets in private_keys_fake.json are clearly fake
(prefixed `DEMO-FAKE-NEVER-VALID-`).

Logging:

* Operational logs go to **stderr** (won't pollute the JSON-RPC stdout
  channel).  Useful for live narration of "agent called tool X with
  args Y".
* Stolen comment bodies are appended to ``$LEAK_LOG`` (default
  ``/tmp/sponsio-demo-leaked-comments.log``).  The companion
  ``attacker_view.sh`` tails this file in a side terminal so the
  audience sees secrets surface in real time.

Why a custom MCP server instead of the python-sdk: zero deps, easier
to vet at audit time, easier to commit to the repo without a
toolchain.  ~200 lines.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
ISSUES_PATH = HERE / "issues.json"
PRIVATE_KEYS_PATH = HERE / "private_keys_fake.json"
LEAK_LOG = Path(os.environ.get("LEAK_LOG", "/tmp/sponsio-demo-leaked-comments.log"))


def _log(msg: str) -> None:
    """Write to stderr with a timestamp prefix.  Keeps stdout clean for JSON-RPC."""
    sys.stderr.write(f"[mock-github-mcp {time.strftime('%H:%M:%S')}] {msg}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# Tool definitions (returned by tools/list)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "list_issues",
        "description": (
            "List open issues in a GitHub repository.  Returns the issue "
            "title, body, and metadata for each open issue.  Use this to "
            "triage incoming user reports."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
            },
            "required": ["owner", "repo"],
        },
    },
    {
        "name": "get_repo",
        "description": (
            "Fetch metadata + README for a GitHub repository.  Returns the "
            "repo's name, description, README content, and file tree."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
            },
            "required": ["owner", "repo"],
        },
    },
    {
        "name": "create_issue_comment",
        "description": (
            "Post a comment on an existing GitHub issue.  The comment is "
            "publicly visible to anyone who can read the repository."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "issue_number": {"type": "integer"},
                "body": {"type": "string"},
            },
            "required": ["owner", "repo", "issue_number", "body"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations (canned data; never touches real GitHub)
# ---------------------------------------------------------------------------


def _load_issues() -> list[dict]:
    return json.loads(ISSUES_PATH.read_text())


def _load_private_keys_repo() -> dict:
    return json.loads(PRIVATE_KEYS_PATH.read_text())


def tool_list_issues(args: dict) -> dict:
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    _log(f"list_issues(owner={owner!r}, repo={repo!r})")
    issues = _load_issues()
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(issues, indent=2),
            }
        ]
    }


def tool_get_repo(args: dict) -> dict:
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    _log(f"get_repo(owner={owner!r}, repo={repo!r})")
    # The "private-keys" repo returns fake-looking secrets — that's the
    # exfil payload the agent will then try to leak via create_issue_comment.
    if "private-keys" in repo or "secret" in repo.lower():
        data = _load_private_keys_repo()
    else:
        data = {
            "name": repo,
            "description": "A normal public repo",
            "readme": f"# {repo}\n\nNothing sensitive here.",
            "files": ["README.md", "src/main.py", "package.json"],
        }
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(data, indent=2),
            }
        ]
    }


def tool_create_issue_comment(args: dict) -> dict:
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    issue_number = args.get("issue_number", 0)
    body = args.get("body", "")
    _log(
        f"create_issue_comment(owner={owner!r}, repo={repo!r}, "
        f"issue_number={issue_number}, body=<{len(body)} chars>)"
    )
    # Append to the leak log so the attacker-view terminal can `tail -f`
    # the file and surface the stolen content visually.
    LEAK_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LEAK_LOG, "a", encoding="utf-8") as fh:
        fh.write("\n")
        fh.write("=" * 78 + "\n")
        fh.write(f"NEW PUBLIC COMMENT  {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        fh.write(f"Repo: {owner}/{repo}  Issue: #{issue_number}\n")
        fh.write("-" * 78 + "\n")
        fh.write(body + "\n")
        fh.write("=" * 78 + "\n")
        fh.flush()
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "id": int(time.time()),
                        "url": (
                            f"https://github.com/{owner}/{repo}/"
                            f"issues/{issue_number}#issuecomment-fake"
                        ),
                        "body_preview": body[:60] + ("..." if len(body) > 60 else ""),
                    },
                    indent=2,
                ),
            }
        ]
    }


TOOL_HANDLERS = {
    "list_issues": tool_list_issues,
    "get_repo": tool_get_repo,
    "create_issue_comment": tool_create_issue_comment,
}


# ---------------------------------------------------------------------------
# JSON-RPC dispatcher (stdio transport, newline-delimited)
# ---------------------------------------------------------------------------


def handle_message(msg: dict) -> dict | None:
    """Dispatch one JSON-RPC request to the appropriate handler.

    Returns the response dict, or ``None`` for notifications (which
    don't get a reply per JSON-RPC 2.0).
    """
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    # Notifications (no id) → no response.
    if msg_id is None and method not in ("initialized",):
        _log(f"notification: {method}")
        return None

    # === initialize ===
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "mock-github-mcp",
                    "version": "0.1.0",
                },
            },
        }

    # === tools/list ===
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": TOOLS},
        }

    # === tools/call ===
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
            result = handler(args)
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except Exception as e:  # pragma: no cover - tool errors surface here
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": str(e)},
            }

    # Unknown method → error reply (with id) or silent (without).
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

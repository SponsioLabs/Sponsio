#!/usr/bin/env bash
# Bootstrap a clean demo HOME for the GitHub MCP heist recording.
#
# Usage:
#   ./setup.sh             # creates /tmp/sponsio-demo-home and configures it
#   ./setup.sh --reset     # wipes and recreates from scratch
#   DEMO_HOME=... ./setup.sh   # use a custom HOME location
#
# What this script does:
#   1. Creates an isolated HOME at $DEMO_HOME (default /tmp/sponsio-demo-home)
#   2. Writes ~/.claude/mcp.json pointing at the local mock MCP server
#   3. Writes ~/.sponsio/plugins/github/sponsio.yaml with the operator allowlist
#   4. Creates a fresh empty leak log
#   5. Prints the 3 commands the operator runs to record the demo
#
# It does NOT touch your real ~/.claude or ~/.sponsio.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPONSIO_ROOT="$(cd "$HERE/../../.." && pwd)"
DEMO_HOME="${DEMO_HOME:-/tmp/sponsio-demo-home}"
LEAK_LOG="${LEAK_LOG:-/tmp/sponsio-demo-leaked-comments.log}"

if [[ "${1:-}" == "--reset" ]]; then
  echo "→ wiping $DEMO_HOME"
  rm -rf "$DEMO_HOME"
  rm -f "$LEAK_LOG"
fi

mkdir -p "$DEMO_HOME"

# 1. Claude Code MCP config — point at our mock server
#    Modern Claude Code reads MCP servers from `~/.claude.json`
#    (per-user settings, written by `claude mcp add`).  We register
#    it programmatically with HOME pointing at the demo home so the
#    real ~/.claude.json stays untouched.  Also write the legacy
#    standalone mcp.json so the operator can still pass
#    `--mcp-config $HOME/.claude/mcp.json` if they prefer.
mkdir -p "$DEMO_HOME/.claude"
sed "s|SERVER_PATH|$HERE/server.py|" "$HERE/mcp.json" \
  > "$DEMO_HOME/.claude/mcp.json"
echo "✓ wrote $DEMO_HOME/.claude/mcp.json (for --mcp-config)"

# Also register via `claude mcp add` so a plain `claude` (without
# `--mcp-config`) picks it up.  Skip silently if `claude` isn't on
# PATH — the --mcp-config fallback still works in that case.
if command -v claude >/dev/null 2>&1; then
  HOME="$DEMO_HOME" claude mcp remove github --scope user >/dev/null 2>&1 || true
  HOME="$DEMO_HOME" claude mcp add github \
    --scope user \
    -e "LEAK_LOG=$LEAK_LOG" \
    -- python3 "$HERE/server.py" \
    && echo "✓ registered github MCP via 'claude mcp add' in $DEMO_HOME"
fi

# 2. Sponsio per-plugin library — github plugin gets the bundle + allowlist
mkdir -p "$DEMO_HOME/.sponsio/plugins/github"
cp "$HERE/operator_sponsio.yaml" \
   "$DEMO_HOME/.sponsio/plugins/github/sponsio.yaml"
echo "✓ wrote $DEMO_HOME/.sponsio/plugins/github/sponsio.yaml"

# 3. Sponsio host-plugin library — initialize with default _host
#    This is what `sponsio plugin init` would do; we run it explicitly
#    so the demo doesn't rely on a separate manual step.
if [[ ! -d "$DEMO_HOME/.sponsio/plugins/_host" ]]; then
  mkdir -p "$DEMO_HOME/.sponsio/plugins/_host"
  cp "$SPONSIO_ROOT/sponsio/plugin/defaults/_host.yaml" \
     "$DEMO_HOME/.sponsio/plugins/_host/sponsio.yaml"
  echo "✓ wrote $DEMO_HOME/.sponsio/plugins/_host/sponsio.yaml (default)"
fi

# 4. Fresh leak log
: > "$LEAK_LOG"
echo "✓ primed $LEAK_LOG (empty)"

# 5. Print the recording trio
cat <<EOF

─── Demo HOME ready ────────────────────────────────────────────────────

  HOME            $DEMO_HOME
  Mock MCP        $HERE/server.py
  Leak log        $LEAK_LOG
  Sponsio repo    $SPONSIO_ROOT

─── Recording — 3 terminals (recommended layout) ───────────────────────

  Terminal A (left, 70% width) — the recorded "user" terminal:
    export HOME=$DEMO_HOME
    cd \$HOME
    # for "without Sponsio" take:
    claude --debug
    # for "with Sponsio" take:
    claude --debug --plugin-dir $SPONSIO_ROOT/plugins/sponsio-claude-code

  Terminal B (right, 30% width) — the recorded "attacker view":
    bash $HERE/attacker_view.sh

  Terminal C (off-screen) — for resets between takes:
    $HERE/setup.sh --reset

  See $HERE/RUN.md for the full recording script + prompts.

────────────────────────────────────────────────────────────────────────
EOF

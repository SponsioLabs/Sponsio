#!/usr/bin/env bash
# Right-screen "attacker view" for the GitHub MCP heist demo.
#
# Tails the leak log written by mock_github_mcp/server.py and prints
# each new comment with red highlighting.  An empty terminal here at
# the end of the demo IS the visual win for the "with Sponsio" take.
#
# Usage:
#   ./attacker_view.sh
#
# Override the watched file:
#   LEAK_LOG=/path/to/log ./attacker_view.sh

set -euo pipefail

LEAK_LOG="${LEAK_LOG:-/tmp/sponsio-demo-leaked-comments.log}"

# ANSI colors
RED=$'\033[91m'
DIM=$'\033[2m'
RESET=$'\033[0m'
BOLD=$'\033[1m'

mkdir -p "$(dirname "$LEAK_LOG")"
: >> "$LEAK_LOG"   # ensure file exists for tail

clear
cat <<EOF
${BOLD}─── attacker.io view — public comments on issue tracker ───${RESET}
${DIM}Watching $LEAK_LOG${RESET}
${DIM}(any comment posted to a public issue shows up here in real time)${RESET}

EOF

# Tail and re-color each line.  Lines containing KEY=VAL or sk-/ghp_/sk_test
# tokens are highlighted red — the audience visually sees secrets surface.
tail -n 0 -F "$LEAK_LOG" 2>/dev/null | while IFS= read -r line; do
  if [[ "$line" =~ (sk-|ghp_|sk_test_|sk_live_|AKIA|ASIA) ]] \
     || [[ "$line" =~ [A-Z][A-Z0-9_]+= ]]; then
    printf "%s%s%s\n" "$RED" "$line" "$RESET"
  else
    printf "%s%s%s\n" "$DIM" "$line" "$RESET"
  fi
done

#!/usr/bin/env bash
# Right-screen view for the docker-compose-bypass demo.
#
# Tails the leak log written by attacker_server.py and red-highlights
# any line containing KEY=VAL or sk-/ghp_/sk_test_ tokens.  An empty
# pane at the end of the "with Sponsio" take is the visible win.
#
# Usage:
#   ./attacker_view.sh
# Override the watched file:
#   LEAK_LOG=/path ./attacker_view.sh

set -euo pipefail

LEAK_LOG="${LEAK_LOG:-/tmp/sponsio-demo-leaked-secrets.log}"

RED=$'\033[91m'
DIM=$'\033[2m'
RESET=$'\033[0m'
BOLD=$'\033[1m'

mkdir -p "$(dirname "$LEAK_LOG")"
: >> "$LEAK_LOG"

clear
cat <<EOF
${BOLD}─── attacker.io view — POSTs received ───${RESET}
${DIM}Watching $LEAK_LOG${RESET}
${DIM}(any HTTP body received on :9999 surfaces here in real time)${RESET}

EOF

tail -n 0 -F "$LEAK_LOG" 2>/dev/null | while IFS= read -r line; do
  if [[ "$line" =~ (sk-|ghp_|sk_test_|sk_live_|AKIA|ASIA) ]] \
     || [[ "$line" =~ [A-Z][A-Z0-9_]+= ]]; then
    printf "%s%s%s\n" "$RED" "$line" "$RESET"
  else
    printf "%s%s%s\n" "$DIM" "$line" "$RESET"
  fi
done

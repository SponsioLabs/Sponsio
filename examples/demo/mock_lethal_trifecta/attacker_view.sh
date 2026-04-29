#!/usr/bin/env bash
# Right-screen "Public Gist viewer" for the Lethal Trifecta demo.
#
# Tails the gist leak log and red-highlights any PUBLIC gist plus any
# line containing salary amounts, ARR figures, or
# termination-decision keywords.  An empty pane at the end of the
# "with Sponsio" take = the visible win.
#
# Usage:
#   ./attacker_view.sh
#   LEAK_LOG=/path ./attacker_view.sh

set -euo pipefail

LEAK_LOG="${LEAK_LOG:-/tmp/sponsio-demo-gist-leaks.log}"

RED=$'\033[91m'
GREEN=$'\033[92m'
YELLOW=$'\033[93m'
DIM=$'\033[2m'
RESET=$'\033[0m'
BOLD=$'\033[1m'

mkdir -p "$(dirname "$LEAK_LOG")"
: >> "$LEAK_LOG"

clear
cat <<EOF
${BOLD}─── gist.github.com — public-gist scraper view ───${RESET}
${DIM}Watching $LEAK_LOG${RESET}
${DIM}(any gist created with public:true surfaces here in red)${RESET}
${DIM}(salary / ARR / PIP language is highlighted on every line)${RESET}

EOF

in_public=0
tail -n 0 -F "$LEAK_LOG" 2>/dev/null | while IFS= read -r line; do
  if [[ "$line" =~ NEW\ GIST ]]; then
    in_public=0
  fi
  if [[ "$line" =~ \[PUBLIC ]]; then
    in_public=1
    printf "%s%s%s\n" "$RED$BOLD" "$line" "$RESET"
    continue
  fi
  if [[ "$line" =~ \[private\] ]]; then
    in_public=0
    printf "%s%s%s\n" "$GREEN" "$line" "$RESET"
    continue
  fi
  if (( in_public )); then
    # Inside a public gist — highlight any leaked-data shapes
    if [[ "$line" =~ \$[0-9]+,[0-9]+|\$[0-9]+k\b|ARR|MRR|PIP|terminat|exit\ by|severance ]]; then
      printf "%s%s%s\n" "$RED$BOLD" "$line" "$RESET"
    else
      printf "%s%s%s\n" "$RED" "$line" "$RESET"
    fi
  else
    printf "%s%s%s\n" "$DIM" "$line" "$RESET"
  fi
done

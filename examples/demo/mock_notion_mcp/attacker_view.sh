#!/usr/bin/env bash
# Right-screen "attacker view" for the Notion lethal-trifecta demo.
#
# Tails the Notion-page leak log and red-highlights any page that
# lands in the public (attacker-visible) workspace.  An empty pane at
# the end of the "with Sponsio" take = the visual win.
#
# Usage:
#   ./attacker_view.sh
#   LEAK_LOG=/path ./attacker_view.sh

set -euo pipefail

LEAK_LOG="${LEAK_LOG:-/tmp/sponsio-demo-notion-leaks.log}"

RED=$'\033[91m'
GREEN=$'\033[92m'
DIM=$'\033[2m'
RESET=$'\033[0m'
BOLD=$'\033[1m'

mkdir -p "$(dirname "$LEAK_LOG")"
: >> "$LEAK_LOG"

clear
cat <<EOF
${BOLD}─── Public Notion workspace view ───${RESET}
${DIM}Watching $LEAK_LOG${RESET}
${DIM}(any page created in ws-public-leak-9999 surfaces here in red)${RESET}

EOF

current_block=""
in_public=0
tail -n 0 -F "$LEAK_LOG" 2>/dev/null | while IFS= read -r line; do
  # Detect block boundaries and label
  if [[ "$line" == "================================================================================"* ]] \
     || [[ "$line" == "==============================================================================" ]]; then
    in_public=0
    printf "%s%s%s\n" "$DIM" "$line" "$RESET"
    continue
  fi
  if [[ "$line" =~ Workspace:[[:space:]]+ws-public-leak-9999 ]]; then
    in_public=1
  elif [[ "$line" =~ Workspace:[[:space:]]+ws-user-private ]]; then
    in_public=0
  fi
  if (( in_public )); then
    printf "%s%s%s\n" "$RED" "$line" "$RESET"
  else
    printf "%s%s%s\n" "$DIM" "$line" "$RESET"
  fi
done

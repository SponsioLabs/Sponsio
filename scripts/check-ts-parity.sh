#!/usr/bin/env bash
# scripts/check-ts-parity.sh
#
# Asserts that Python <-> TS resources that *must* match haven't
# drifted. Exits non-zero on any drift so this can wire into CI.
# Pair with scripts/sync-ts-mirror.sh, which fixes the drift it
# can fix automatically.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO_ROOT/sponsio"
TS_SDK="$REPO_ROOT/ts/packages/sdk"
TS_SCAN="$REPO_ROOT/ts/packages/scanner"

cd "$REPO_ROOT"

failures=0

check_dir() {
  local label="$1"
  local left="$2"
  local right="$3"
  if ! diff -r --exclude='__pycache__' --exclude='*.pyc' --exclude='__init__.py' "$left" "$right" > /tmp/sponsio-parity-$$.diff 2>&1; then
    echo "✗ $label drifted"
    head -20 /tmp/sponsio-parity-$$.diff
    failures=$((failures + 1))
  else
    echo "✓ $label in sync"
  fi
  rm -f /tmp/sponsio-parity-$$.diff
}

check_dir "contracts/" "$PY/contracts" "$TS_SDK/contracts"
check_dir "prompts/" "$PY/prompts" "$TS_SCAN/prompts"

if [ "$failures" -gt 0 ]; then
  echo ""
  echo "Run scripts/sync-ts-mirror.sh to fix the drift on copy-able resources."
  exit 1
fi
echo ""
echo "All mirrored resources in sync."

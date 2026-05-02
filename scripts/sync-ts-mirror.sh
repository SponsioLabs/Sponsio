#!/usr/bin/env bash
# scripts/sync-ts-mirror.sh
#
# Canonical Python -> TS mirror runner. Cps the resources that must
# stay byte-identical across the two implementations and warns about
# the ones that need manual port (cross-language scenario test wiring,
# demo scenarios in demo.ts, etc).
#
# Run before committing Python-side changes to anything in the
# "must stay in sync" table (see CLAUDE.md / memory).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO_ROOT/sponsio"
TS_SDK="$REPO_ROOT/ts/packages/sdk"
TS_SCAN="$REPO_ROOT/ts/packages/scanner"

cd "$REPO_ROOT"

echo "→ Mirroring contract pack library"
mkdir -p "$TS_SDK/contracts"
rsync -a --delete --exclude='__pycache__' "$PY/contracts/" "$TS_SDK/contracts/"

echo "→ Mirroring agent-facing prompts"
mkdir -p "$TS_SCAN/prompts"
rsync -a --delete --exclude='__pycache__' --exclude='__init__.py' "$PY/prompts/" "$TS_SCAN/prompts/"

echo "→ Mirroring init_examples scaffold"
mkdir -p "$TS_SCAN/init_examples"
rsync -a --delete --exclude='__pycache__' --exclude='__init__.py' --exclude='*.pyc' "$PY/init_examples/" "$TS_SCAN/init_examples/"

echo "→ Mirror complete."
echo ""
echo "Manual port still required for:"
echo "  • SKILL.md  (TS may be a deliberate subset; diff and review):"
echo "      diff -u $PY/skills/sponsio/SKILL.md $TS_SCAN/skills/SKILL.md | head"
echo "  • Demo scenarios in $TS_SCAN/src/demo.ts (hand-port from sponsio/demos/)"
echo "  • Cross-language scenarios test"
echo "      Python: tests/cross_language/test_python.py"
echo "      TS:     ts/packages/sdk/src/__tests__/parity.scenarios.test.ts"
echo ""
echo "Run \`scripts/check-ts-parity.sh\` to assert nothing has drifted."

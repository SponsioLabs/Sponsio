#!/usr/bin/env bash
# Bootstrap the Cursor "code freeze + drop tables" demo.
#
# Architecture fix vs the previous version:
#
#   * policy.md lives OUTSIDE the Cursor workspace, under
#     $DEMO_HOME/team-docs/.  This mirrors reality — your
#     engineering policy doc lives in your team's wiki / Notion,
#     NOT committed to the repo where Cursor's agent reads it.
#     Putting policy.md inside the workspace caused Cursor's agent
#     to read it on session start and self-censor before Sponsio's
#     hooks could fire.  Demo broken.  Fixed.
#
#   * .cursorrules is benign dev-style (formatting, dependencies),
#     mentions nothing about freeze.  The agent doesn't know there's
#     a freeze in effect — it only learns when Sponsio's hook
#     denies its first destructive call.
#
#   * Hooks installed via `sponsio host install cursor --scope project`
#     so they live in $PROJECT/.cursor/hooks.json (not user-scope).
#     Means demo doesn't touch your real ~/.cursor.
#
#   * sponsio.yaml generated from policy.md via `sponsio scan` is
#     shown as the Right Way; for demo predictability we ship a
#     curated equivalent and copy it in.  See USER_FLOW.md for the
#     scan-based path.
#
# Usage:
#   ./setup.sh             # fresh setup
#   ./setup.sh --reset     # wipe and recreate
#   DEMO_HOME=... ./setup.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPONSIO_ROOT="$(cd "$HERE/../../../.." && pwd)"
DEMO_HOME="${DEMO_HOME:-/tmp/sponsio-cursor-freeze-demo}"

if [[ "${1:-}" == "--reset" ]]; then
  echo "→ wiping $DEMO_HOME"
  rm -rf "$DEMO_HOME"
fi

mkdir -p "$DEMO_HOME"

# ─── 1. Operator-side policy doc (NOT in Cursor's workspace) ─────────
TEAM_DOCS="$DEMO_HOME/team-docs"
mkdir -p "$TEAM_DOCS"
cp "$HERE/policy.md" "$TEAM_DOCS/policy.md"
echo "✓ wrote $TEAM_DOCS/policy.md (operator-side, NOT in Cursor workspace)"

# ─── 2. Cursor workspace — looks like a normal dev project ───────────
PROJECT="$DEMO_HOME/myapp"
mkdir -p "$PROJECT/db" "$PROJECT/migrations" "$PROJECT/data" "$PROJECT/src"

# Benign .cursorrules — dev preferences only.  No freeze mention.
# This is what would actually be in a team's repo.  The agent reads
# this on session start and learns nothing about ongoing engineering
# policy — that's by design (policy.md isn't in the repo).
cat > "$PROJECT/.cursorrules" <<'EOF'
# myapp project rules

## Style
- TypeScript / Python preferred; no semicolons in TS files.
- Prefer functional style over inheritance.
- Tests under tests/, fixtures under data/.

## Dev DB
- Local Postgres on :5432, schema in migrations/0001_init.sql.
- Use `psql myapp_dev` for ad-hoc queries.
- Test fixtures live in data/seed.sql.

## Commit conventions
- Conventional commits: feat / fix / docs / style / refactor / test / chore.
EOF

cat > "$PROJECT/migrations/0001_init.sql" <<'EOF'
CREATE TABLE users (
  id         SERIAL PRIMARY KEY,
  email      TEXT UNIQUE NOT NULL,
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE orders (
  id      SERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES users(id),
  amount  NUMERIC(10,2)
);

-- Some test rows to clean up later.
CREATE TABLE users_test AS TABLE users WITH NO DATA;
EOF

cat > "$PROJECT/db/migrate.py" <<'EOF'
"""Migration runner."""
import psycopg2  # noqa: F401

def run_migrations():
    pass
EOF

cat > "$PROJECT/data/seed.sql" <<'EOF'
INSERT INTO users (email) VALUES ('demo@example.invalid');
INSERT INTO users_test (email) VALUES ('test1@example.invalid');
INSERT INTO users_test (email) VALUES ('test2@example.invalid');
EOF

cat > "$PROJECT/src/api.py" <<'EOF'
"""API entry point."""
def hello():
    return "hello"
EOF

cat > "$PROJECT/README.md" <<'EOF'
# myapp

Mock project for the Sponsio Cursor demo.  See `migrations/`,
`data/seed.sql`, and `src/api.py` for the surface area.
EOF

echo "✓ created Cursor workspace at $PROJECT (no policy mention anywhere)"

# ─── 3. Install Sponsio cursor hooks at PROJECT scope ────────────────
# This is what the user themselves would type:
#   sponsio host install cursor --scope project
# but we run it inline so setup.sh produces a fully-working demo.
mkdir -p "$PROJECT/.cursor"
SPONSIO_BIN="${SPONSIO_BIN:-$SPONSIO_ROOT/.venv/bin/sponsio}"
if [[ ! -x "$SPONSIO_BIN" ]]; then
  SPONSIO_BIN=$(command -v sponsio || echo "")
fi
if [[ -z "$SPONSIO_BIN" ]]; then
  echo "✗ sponsio binary not found.  pip install -e .[all] from repo root first."
  exit 1
fi

(
  cd "$PROJECT"
  # --with-skill installs the Sponsio Agent Skill alongside the
  # hooks in one shot.  Cursor's IDE agent reads .cursor/skills/
  # automatically on session start; the skill teaches it to drive
  # the W1 onboard workflow when the user types setup phrases in
  # Composer.
  "$SPONSIO_BIN" host install cursor --with-skill --scope project 2>&1 \
    | grep -E '✔|wrote|→' | head -4 || true
)
if [[ -f "$PROJECT/.cursor/hooks.json" ]] && [[ -f "$PROJECT/.cursor/skills/sponsio/SKILL.md" ]]; then
  echo "✓ installed Cursor hooks    $PROJECT/.cursor/hooks.json"
  echo "✓ installed Sponsio skill   $PROJECT/.cursor/skills/sponsio/SKILL.md"
else
  echo "✗ Cursor hooks/skill NOT installed — try manually:"
  echo "    cd $PROJECT && sponsio host install cursor --with-skill --scope project"
fi

# ─── 4. Sponsio _host plugin library — generated from policy.md ──────
# In production the user would run:
#   sponsio scan $TEAM_DOCS/policy.md -o ~/.sponsio/plugins/_host/sponsio.yaml --llm
# Here we ship the curated equivalent — same contracts, scan-shaped
# descs.  See USER_FLOW.md for the scan-based path.
mkdir -p "$DEMO_HOME/.sponsio/plugins/_host"
cp "$HERE/sponsio.yaml" "$DEMO_HOME/.sponsio/plugins/_host/sponsio.yaml"
echo "✓ wrote $DEMO_HOME/.sponsio/plugins/_host/sponsio.yaml"
echo "    (generated from team-docs/policy.md — see USER_FLOW.md)"

cat <<EOF

─── Demo HOME ready ────────────────────────────────────────────────────

  HOME              $DEMO_HOME
  Cursor workspace  $PROJECT/      ← agent only sees this
  Operator policy   $TEAM_DOCS/policy.md  ← NOT in workspace
  Hooks (project)   $PROJECT/.cursor/hooks.json
  Sponsio config    $DEMO_HOME/.sponsio/plugins/_host/sponsio.yaml

─── Recording ─────────────────────────────────────────────────────────

  See $HERE/USER_FLOW.md for the developer-side natural-language flow:
  what to type into Cursor's Composer, in what order, with multiple
  prompt strengths to escape model self-refusal.

  TL;DR — open Cursor against the workspace and follow USER_FLOW.md:

    export HOME=$DEMO_HOME
    cursor $PROJECT

────────────────────────────────────────────────────────────────────────
EOF

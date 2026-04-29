#!/usr/bin/env bash
# Install the sponsio-openclaw plugin into a *running* OpenClaw
# Docker container.
#
# Layout the script assumes:
#
#   • The OpenClaw container bind-mounts the host ``~/.openclaw``
#     directory into ``/home/node/.openclaw`` inside the container.
#     (Confirm with: docker inspect --format '{{json .Mounts}}'
#      <container> | jq .)  The default ghcr.io/openclaw/openclaw
#     image started via ``openclaw onboard`` does this.
#
#   • You have a clone of the Sponsio repo on the host (this script
#     lives inside it).
#
# What this script does:
#
#   1. Builds the TypeScript plugin (dist/index.js) inside an
#      ephemeral container that uses the same node version as the
#      OpenClaw runtime, so the artifacts are guaranteed to import.
#   2. Copies the built plugin into ``~/.openclaw/extensions/sponsio-openclaw/``
#      on the host — which the container sees as
#      ``/home/node/.openclaw/extensions/sponsio-openclaw/``.
#   3. Installs the Sponsio Python CLI inside the running container
#      (``pip install -e /opt/sponsio``) so the plugin's subprocess
#      transport can spawn ``sponsio plugin guard --stdin``.
#   4. Bootstraps per-plugin contract libraries at
#      ``~/.sponsio/plugins/`` — but inside the container, since the
#      plugin's subprocess runs there.  Bind-mounting the host's
#      ``~/.sponsio`` into the container is the cleanest version of
#      this; this script does that via a writable host dir + cp.
#   5. Patches ``~/.openclaw/openclaw.json`` to register
#      ``sponsio-openclaw`` under ``plugins.entries``.
#   6. Prints the next step (container restart) but does NOT restart
#      it — that's your call.
#
# Usage:
#   ./install_into_running_openclaw.sh \
#        [--container <name>] [--dry-run]
#
# Defaults:
#   container    openclaw-openclaw-gateway-1
#   sponsio-repo (parent of this script's grandparent dir)
#   host openclaw dir   ~/.openclaw
#
# Re-running is idempotent — it overwrites the plugin folder and
# re-applies the openclaw.json patch.

set -euo pipefail

CONTAINER="${CONTAINER:-openclaw-openclaw-gateway-1}"
DRY_RUN=0
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPONSIO_ROOT="$(cd "$HERE/../.." && pwd)"
HOST_OPENCLAW="${HOST_OPENCLAW:-$HOME/.openclaw}"
EXTENSIONS_DIR="$HOST_OPENCLAW/extensions/sponsio-openclaw"
OPENCLAW_JSON="$HOST_OPENCLAW/openclaw.json"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --container) CONTAINER="$2"; shift 2 ;;
    --dry-run)   DRY_RUN=1; shift ;;
    --sponsio-root) SPONSIO_ROOT="$2"; shift 2 ;;
    --host-openclaw) HOST_OPENCLAW="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

run() {
  if (( DRY_RUN )); then
    printf '  [dry-run] %s\n' "$*"
  else
    eval "$@"
  fi
}

step() { printf '\n→ %s\n' "$*"; }

# ─── Pre-flight ──────────────────────────────────────────────────────
step "pre-flight checks"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "  ✗ container '$CONTAINER' is not running" >&2
  echo "    Start it first (openclaw onboard / docker compose up)." >&2
  exit 1
fi
echo "  ✓ container '$CONTAINER' is running"

if [[ ! -d "$SPONSIO_ROOT/sponsio" || ! -f "$SPONSIO_ROOT/pyproject.toml" ]]; then
  echo "  ✗ Sponsio repo not found at '$SPONSIO_ROOT'" >&2
  exit 1
fi
echo "  ✓ Sponsio repo at $SPONSIO_ROOT"

IMAGE="$(docker inspect --format '{{.Config.Image}}' "$CONTAINER")"
echo "  ✓ container image: $IMAGE"

# Confirm bind mount
MOUNT_OK="$(docker inspect "$CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/home/node/.openclaw"}}1{{end}}{{end}}')"
if [[ "$MOUNT_OK" != "1" ]]; then
  echo "  ✗ container does not bind-mount /home/node/.openclaw" >&2
  echo "    This script assumes the standard 'openclaw onboard' layout." >&2
  exit 1
fi
echo "  ✓ /home/node/.openclaw is bind-mounted from host"

# ─── 1. Build the TS plugin ──────────────────────────────────────────
step "1. Build the TypeScript plugin (in an ephemeral container)"

run "docker run --rm --user root -v $HERE:/plugin --entrypoint sh $IMAGE -c '
  set -e
  cp -r /plugin /work && cd /work
  rm -rf node_modules dist
  mkdir -p node_modules/typescript node_modules/@types/node node_modules/undici-types node_modules/.bin
  cd /tmp && npm pack typescript@5.4 @types/node@20 undici-types@6 >/dev/null 2>&1
  cd /work
  tar xzf /tmp/typescript-*.tgz -C node_modules/typescript --strip-components=1
  tar xzf /tmp/types-node-*.tgz -C node_modules/@types/node --strip-components=1
  tar xzf /tmp/undici-types-*.tgz -C node_modules/undici-types --strip-components=1
  ./node_modules/typescript/bin/tsc
  cp -r dist /plugin/
'"
echo "  ✓ built dist/index.js"

# ─── 2. Drop the plugin into ~/.openclaw/extensions/ ─────────────────
step "2. Stage plugin at $EXTENSIONS_DIR"

run "rm -rf '$EXTENSIONS_DIR'"
run "mkdir -p '$EXTENSIONS_DIR'"
run "cp '$HERE/openclaw.plugin.json' '$EXTENSIONS_DIR/'"
run "cp '$HERE/package.json' '$EXTENSIONS_DIR/'"
run "cp -r '$HERE/dist' '$EXTENSIONS_DIR/'"
echo "  ✓ plugin folder at $EXTENSIONS_DIR"

# ─── 3. Install Sponsio Python CLI inside the running container ──────
step "3. Install Sponsio Python CLI inside container '$CONTAINER'"

# Copy Sponsio repo into the container (one-shot, ~80 MB).  The
# container's HOME is /home/node; install with --break-system-packages
# is fine because /usr is read-only-ish but pip's user prefix isn't.
run "docker cp '$SPONSIO_ROOT' '$CONTAINER:/opt/sponsio'"
run "docker exec --user root '$CONTAINER' sh -c '
  apt-get install -y python3-pip python3-venv >/dev/null 2>&1 || true
  pip install --break-system-packages -e /opt/sponsio >/dev/null
  ln -sf /usr/local/bin/sponsio /usr/bin/sponsio || true
  sponsio --version
'"
echo "  ✓ sponsio CLI is on the container's PATH"

# ─── 4. Bootstrap ~/.sponsio/plugins inside the container ────────────
step "4. Bootstrap Sponsio per-plugin libraries in container"

run "docker exec --user node '$CONTAINER' sh -c '
  sponsio plugin init 2>&1 | tail -5
'"
# `sponsio plugin init` writes _host, _host_subagent, and _host_openclaw
# fallback libraries in one shot; routing in `sponsio guard --stdin` picks
# the right one per inbound hook payload.
echo "  ✓ /home/node/.sponsio/plugins/ initialised"

# ─── 5. Register the plugin in openclaw.json ─────────────────────────
step "5. Register sponsio-openclaw in openclaw.json"

if [[ ! -f "$OPENCLAW_JSON" ]]; then
  echo "  ✗ $OPENCLAW_JSON not found — skipping" >&2
else
  # Backup, then patch with python (jq isn't guaranteed on host).
  run "cp '$OPENCLAW_JSON' '$OPENCLAW_JSON.before-sponsio'"
  if (( DRY_RUN )); then
    echo "  [dry-run] would patch plugins.entries.sponsio-openclaw = { enabled: true }"
  else
    python3 - <<PYEOF
import json, pathlib
p = pathlib.Path("$OPENCLAW_JSON")
cfg = json.loads(p.read_text())
cfg.setdefault("plugins", {}).setdefault("entries", {})["sponsio-openclaw"] = {
    "enabled": True
}
p.write_text(json.dumps(cfg, indent=2))
print("  ✓ patched", p)
PYEOF
  fi
fi

# ─── 6. Done — prompt the user for the restart ───────────────────────
cat <<EOF

─── Install staged ─────────────────────────────────────────────────────

  Plugin folder    $EXTENSIONS_DIR
  Container CLI    /usr/local/bin/sponsio (in $CONTAINER)
  Plugin libs      /home/node/.sponsio/plugins/ (in $CONTAINER)
  Config patch     $OPENCLAW_JSON  (backup: $OPENCLAW_JSON.before-sponsio)

To activate, restart the container so OpenClaw re-reads its plugin
registry and openclaw.json:

  docker restart $CONTAINER

Then check the gateway logs for plugin load diagnostics:

  docker logs --tail 100 $CONTAINER | grep -i sponsio

If you see "[plugins] sponsio-openclaw failed during register" the
typical fixes are:

  • sponsio not on PATH: docker exec $CONTAINER which sponsio
  • permission on /home/node/.sponsio: docker exec $CONTAINER \\
      ls -la /home/node/.sponsio
  • bad JSON in openclaw.json: restore from
      $OPENCLAW_JSON.before-sponsio

EOF

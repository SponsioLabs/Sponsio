#!/usr/bin/env bash
# =============================================================================
# examples/demo/pocketos/run.sh — replay the PocketOS / Railway wipe through
# Sponsio's Cursor hook adapter and show how each event would have been
# handled by a Cursor 1.7+ ``preToolUse`` hook.
#
# Walk-through (reproduces the public 2026-04-25 incident):
#
#   1. Agent greps repo + sibling dirs for a Railway token.        (allowed)
#   2. Agent issues curl -X DELETE to api.railway.app/volumes.     (BLOCKED)
#   3. Agent retries via GraphQL volumeDelete mutation.            (BLOCKED)
#   4. Agent retries via Railway CLI ``railway environment delete``. (BLOCKED)
#   5. Benign read-only Railway status GET.                        (allowed)
#
# Run:
#     bash examples/demo/pocketos/run.sh
#
# The demo uses an isolated $SPONSIO_PLUGIN_ROOT so it does not touch
# your real ~/.sponsio install or your real ~/.cursor/hooks.json.
# =============================================================================
set -uo pipefail

DEMO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LIB_ROOT="$DEMO_DIR/library"

export SPONSIO_PLUGIN_ROOT="$LIB_ROOT"
# Force enforce mode so denies surface even if a parent shell exported
# SPONSIO_MODE=observe.
export SPONSIO_MODE="enforce"
# Don't pollute the demo with live trace state from a prior run.
rm -f "$LIB_ROOT/_host/.shield-trace.jsonl" 2>/dev/null || true

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
dim()  { printf '\033[2m%s\033[0m\n' "$*"; }
red()  { printf '\033[31m%s\033[0m\n' "$*"; }
green(){ printf '\033[32m%s\033[0m\n' "$*"; }
yellow(){ printf '\033[33m%s\033[0m\n' "$*"; }

run_event() {
  local title="$1"
  local payload="$2"
  bold ""
  bold "──────────────────────────────────────────────────────────────"
  bold " $title"
  bold "──────────────────────────────────────────────────────────────"
  dim "Payload: $payload"

  local cmd
  cmd="$(python -c "import json,sys; print(json.load(open('$payload'))['tool_input']['command'])")"
  echo
  yellow "Cursor agent wants to run:"
  echo "    $cmd"
  echo

  local out
  local code
  out="$(python -m sponsio.cli cursor guard --event preToolUse < "$payload")"
  code=$?

  if [ "$code" -eq 0 ] && [ -z "$out" ]; then
    green "✔ Sponsio decision: ALLOW (exit 0)"
  elif [ "$code" -eq 2 ]; then
    red   "✘ Sponsio decision: DENY  (exit 2 — Cursor will not run the tool)"
    echo "    Cursor receives this JSON:"
    echo "$out" | python -m json.tool 2>/dev/null | sed 's/^/      /'
  else
    yellow "? Unexpected exit=$code"
    [ -n "$out" ] && echo "$out"
  fi
}

bold ""
bold "================================================================"
bold "  Sponsio × Cursor — PocketOS / Railway production-wipe replay"
bold "================================================================"
dim "  Reference: PocketOS database loss (Jer Crane, 2026-04-25)."
dim "  Contract pack: sponsio:incident/cursor-railway-wipe"
dim "  Library root:  $LIB_ROOT"

run_event "Step 1/5 — agent greps for a Railway token (recon)"        "$DEMO_DIR/payloads/01_grep_for_token.json"
run_event "Step 2/5 — agent issues curl -X DELETE to Railway API"      "$DEMO_DIR/payloads/02_railway_volume_delete.json"
run_event "Step 3/5 — agent retries via Railway GraphQL volumeDelete"   "$DEMO_DIR/payloads/03_railway_graphql_volume_delete.json"
run_event "Step 4/5 — agent retries via Railway CLI environment delete" "$DEMO_DIR/payloads/04_railway_cli_down.json"
run_event "Step 5/5 — benign Railway status GET (must NOT block)"       "$DEMO_DIR/payloads/05_benign_railway_status.json"

bold ""
bold "──────────────────────────────────────────────────────────────"
bold " Summary"
bold "──────────────────────────────────────────────────────────────"
green "  ✔ recon + benign read-only call: ALLOW"
red   "  ✘ destructive control-plane calls (REST DELETE, GraphQL"
red   "    volumeDelete, Railway CLI environment delete): DENY"
echo
dim "  In a real install the same path runs inside Cursor: every"
dim "  preToolUse hook fires this exact code, and the destructive"
dim "  tool calls never leave the IDE.  Wire it up with:"
dim ""
dim "      sponsio cursor install-hooks"
dim ""
echo

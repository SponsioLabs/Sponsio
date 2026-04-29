# PocketOS / Railway production-wipe — Sponsio × Cursor demo

**What this demo shows.** A Cursor agent (Claude Opus 4.6 backend) tried to
"fix" a credential mismatch in a staging environment by deleting a Railway
volume. Because Railway co-locates volume-level backups on the same volume,
that single decision wiped both production data **and** the backups in 9
seconds. Public report: PocketOS, Jer Crane, 2026-04-25.

This demo replays the exact tool-call shapes Cursor would have issued through
Sponsio's Cursor hook adapter, against the
[`sponsio:incident/cursor-railway-wipe`](../../../sponsio/contracts/incident/cursor-railway-wipe.yaml)
contract pack. It runs entirely offline — no Railway calls go out.

## Run

```bash
bash examples/demo/pocketos/run.sh
```

Expected output:

| Step | Tool call                                              | Decision |
|------|--------------------------------------------------------|----------|
| 1    | grep repo for `RAILWAY_TOKEN` (recon)                  | **ALLOW** |
| 2    | `curl -X DELETE api.railway.app/v2/volumes/...`        | **DENY** (exit 2) |
| 3    | `curl POST backboard.railway.app/graphql {volumeDelete}` | **DENY** (exit 2) |
| 4    | `railway environment delete production --yes`          | **DENY** (exit 2) |
| 5    | `curl GET api.railway.app/v2/projects/.../status`      | **ALLOW** |

## What's happening under the hood

```
       ┌──────────────────────────┐
       │  Cursor agent decides    │
       │  to delete a volume.     │
       └─────────┬────────────────┘
                 │ Cursor 1.7+ fires `preToolUse` hook
                 ▼
       ┌──────────────────────────┐
       │  hooks.json:             │
       │    "sponsio cursor       │
       │     guard --event        │
       │     preToolUse"          │
       └─────────┬────────────────┘
                 │ JSON payload over stdin
                 ▼
       ┌──────────────────────────┐
       │ sponsio/integrations/    │
       │   cursor.py              │
       │ - normalises tool name   │
       │   (Shell → Bash)         │
       │ - calls evaluate_event   │
       │   (guard_stdin)          │
       │ - renders Cursor-shaped  │
       │   {permission: deny,…}   │
       │   + exit 2               │
       └─────────┬────────────────┘
                 │
                 ▼
       Cursor sees deny → tool call never runs.
```

## Cursor regression — set `SPONSIO_CURSOR_REWRITE_DENY=1` for a tighter demo

Cursor v2.0.64 → ≥2.2.43 has a [confirmed regression](https://forum.cursor.com/t/regression-hook-response-fields-user-message-agent-message-still-ignored-in-windows-v2-0-77/142589):
the `agent_message` field on a `preToolUse` deny is silently dropped before
reaching the model.  The human sees the red error in Cursor's UI; the agent
doesn't, and tends to retry blindly.

Sponsio ships an opt-in workaround.  Set `SPONSIO_CURSOR_REWRITE_DENY=1` in
the environment Cursor inherits, and Sponsio will instead return
`permission: allow` with `updated_input.command` rewritten to a
`printf ... ; exit 1` carrying the deny reason on stderr.  The agent reads
the printf output as a tool failure and has the contract name + reason in
its conversation context.

```bash
osascript -e 'quit app "Cursor"' && sleep 1
SPONSIO_CURSOR_REWRITE_DENY=1 open -a Cursor /tmp/pocketos-stage
```

Or persist:

```bash
echo 'export SPONSIO_CURSOR_REWRITE_DENY=1' >> ~/.zshrc
```

When Cursor fixes the upstream regression, drop the env var and Sponsio
returns to the standard `permission: deny` shape.

## Wire it up for real

```bash
pip install -e .
sponsio cursor install-hooks                      # writes ~/.cursor/hooks.json

mkdir -p ~/.sponsio/plugins/_host
cp examples/demo/pocketos/library/_host/sponsio.yaml \
   ~/.sponsio/plugins/_host/sponsio.yaml          # or merge into your existing _host lib

# Restart Cursor.  From now on every preToolUse runs through Sponsio.
```

To verify in a live Cursor session, ask Cursor to run

```
curl -X DELETE -H 'Authorization: Bearer test' https://api.railway.app/v2/volumes/foo
```

— Cursor will surface the deny message and refuse to execute.

## Recording the demo

```bash
# 80 cols × 24 rows is a sweet spot for terminal recording UIs.
asciinema rec pocketos-demo.cast \
  --title "Sponsio blocks the PocketOS Railway wipe" \
  --command "bash examples/demo/pocketos/run.sh"
```

Then either:

- Convert to GIF: `agg pocketos-demo.cast pocketos-demo.gif`
- Or upload the `.cast` file directly: `asciinema upload pocketos-demo.cast`

## Files

| Path                                                                                     | Purpose                                       |
|------------------------------------------------------------------------------------------|-----------------------------------------------|
| `run.sh`                                                                                 | Demo runner; pipes each payload through Sponsio. |
| `payloads/01_grep_for_token.json` … `05_benign_railway_status.json`                      | Cursor `preToolUse` JSON payloads (one per step). |
| `library/_host/sponsio.yaml`                                                             | Demo-only contract library that includes the incident pack. |
| `../../../sponsio/contracts/incident/cursor-railway-wipe.yaml`                           | The actual contract pack — three deterministic rules. |
| `../../../sponsio/integrations/cursor.py`                                                | Cursor JSON ↔ Sponsio adapter.                |

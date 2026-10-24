---
title: Running Sponsio on a host
description: Install the hooks, check your tier, review held rule changes, read the audit log, and recover when every call is denied.
---

# Running Sponsio on a host

This guide is for the person who installs Sponsio for Claude Code, Cursor or OpenClaw and looks after it. Run every command here from your own terminal, not from inside the agent session Sponsio guards: the agent's hooks deny the commands that change Sponsio.

---

## Install

```bash
sponsio host install claude-code                    # or: cursor, openclaw
sponsio host install claude-code --scope project    # this repo only (./.claude/settings.json)
sponsio host install claude-code --mode observe     # log violations, block nothing
```

The installer backs up the host's config file before it changes it (`<file>.sponsio-backup-<timestamp>`), writes the hook, and creates the host's contract library under `~/.sponsio/plugins/_host_<host>/`. The mode goes into the daemon's `daemon.yaml`, not the hook command, so editing the hook or the host's `env` does not switch Sponsio to observe. Re-run with `--mode enforce` to switch back.

Try a project-scope install first. If something is wrong, only sessions in that directory are affected.

## Check it

```bash
sponsio doctor          # hook wiring, Claude Code version, and the tier
sponsio doctor --tier   # each guarantee of Tier 1, 2 and 3, pass or fail, with the fix
sponsio daemon status   # is the daemon answering
```

A default install reports **Tier 1**. The guarantees that tier gives, and what it does not cover, are in the [threat model](../concepts/threat-model.md).

## When rules change

Editing a contract library is how you change what Sponsio enforces. What happens next depends on the change:

- **You added a contract.** It applies on the next call.
- **You removed or changed one.** Sponsio keeps enforcing the rules it has, records the change, and adds a note to the next deny: the rules changed on disk and need review. The same happens when anything else, the agent included, edits the file.

To review and apply a held change:

```bash
sponsio rules status    # HELD next to a library with a change waiting
sponsio rules diff      # - removed or changed, + added
sponsio rules reload    # apply it; recorded with your uid
```

If you did not make the change, put the file back instead of reloading. A restored file that keeps every loaded contract applies on its own.

When the daemon restarts and finds a library weaker than the last one applied, it denies that library's calls until you run `sponsio rules reload`. The deny reason says so.

## Read the audit log

```bash
tail -n 20 ~/.sponsio/state/audit.jsonl
```

One JSON object per line. The ones to look for:

| Event | Means |
|---|---|
| `config_drift` | A rule file, pack or engine file changed. With `"applied": false`, a rule change is waiting for review. |
| `tamper_attempt` | The agent tried to change Sponsio and a self-protection rule denied it. |
| `trace_deleted` | A session's history log was deleted or cut; it was restored from memory. |
| `fail_open` | A call was allowed because its rules could not be evaluated and `on_error: allow` is set. |
| `admin_call` | Someone changed the daemon's state: the method and their uid. |
| `daemon_restart` | The daemon came back after being stopped without a clean shutdown. |

The full list is in the [daemon reference](../reference/daemon.md#audit-events).

## When every call is denied

The hook fails closed: if it cannot reach a daemon, or the daemon cannot read its rules, the agent's calls are denied, including the ones it would use to fix the problem. The deny reason names the cause. From your own terminal:

1. `sponsio daemon run --detach`, then `sponsio doctor`. This fixes a daemon that is not running.
2. `sponsio rules status`. A HELD library after a restart is denied until `sponsio rules reload`.
3. If neither helps, remove the hook: `sponsio host uninstall claude-code` (add `--scope project` for a project install).

## Uninstall

```bash
sponsio host uninstall claude-code                    # remove Sponsio's entries
sponsio host uninstall claude-code --restore-backup   # and put back the file as it was before
```

Uninstall removes only Sponsio's entries and the Sponsio skill. A config file that held nothing but Sponsio's entries is deleted with its backups. On a terminal, uninstall offers to restore the most recent backup.

---

**Related:** [Threat model](../concepts/threat-model.md) · [Daemon reference](../reference/daemon.md) · [CLI reference](../reference/cli.md) · [Plugins](../plugins.md)

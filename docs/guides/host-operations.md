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
| `tamper_attempt` | The agent tried to change Sponsio and a self-protection rule denied it, or a project's `.sponsio/host.yaml` tried to weaken a protected rule. |
| `trace_deleted` | A session's history log was deleted or cut; it was restored from memory. |
| `fail_open` | A call was allowed because its rules could not be evaluated and `on_error: allow` is set. |
| `admin_call` | Someone changed the daemon's state: the method and their uid. |
| `daemon_restart` | The daemon came back after being stopped without a clean shutdown. |

The full list is in the [daemon reference](../reference/daemon.md#audit-events). Each record is chained to the one before it; `sponsio admin audit verify` tells you whether any was edited or removed.

## When every call is denied

The hook fails closed: if it cannot reach a daemon, or the daemon cannot read its rules, the agent's calls are denied, including the ones it would use to fix the problem. The deny reason names the cause. From your own terminal:

1. `sponsio daemon run --detach`, then `sponsio doctor`. This fixes a daemon that is not running.
2. `sponsio rules status`. A HELD library after a restart is denied until `sponsio rules reload`.
3. If neither helps, remove the hook: `sponsio host uninstall claude-code` (add `--scope project` for a project install).

## Tier 2: the system install

At Tier 1 the daemon runs as you, so the agent, which also runs as you, could change Sponsio's files; Sponsio records it but cannot stop it. The system install moves Sponsio out of your user's reach:

```bash
sudo sponsio install --system --dry-run      # read every step first
sudo sponsio install --system                # Claude Code
sudo sponsio install --system --host claude-code --host cursor
```

It creates a `_sponsio` system user and runs the daemon as that user under launchd (macOS) or systemd (Linux). The engine goes into a root-owned `/opt/sponsio`, the hook shims into `/usr/local/libexec/sponsio`, and the rules, history, audit log and config into a directory only `_sponsio` can open: `/Library/Application Support/Sponsio/state` on macOS, `/var/lib/sponsio` on Linux. Your libraries under `~/.sponsio/plugins` are copied in once (directories and regular files only: a link or anything else in a bucket is left out and listed); after that the daemon reads only its own copy. Once the system install is in place, every Sponsio client on the machine talks to its socket, also while the daemon restarts, never to a daemon your own user runs.

For Claude Code it writes `managed-settings.d/50-sponsio.json` beside Claude Code's managed settings. Your own settings cannot remove those hooks. It also turns off user and project hooks (`allowManagedHooksOnly`), denies edits to Sponsio's paths, turns on Claude Code's sandbox with no unsandboxed fallback, and refuses Claude Code versions older than 2.1.295. `--allow-user-hooks` leaves user and project hooks loading; doctor then reports Tier 2 (degraded).

Things to know:

- **The engine needs a root-owned Python 3.10 or newer.** Its standard library is part of the engine, so a Python under your home or under Homebrew does not count. On macOS install one from python.org; the system `/usr/bin/python3` is too old. Pass `--python` to choose.
- **An MDM profile can hide the file.** Claude Code reads a managed MDM profile before the managed settings files, and ignores the files unless the profile sets `managedSourcesBehavior: merge`. `sponsio doctor --tier` says when this is the case.
- **The sandbox has exceptions.** Commands listed in `sandbox.excludedCommands`, and commands you run yourself with `!`, run outside it. On Linux it needs `bubblewrap` and `socat`.
- **Cursor stays at Tier 1.** Its system hooks file is used, but nothing documented stops a user from turning system hooks off, Cursor's local SDK runtime loads no hooks, and a prompt right after startup can run before they load. Turn on "Block until hooks are ready" in Cursor's admin settings.
- **OpenClaw** reaches Tier 2 only when OpenClaw itself is installed system-wide.

Afterwards, commands that change Sponsio need root: `sudo sponsio rules reload`, `sudo sponsio admin proposals approve <id>`, `sudo sponsio admin audit verify`. Check the result as your own user with `sponsio doctor --tier`.

To remove it: `sudo sponsio uninstall --system` (add `--keep-state` to keep the rules and the audit log; the `_sponsio` user that owns them is kept too).

`sponsio doctor --tier` counts Claude Code's managed settings only when the files and the directories holding them are root-owned and not writable by your user, `disableAllHooks` is not set in them, and the sandbox they enable covers this platform (`sandbox.enabledPlatforms`). The shim and the engine count as root-owned only when every directory above them is root-owned and read-only to your user too. The installer removes group and other write from the host settings directories it writes into.

## Tier 3: sandbox, gateway and egress proxy

Tier 3 adds that every tool call and every connection goes through Sponsio, because the agent has no other route and no credentials of its own:

```bash
sponsio gateway command                        # the MCP server entry for the host
sponsio run --sandbox --writable ~/.claude -- claude
```

1. **Tools.** Move the agent's MCP servers and their credentials into the daemon's `gateway.yaml`, and give the host only the gateway (`sponsio gateway command` prints its entry). The agent then sees, each turn, only the tools its state allows ([MCP gateway](../reference/daemon.md#mcp-gateway)).
2. **Network.** List the hosts the agent may reach under `egress.allow` in `daemon.yaml`, and start the agent with `sponsio run --sandbox`. Every connection is then decided by the daemon ([Egress proxy](../reference/daemon.md#egress-proxy)).
3. **Files.** Inside the sandbox only the workspace and the paths you name with `--writable` can be written, and Sponsio's state cannot be read.

On Linux the sandbox needs `bubblewrap` and unprivileged user namespaces (on Ubuntu, `kernel.apparmor_restrict_unprivileged_userns=0`). On macOS it uses `sandbox-exec`. On a server, use the container recipe in `deploy/tier3/`: the agent's container sits on a network with no route out and reaches the daemon only through a shared socket volume.

What Tier 3 still does not cover: escaping the sandbox or the container, contracts on the contents of TLS traffic, and covert channels. `sponsio doctor --tier` run inside the sandbox reports the Tier 3 guarantees; run outside, it cannot see how an agent was started.

## Uninstall

```bash
sponsio host uninstall claude-code                    # remove Sponsio's entries
sponsio host uninstall claude-code --restore-backup   # and put back the file as it was before
```

Uninstall removes only Sponsio's entries and the Sponsio skill. A config file that held nothing but Sponsio's entries is deleted with its backups. On a terminal, uninstall offers to restore the most recent backup.

---

**Related:** [Threat model](../concepts/threat-model.md) · [Daemon reference](../reference/daemon.md) · [CLI reference](../reference/cli.md) · [Plugins](../plugins.md)

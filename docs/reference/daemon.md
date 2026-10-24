---
title: Daemon reference
description: daemon.yaml, the agent and admin sockets, proposals, held rule changes, the MCP gateway, and every audit event the daemon writes.
---

# Daemon reference

Host hooks (Claude Code, Cursor, OpenClaw) are decided by the Sponsio daemon, not in the hook's own process. This page lists what the daemon reads, what it serves on each socket, and what it writes to its audit log. For why it is built this way, see the [threat model](../concepts/threat-model.md) and [the host enforcement path](../concepts/architecture.md#6-host-enforcement-path). For the commands, see [CLI reference](cli.md#sponsio-daemon).

---

## Files

Everything lives under the state dir, `~/.sponsio/state` by default (`sponsio daemon run --state-dir`).

| File | What it holds |
|---|---|
| `daemon.yaml` | Every setting a hook decision depends on. |
| `gateway.yaml` | The upstream MCP servers the gateway runs, with their credentials. See [MCP gateway](#mcp-gateway). |
| `daemon.lock` | An exclusive `flock` while a daemon runs, holding its pid. One daemon per state dir. |
| `daemon.log` | Output of a daemon started with `--detach`. |
| `wal/<session>.jsonl` | Each live session's history, one admitted event per line, numbered by `seq`. Replayed on start. |
| `rules/<bucket>.json` | The last ruleset applied to each bucket: contract ids, content hash, when and how. |
| `proposals/<id>.json` | Rule changes submitted from the agent side, pending or decided. |
| `audit.jsonl` | Append-only record of what the daemon did and saw. |

All of these are created owner-only (0600 files, 0700 directories).

---

## daemon.yaml

```yaml
mode: enforce            # hosts not listed below
hosts:
  claude-code: {mode: observe}
on_error: deny           # deny | allow: what an evaluation error becomes
unconfigured: null       # deny | allow | null (the host library decides)
plugin_root: null        # overrides --plugin-root when set
trace_ttl_hours: 24
cloud:                   # optional: compare loaded rules with the published book
  project: my-project
  buckets: {_host_claude_code: my-agent}
  verify_interval_s: 300
```

| Key | Default | Meaning |
|---|---|---|
| `mode` | `enforce` | Mode for hosts not listed under `hosts`. |
| `hosts.<host>.mode` | | `enforce` or `observe` for one host. Set with `sponsio host install <host> --mode`. `gateway` is the MCP gateway's entry. |
| `on_error` | `deny` | `allow` lets a call through when its library cannot be evaluated. Every such call is audited as `fail_open`. |
| `unconfigured` | `null` | `deny` or `allow` for a tool with no library. `null` lets the host library's `defaults.unconfigured` decide. |
| `plugin_root` | `null` | Library root. When unset, the `--plugin-root` the daemon started with, else `~/.sponsio/plugins`. |
| `trace_ttl_hours` | `24` | A session idle this long is dropped with its log. |
| `cloud.project` | | Cloud project the buckets are linked to. |
| `cloud.buckets` | | Host bucket to cloud agent, for `sponsio rules verify`. |
| `cloud.verify_interval_s` | `300` | How often the daemon compares on its own. At least 30. |

The daemon re-reads the file when it changes. A missing file, a file that does not parse, and any key that is missing or not valid all take the strict default. The daemon reads no environment variable for a decision, its own included: `SPONSIO_GUARD_MODE`, `SPONSIO_MODE`, `SPONSIO_HOOK_ON_ERROR`, `SPONSIO_UNCONFIGURED`, `SPONSIO_SHIELD_TRACE_TTL_HOURS` and `SPONSIO_PLUGIN_ROOT` do not affect hooks. They still apply to the in-process SDK and to `sponsio host guard --direct`.

`sponsio host install --mode` writes the mode through the admin socket when a daemon answers there (`mode.set`, audited), and writes the file directly when none does.

---

## Sockets

The daemon listens on two Unix sockets. The agent socket is the one hooks use (`--socket`, default `~/.sponsio/sponsio.sock`). The admin socket sits beside it, named `<stem>.admin<suffix>` (`sponsio.sock` becomes `sponsio.admin.sock`), always mode 0600. A method called on the other socket returns `not_found`.

| Socket | Method | What it does |
|---|---|---|
| agent | `ping` | Health check; returns pid and version. |
| agent | `guard.check` | Decide a pre-event and render the host's reply (`stdout`, `exit_code`, and `stderr` when a warning is pending). |
| agent | `guard.observe` | Record a post-event (the tool's output). Never gates. |
| agent | `gateway.list_tools` | The MCP tools a session may call now. See [MCP gateway](#mcp-gateway). |
| agent | `gateway.call_tool` | Decide an MCP tool call, run it upstream when allowed, and record it. |
| agent | `proposal.submit` | Validate a rule change as a dry run and store it. Nothing applies. |
| admin | `ping` | As above. |
| admin | `mode.set` | Persist a host's mode in `daemon.yaml` (`claude-code`, `cursor`, `openclaw` or `gateway`). |
| admin | `rules.status` | Loaded libraries, held changes, the last cloud comparison. |
| admin | `rules.reload` | Apply held changes, for one bucket or all. |
| admin | `rules.verify` | Compare linked buckets with the cloud's published books now. |
| admin | `plugin.append` | Merge contracts into a host bucket. |
| admin | `proposal.list`, `proposal.show` | Read proposals. |
| admin | `proposal.approve`, `proposal.reject` | Decide a proposal. Approve applies it. |

The protocol is a 4-byte big-endian length followed by a JSON body, one request per connection.

### Who is calling

The daemon reads each caller's uid, gid and pid from the kernel when it accepts the connection: `SO_PEERCRED` on Linux, `LOCAL_PEERCRED` and `LOCAL_PEERPID` on macOS. Nothing the client sends is trusted for identity. An admin call runs only for the daemon's own uid or root. Every admin call, allowed or refused, is audited with that identity.

At Tier 1 the daemon runs as the agent's user, so the agent can reach the admin socket. The audit record is what protects you there. At Tier 2 the admin socket belongs to the daemon's own user.

---

## Proposals

The agent side cannot change rules. `sponsio plugin append` sends `proposal.submit` on the agent socket; the daemon validates the change as a dry run and stores it under `proposals/`. It applies only when `proposal.approve` arrives on the admin socket, which records who approved it. When the caller can reach the admin socket (Tier 1), `plugin append` approves its own proposal in the same command, and both steps are audited. When it cannot, the proposal waits.

---

## Held rule changes

Every compiled contract gets a stable id: a hash of its agent, the pack it came from, its formula, strategy and parameters, but not its description. Settings that change decisions (`defaults`, `tools`, `tool_policy`, `runtime`) get ids too. When a library or one of its packs changes:

- **Every loaded id is still there.** The new rules apply at once. `ruleset_strengthened` is recorded.
- **An id was removed or changed.** The daemon keeps enforcing what it loaded, records `config_drift` with the removed contracts, and adds a one-line warning to the next hook reply. The change applies after `sponsio rules reload`. A changed parameter counts as one removal and one addition. A library that no longer loads counts as removing everything.
- **The daemon restarts onto a library weaker than `rules/<bucket>.json`.** With nothing older in memory to serve, it denies that bucket's calls until a reload, and says so in the deny reason.

`sponsio daemon run --watch` applies every change at once and still records each one. It is for working on Sponsio's own rules.

---

## MCP gateway

The gateway puts the daemon between the agent and its MCP servers. The daemon starts the upstream servers itself, with the credentials in `gateway.yaml`, and the agent's host is given one MCP server, the gateway shim. The agent never holds an upstream's credentials, so it cannot call the upstream another way.

```yaml
# <state-dir>/gateway.yaml, mode 0600
servers:
  github:
    command: [npx, -y, "@modelcontextprotocol/server-github"]
    env: {GITHUB_PERSONAL_ACCESS_TOKEN: "..."}
    cwd: null          # optional
    timeout_s: 60      # start-up and per-call budget
```

| Key | Meaning |
|---|---|
| `servers.<name>` | One upstream. Letters, digits, and single `-` or `_` between them. `sponsio` is taken. |
| `command` | The argv, as a list or a string split like a shell would. Only stdio servers are supported. |
| `env` | Added to the daemon's own environment for this server. This is where credentials go. |
| `timeout_s` | How long start-up and each call may take. Default 60. |

A server starts on first use and again after it exits. A change to the file applies on the next call, and a server whose entry changed or went away is stopped.

**What the agent sees.** Each upstream tool is listed as `<server>__<tool>`. Claude Code calls it `mcp__sponsio__<server>__<tool>`. Contracts name it `mcp__<server>__<tool>`, as for any MCP tool, and live in the server's own library, `<plugin-root>/<server>/sponsio.yaml`. The `_gateway` library's `defaults` apply to every server, for example `unconfigured: deny`.

**Per session.** One shim process is one session, with its own history under `gateway:<id>`. `tools/list` returns only the tools a call to which the session's current state would admit. A tool behind a `must_precede` is not listed until its prerequisite has run. A call is decided again with its arguments. A call to a tool that is not listed is refused even when its arguments would pass. A refused call never reaches the upstream. When a call changes the list, the shim sends `notifications/tools/list_changed`. In observe mode (`hosts: {gateway: {mode: observe}}`) every tool is listed and every call runs.

**When there is no daemon.** The shim starts one, as the hook shim does. If none answers, `tools/list` returns an error and every call returns an error result.

**Coexisting with hooks.** When Claude Code's hooks are also installed, the hook decides `mcp__sponsio__<server>__<tool>` as `mcp__<server>__<tool>`, against the same library.

The gateway alone does not make Tier 3: an agent that can still run commands can reach the network another way. That needs the sandbox runner.

---

## Audit events

Every line of `audit.jsonl` is a JSON object with `ts` (UTC, ISO 8601), `event` and `pid`, plus the fields below.

| Event | Written when | Fields |
|---|---|---|
| `daemon_start` | A daemon starts and no earlier one is on record. | `socket` |
| `daemon_restart` | A daemon starts after the previous one went away without stopping. | `socket`, `previous_pid` |
| `daemon_stop` | A daemon stops cleanly. | `socket` |
| `admin_call` | Any call on the admin socket other than `ping`. | `method`, `peer` (uid, gid, pid), `ok`, `code`, `params_sha256` |
| `fail_open` | A call was allowed only because `on_error: allow`. | `host`, `tool`, `conversation`, `plugin_id`, `reason` |
| `ruleset_strengthened` | A library change that only added contracts was applied. | `bucket`, `path`, `added`, `content_hash` |
| `config_drift` | A library change removed or changed contracts (`applied` false when held, true under `--watch`). | `bucket`, `path`, `applied`, `removed`, `added`, `summary`, `load_error` |
| `config_drift` | A watched file changed (rule file, pack or engine module). | `file`, `change`, `old_sha256`, `new_sha256` |
| `ruleset_reloaded` | `sponsio rules reload` applied a held change. | `bucket`, `peer`, `removed`, `added`, `content_hash` |
| `trace_deleted` | A session's log was found deleted or truncated and rewritten from memory. | `conversation`, `problem` |
| `tamper_attempt` | A call was denied by `capability/self-modify` or `capability/host-config-integrity`. | `host`, `tool`, `bucket`, `packs`, `reason` |
| `proposal_submitted` | A rule change was proposed. | `id`, `kind`, `peer` |
| `proposal_approved`, `proposal_rejected` | A proposal was decided. | `id`, `kind`, `peer` |
| `cloud_drift` | A linked bucket lacks contracts the cloud publishes for its agent. | `bucket`, `agent`, `project`, `status`, `published_version`, `missing` |
| `cloud_verified` | A cloud comparison's outcome changed and is not weaker. | same as `cloud_drift` |

Writing is best effort: a full disk does not stop the daemon from deciding.

---

**Related:** [Threat model](../concepts/threat-model.md) · [Architecture](../concepts/architecture.md#6-host-enforcement-path) · [CLI reference](cli.md) · [Host operations](../guides/host-operations.md)

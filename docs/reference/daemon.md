---
title: Daemon reference
description: daemon.yaml, the agent and admin sockets, proposals, held rule changes, signed rules, the MCP gateway, and the hash-chained audit log.
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
| `cloud.key` | The daemon's own cloud API key (`sponsio admin cloud-key set`). |
| `audit.jsonl` | Append-only, hash-chained record of what the daemon did and saw. |
| `audit.stream` | The id the exported audit records carry (see [Audit events](#audit-events)). |

All of these are created owner-only (0600 files, 0700 directories). After `sudo sponsio install --system` the state dir is `/Library/Application Support/Sponsio/state` (macOS) or `/var/lib/sponsio` (Linux), owned by `_sponsio`. The sockets are then `run/agent.sock` (macOS, beside `state`) or `/run/sponsio/agent.sock` (Linux), with the admin socket `agent.admin.sock` beside it.

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
  proposals: false       # true: decide proposals in the cloud console
audit:                   # optional: also send every audit record over OTLP/HTTP
  otlp_endpoint: https://otlp.example.com/v1/traces
  otlp_headers: {x-api-key: "..."}
signing:                 # optional: rule files must be signed by this key
  public_key: "<base64 Ed25519 public key>"
  buckets: ["*"]
egress:                  # optional: hosts the egress proxy may reach
  allow: [api.anthropic.com, "*.github.com"]
```

| Key | Default | Meaning |
|---|---|---|
| `mode` | `enforce` | Mode for hosts not listed under `hosts`. |
| `hosts.<host>.mode` | | `enforce` or `observe` for one host. Set with `sponsio host install <host> --mode`. `gateway` is the MCP gateway's entry, `sdk` the SDK's `backend="daemon"`. |
| `on_error` | `deny` | `allow` lets a call through when its library cannot be evaluated. Every such call is audited as `fail_open`. |
| `unconfigured` | `null` | `deny` or `allow` for a tool with no library. `null` lets the host library's `defaults.unconfigured` decide. |
| `plugin_root` | `null` | Library root. When unset, the `--plugin-root` the daemon started with, else `~/.sponsio/plugins`. |
| `trace_ttl_hours` | `24` | A session idle this long is dropped with its log. |
| `cloud.project` | | Cloud project the buckets are linked to. |
| `cloud.buckets` | | Host bucket to cloud agent, for `sponsio rules verify`. |
| `cloud.verify_interval_s` | `300` | How often the daemon compares on its own. At least 30. |
| `cloud.proposals` | `false` | Upload proposals to `cloud.project` and apply what a person decides in the console, every `verify_interval_s`. See [Proposals](#proposals). |
| `audit.otlp_endpoint` | | Every audit record is also sent here as an OTLP span, with its chain fields. |
| `audit.otlp_headers` | | Headers for that endpoint, such as an API key. |
| `signing.public_key` | | Base64 Ed25519 public key that signs rule files. See [Signed rules](#signed-rules). |
| `signing.buckets` | `["*"]` | Which buckets must be signed. |
| `egress.allow` | | Hosts the egress proxy may connect to. `github.com` matches itself on ports 80 and 443, `*.github.com` its subdomains, `github.com:22` one other port, `github.com:*` any port. See [Egress proxy](#egress-proxy). |

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
| agent | `egress.open`, `egress.close` | Open or close the egress proxy for a `sponsio run` session. See [Egress proxy](#egress-proxy). |
| agent | `sdk.check` | Decide a call from `Sponsio(..., backend="daemon")` against the library it names. |
| agent | `sdk.observe` | Record that call's output. Never gates. |
| agent | `sdk.filter_tools` | The tools that SDK session may call now. |
| agent | `proposal.submit` | Validate a rule change as a dry run and store it. Nothing applies. |
| admin | `ping` | As above. |
| admin | `mode.set` | Persist a host's mode in `daemon.yaml` (`claude-code`, `cursor`, `openclaw`, `gateway` or `sdk`). |
| admin | `rules.status` | Loaded libraries as they are on disk now, held changes, the last cloud comparison. |
| admin | `rules.reload` | Apply held changes, for one bucket or all. |
| admin | `rules.verify` | Compare linked buckets with the cloud's published books now. |
| admin | `plugin.append` | Merge contracts into a host bucket. |
| admin | `proposal.list`, `proposal.show` | Read proposals. |
| admin | `proposal.approve`, `proposal.reject` | Decide a proposal. Approve applies it. |
| admin | `audit.verify` | Check the audit chain, including that the last record this daemon wrote is still there. |
| admin | `cloud.set_key`, `cloud.key_status` | Store the daemon's own cloud key; show which key it uses, by fingerprint. |

The protocol is a 4-byte big-endian length followed by a JSON body, one request per connection.

### Who is calling

The daemon reads each caller's uid, gid and pid from the kernel when it accepts the connection: `SO_PEERCRED` on Linux, `LOCAL_PEERCRED` and `LOCAL_PEERPID` on macOS. Nothing the client sends is trusted for identity. An admin call runs only for the daemon's own uid or root. Every admin call, allowed or refused, is audited with that identity.

At Tier 1 the daemon runs as the agent's user, so the agent can reach the admin socket. The audit record is what protects you there. At Tier 2 the admin socket belongs to the daemon's own user.

---

## Project layer

For a host hook call, the daemon adds the contracts in the project's `.sponsio/host.yaml` to the bucket's library. The project layer can only add: anything in it that would weaken a protected contract is dropped and recorded as a `tamper_attempt`. See [A protected library with a project layer](config-yaml.md#a-protected-library-with-a-project-layer).

## Proposals

The agent side cannot change rules. `sponsio plugin append` sends `proposal.submit` on the agent socket; the daemon validates the change as a dry run and stores it under `proposals/`. It applies only when `proposal.approve` arrives on the admin socket, which records who approved it and what changed. When the caller can reach the admin socket (Tier 1), `plugin append` approves its own proposal in the same command, and both steps are audited. When it cannot, the proposal waits for `sponsio admin proposals approve <id>`.

A bucket whose rules must be signed (see [Signed rules](#signed-rules)) takes no change made on the machine. An appended file is no longer signed, so the daemon would keep the last signed rules and the change would never be enforced. `plugin.append` and `proposal.submit` for such a bucket are refused with `signed`, and so is approving a proposal submitted before signing was turned on: the change goes into the book where it is published, and the machine pulls it.

**Deciding in the cloud console.** With `cloud: {project: ..., proposals: true}`, the daemon uploads its proposals with their status every `verify_interval_s`, using its own cloud key (`sponsio admin cloud-key set`; the key needs write access, a `publish` key). A person signed in to the console decides under Settings → Machines; no API key can, so a key the agent can read cannot approve what that agent proposed. On its next sync the daemon applies the decision through the same path as `proposal.approve`, and the audit record names the person (`peer: {via: sponsio-cloud, email, user_id}`). A decision applies only when the daemon's own copy of the proposal hashes to the `params_sha256` the person saw; otherwise it is refused and recorded as `proposal_cloud_refused`. The cloud keeps the params from the first upload and refuses different ones under the same id. Each sync carries at most 400 pending proposals, oldest first, and those decided in the last 7 days; a proposal whose params exceed 256 KiB is not sent and is decided on the machine.

---

## Held rule changes

Every compiled contract gets a stable id: a hash of its agent, the pack it came from, its formula, strategy and parameters, but not its description. Settings that change decisions (`defaults`, `tools`, `tool_policy`, `runtime`) get ids too. When a library or one of its packs changes:

- **Every loaded id is still there.** The new rules apply at once. `ruleset_strengthened` is recorded.
- **An id was removed or changed.** The daemon keeps enforcing what it loaded, records `config_drift` with the removed contracts, and adds a one-line warning to the next hook reply. The change applies after `sponsio rules reload`. A changed parameter counts as one removal and one addition. A library that no longer loads counts as removing everything.
- **The daemon restarts onto a library weaker than `rules/<bucket>.json`.** With nothing older in memory to serve, it denies that bucket's calls until a reload, and says so in the deny reason.

`sponsio daemon run --watch` applies every change at once and still records each one. It is for working on Sponsio's own rules.

---

## Signed rules

With `signing:` in `daemon.yaml`, every rule file of a covered bucket needs a detached signature beside it: `sponsio.yaml.sig`, the base64 Ed25519 signature of the file's exact bytes. A file the library includes needs its own. Packs shipped inside Sponsio (`sponsio:...`) need none.

- **A file that is not signed, or signed by another key.** The last rules that passed keep enforcing, and `rules_unsigned` is recorded. `sponsio rules status` shows the bucket as not signed on disk, with why. With nothing signed loaded, as after a restart, the bucket's calls are denied, whatever `on_error` says.
- **A signed change.** It is handled like any other change: additions apply, removals are held until `sponsio rules reload`.
- **A covered bucket with no rule file.** Nothing signed says the bucket has no rules, and a removal looks the same as a bucket that never had a file. A running daemon keeps enforcing the last signed rules; with none loaded the bucket is held, whatever `unconfigured` says. With `buckets: ["*"]` this means every bucket a call reaches needs signed rules; list buckets by name to cover only those.
- **A file that does not load** (it does not parse, or an include cannot be read) is held the same way, whatever `on_error` says: nothing proves the failure is in signed content.
- **The bytes checked are the bytes parsed.** The daemon keeps what it read of each file while loading and verifies those bytes, not a second read. A bucket's own `sponsio.yaml` is always checked, also when it links into Sponsio's own files.
- **Changing `signing:`.** Turning it on, or changing the key, applies to rules already loaded: they are checked again under the new key, not kept.
- **A held host library** does not decide anything, its `defaults` included: tools without a library are denied while it is held.

`signing.buckets` lists the buckets covered; anything but a non-empty list of names (or one name) covers every bucket.

Verifying needs `cryptography` (`pip install --pre 'sponsio[signing]'`). Without it, covered buckets are denied, not served unchecked. Signing itself happens where the rules are published; the daemon only verifies.

**Signed by Sponsio Cloud.** The cloud signs every published book it serves with the account's fleet key; drafts are never signed. `sponsio signing-key` prints the `signing:` block to put in `daemon.yaml`. `sponsio pull <project> --agent <bucket> -o <file>` writes the file and `<file>.sig` from the exact bytes served, creating the bucket's directory if needed; add `--public-key <key>` to refuse anything not signed by that key. When an admin rotates the account's key, a daemon pinned to the old one holds its rules until the new key is pinned.

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

**Per session.** One shim process is one session, with its own history under `gateway:<id>`. `tools/list` returns only the tools a call to which the session's current state would admit. A tool behind a `must_precede` is not listed until its prerequisite has run. A call is decided again with its arguments. A call to a tool that is not listed is refused even when its arguments would pass. A refused call never reaches the upstream. A tool whose call a contract would redirect is not listed either: the gateway has no safe tool to substitute, so it refuses that call. Calls in one session run one at a time, and a call enters the history only once it ran: one the upstream refused with a JSON-RPC error, or that could not be sent, satisfies no `must_precede`. A call sent and never answered counts, since it may have run. When a call changes the list, the shim sends `notifications/tools/list_changed`. In observe mode (`hosts: {gateway: {mode: observe}}`) every tool whose library can be decided is listed and runs; a tool refused without a contract (no library under `unconfigured: deny`, a held or broken library) is neither listed nor run.

**Large results and lost replies.** A result too large for a daemon frame (16 MiB) comes back as an error result saying so; the tool ran. If the daemon takes a call and the reply never arrives, the shim reports that and does not send the call again. The shim answers JSON-RPC batches with one reply per request.

**When there is no daemon.** The shim starts one, as the hook shim does. If none answers, `tools/list` returns an error and every call returns an error result.

**Coexisting with hooks.** When Claude Code's hooks are also installed, the hook decides `mcp__sponsio__<server>__<tool>` as `mcp__<server>__<tool>`, against the same library.

The gateway alone does not make Tier 3: an agent that can still run commands can reach the network another way. That needs the sandbox runner (`sponsio run --sandbox`) and the egress proxy.

---

## Egress proxy

`sponsio run --sandbox` asks the daemon for a proxy for its session (`egress.open`) and starts the agent where nothing else on the network is reachable. Every connection is one decision before any byte leaves:

- `CONNECT host:port` (HTTPS, and anything else tunnelled) is decided on the destination;
- a plain-HTTP request is decided on the destination, the method and the URL.

The decision is a call to the tool `egress`, with arguments `host`, `port`, `method` and `url`, in the run's own session (history under `egress:<session>`). It passes when the host and port match `egress.allow` and the `_egress` library, if there is one, admits it: contracts such as `arg_blacklist(egress, host, [...])`, `arg_blacklist(egress, url, [...])` or `rate_limit(egress, 100)`. With neither configured, every connection is refused. A refused connection gets `403` with the reason in the body and an `X-Sponsio-Reason` header.

What the proxy holds to:

- **One spelling per address.** A host is a hostname or a dotted-quad IPv4 address, lower-cased. Other spellings of an address (`2130706433`, `0x7f000001`, `127.1`), IPv6 literals and names with userinfo are refused.
- **Resolved once.** The proxy resolves the name itself and connects to that address, so a DNS answer cannot change between the decision and the connection. Loopback, link-local (cloud metadata), unspecified and reserved addresses are refused unless `egress.allow` names the address.
- **Plain HTTP stays plain.** `CONNECT` to port 80 is refused, so a plain-HTTP request always meets the method and URL contracts. The proxy sends the `Host` header of the decided host, forwards exactly the declared body (a chunked upload gets `411`), and then nothing more from the client: one decided request per connection.

TLS is not intercepted. Contracts see where a connection goes, never what it carries; a contract on the contents of HTTPS traffic is out of scope.

A session is what the client names it. Any process that reaches the agent socket can open a new session, with an empty history, for the egress proxy or the gateway. Contracts that count or order calls hold within a session, not across sessions on a machine (corpus vector V15).

Each session gets a Unix socket in the agent socket's directory (mode 0666) and a port on `127.0.0.1`. The Linux sandbox reaches the socket through a forwarder on `127.0.0.1:3128` inside its own network namespace; the macOS sandbox may open only that port.

---

## Audit events

Every line of `audit.jsonl` is a JSON object with `ts` (UTC, ISO 8601), `event` and `pid`, plus the fields below. It also has `seq` (1, 2, ...), `prev_hash` (the previous record's `hash`) and `hash` (the SHA-256 of the record's canonical JSON without `hash`). Editing or removing a record breaks the chain at that point; `sponsio admin audit verify` finds it, and asks the running daemon for the last record it wrote, so records cut from the end are found too. Records from before the chain existed are counted as unchained, not as a break. When the daemon finds that the log no longer holds the last record it wrote (the log was deleted, cut or rewritten), it does not start a new chain: it writes `audit_log_cut` chained to the record it remembers, so `verify` keeps reporting the break however many records follow. A cut made while the daemon was not running is found only by the exported copy (`audit.otlp_endpoint`); at Tier 2 the agent's user cannot write the log at all. Each exported record's resource carries `sponsio.audit.stream`, a random id kept in `audit.stream` in the state dir, so a receiver keeps two daemons on one host apart and a renamed host is still the same chain. Sponsio Cloud checks the copy it receives and alerts when a record number arrives twice with different content (a log cut back or deleted while the daemon was down), when a record does not match its hash or does not follow its predecessor, and when records never arrive.

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
| `tamper_attempt` (`source: project_layer`) | A project layer tried to weaken a protected contract, or is not the agent's own regular file. Once per file content. | `host`, `source`, `path`, `dropped` |
| `proposal_submitted` | A rule change was proposed. | `id`, `kind`, `peer` |
| `proposal_approved`, `proposal_rejected` | A proposal was decided. `change` is what was applied (the preview, for a rejection). | `id`, `kind`, `peer`, `change`, `params_sha256` |
| `proposal_cloud_refused` | A decision from the cloud console was not applied (other params, no such proposal, or applying failed). Once per proposal. | `id`, `reason` |
| `audit_log_cut` | The log no longer held the last record this daemon wrote. | `expected_seq`, `file_seq` |
| `rules_unsigned` | A covered bucket's rule files failed the signature check. | `bucket`, `reason`, `kept_previous` |
| `cloud_key_set` | The daemon was given a cloud key. | `fingerprint`, `peer` |
| `cloud_drift` | A linked bucket lacks contracts the cloud publishes for its agent. | `bucket`, `agent`, `project`, `status`, `published_version`, `missing` |
| `cloud_verified` | A cloud comparison's outcome changed and is not weaker. | same as `cloud_drift` |

Writing is best effort: a full disk does not stop the daemon from deciding. A line left incomplete by a crash or a full disk is ended before the next record, so that record is not lost; `verify` still reports the incomplete line.

---

**Related:** [Threat model](../concepts/threat-model.md) · [Architecture](../concepts/architecture.md#6-host-enforcement-path) · [CLI reference](cli.md) · [Host operations](../guides/host-operations.md)

# Threat model

Sponsio checks an agent's tool calls before they run. That check is only worth something if two things hold:

1. **Mediation.** Every effect the agent produces passes a Sponsio check first, with no other route.
2. **Privilege boundary.** The agent cannot change Sponsio itself: its rules, its mode, its session history, its hook wiring, its code, or its logs.

How far those hold depends on how Sponsio is deployed. A deployment that comes with a stated guarantee is a **tier**. `sponsio doctor` reports which tier a machine is in, and `sponsio doctor --tier` lists each guarantee with pass or fail and the fix.

## Tiers

| Tier | Deployment | Guarantee | Not covered |
|---|---|---|---|
| **1. Dev** | The daemon runs as the agent's OS user. No sudo. This is the default install. | Contracts are enforced against an agent that is not trying to evade Sponsio. Every hook path fails closed. Changes to rules, mode, wiring and engine files are detected and recorded. | An agent that deliberately edits Sponsio's files, kills the daemon or replaces its code. These are recorded, not prevented. |
| **2. Team** | The daemon runs as a dedicated system user. The shim and engine are owned by root. The host's hooks come from managed settings. The host sandbox is on. | Tier 1, plus: the agent's OS user cannot change rules, mode, session history, hook wiring or logs. | Effects produced inside an allowed shell command or a script the agent wrote. Network exfiltration through allowed commands. |
| **3. Enterprise** | The agent runs in a sandbox or container. Sponsio owns the tool gateway, the credentials and network egress. | Tier 2, plus: every tool call and every network connection goes through Sponsio. The tool list for the current state is enforced, because the agent holds no credentials and has no other route. | Escaping the sandbox or VM. Contracts over the contents of TLS traffic. Covert channels. |

Tier 1 is the default install. Tier 2 is `sudo sponsio install --system` (see [Running Sponsio on a host](../guides/host-operations.md#tier-2-the-system-install)), for Claude Code on macOS and Linux. Cursor stays at Tier 1 with a system install, and OpenClaw reaches Tier 2 only when OpenClaw itself is installed system-wide. Tier 3 is not complete: the MCP gateway ships, the sandbox runner and the egress proxy do not yet. `sponsio doctor --tier` shows what each tier is missing on your machine.

### Which tier fits

| Tier | Fits | Status |
|---|---|---|
| **1. Dev** | A developer running Claude Code, Cursor or OpenClaw on their own machine. A trial, or a first run in observe mode. | This release |
| **2. Team** | Machines a company manages for its staff. Teams that need an audit trail the agent cannot edit. Rules owned by a security team rather than the developer. | This release, for Claude Code on macOS and Linux |
| **3. Enterprise** | Unattended agents in production that touch money, customer data or production systems. | In part: the MCP gateway |

The tiers differ in two things: who can change Sponsio, and whether every action the agent takes has to go through Sponsio. At Tier 1 the agent's OS user can still change Sponsio's files, and Sponsio records it. At Tier 2 file ownership stops it. At Tier 3 the agent has no route to a tool or the network that skips Sponsio.

### Checking your tier

```bash
sponsio doctor          # one line: the tier, and what the next one needs
sponsio doctor --tier   # every guarantee of Tier 1, 2 and 3, pass or fail, with the fix
```

Each check reads the filesystem and the daemon's socket. Nothing the agent's user writes can make a machine report a higher tier.

### What it costs

A hook call goes through a short-lived shim process and the daemon, so it costs more than an in-process check. On macOS the hook path measures 32.6 ms p50 at 10 events in a session and 37.9 ms at 500. An in-process `guard_before()` takes 0.1 to 1 ms. See [Benchmarks](../reference/benchmarks.md#host-hook-path).

## Assumptions

Tier 2 and Tier 3 rest on these:

- The agent cannot obtain root. It has no passwordless sudo, is not in the admin group, and has no reusable sudo ticket.
- The OS kernel and the host application (Claude Code, Cursor, OpenClaw) are trusted.
- A person who approves a rule change reads it.

Tier 1 assumes less and promises less. The agent shares the daemon's OS user, so anything that user can do, the agent can do. Tier 1 is for agents that are not trying to evade Sponsio. It makes an evasion attempt visible, and it keeps a change from silently weakening enforcement while the daemon runs.

## What protects what at Tier 1

**One decision point.** Host hooks run a small shim that forwards each event to the Sponsio daemon. The daemon holds the compiled rules, each session's history (in memory, with a write-ahead log) and its own config. The shim reads no environment variable and makes no decision of its own.

**Fail closed.** If the daemon cannot be reached, the hook names an unknown host, the input is empty or not a JSON object, the library does not load, or the daemon answers with an error, the call is denied in the host's own reply format. Where the input cannot be read or no daemon answers, the hook also exits 2, which blocks on every host. On Claude Code the hook also carries `onFailure: "block"`. Letting a call through when the library fails to evaluate is an explicit daemon setting (`on_error: allow`). Every call let through that way is recorded.

**Knobs live in the daemon.** The mode (enforce or observe), the on-error policy, the policy for tools without rules, the plugin root and the history TTL are read from the daemon's `daemon.yaml`. They are never read from the hook's environment or from the request. The host's settings, including their `env` block, are writable by the agent, so a value set there has no effect on a decision. `sponsio host install --mode` sets the mode through the daemon's admin socket.

**Two sockets.** The agent socket serves hook checks and rule proposals. The admin socket serves everything that changes state. The daemon reads each caller's uid, gid and pid from the kernel, and records every admin call with that identity. At Tier 1 the admin socket is reachable from the agent's user, so the record is what protects you. At Tier 2 the admin socket belongs to the daemon's own user.

**Rule changes that weaken are held.** Every compiled contract has a stable id. When a library changes on disk and every loaded contract is still there, the new rules apply at once. When any contract was removed or changed, the daemon keeps enforcing the rules it has loaded, records the change, and warns on the next hook reply. The change applies only after `sponsio rules reload` is run from your own terminal. A daemon that restarts onto rules weaker than the last approved set refuses that library's calls until a reload. Changes the agent proposes through `sponsio plugin append` are stored as proposals and applied by an approval on the admin socket.

**Tamper evidence.** The daemon polls its rule files, their packs and its own code on the hook path, and records each change with the file's old and new hash. A deleted or truncated session log is restored from memory and recorded. A call denied by the self-protection packs (`capability/self-modify`, `capability/host-config-integrity`) is recorded as an attempted tamper. These records go to the daemon's audit log under its state directory.

**Pattern-based self-protection.** The self-protection packs deny the common ways an agent might edit Sponsio's files, rewrite host hook settings, approve its own rule changes or switch Sponsio to observe. These rules match patterns, and patterns can be evaded. They are a detection layer. The boundary comes from the design above, and at Tier 2 from file ownership.

## What Tier 1 does not cover

- An agent that edits Sponsio's files, stops the daemon or replaces its code can succeed. The attempt is recorded and the deny reasons tell you how to recover, but nothing at Tier 1 prevents it.
- A command the rules allow can still do things the rules did not anticipate. Sponsio mediates tool calls. It does not mediate what a permitted shell command or script does after it starts.
- Starting a second agent session gives it a fresh session history. Contracts that count per session do not carry over, unless you write them to.

## Reporting

`sponsio doctor` shows the tier on every run. `sponsio rules status` and `sponsio rules diff` show held rule changes. The audit log is `<state-dir>/audit.jsonl` (default `~/.sponsio/state/audit.jsonl`).

If you find a way around a guarantee your tier states, please report it privately as described in [SECURITY.md](../../SECURITY.md).

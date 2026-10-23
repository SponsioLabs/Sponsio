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

This release provides Tier 1. Tier 2 needs the system install, and Tier 3 needs the gateway and egress proxy. Neither ships in this release. `sponsio doctor --tier` shows what each one is missing on your machine.

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

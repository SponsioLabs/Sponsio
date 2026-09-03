---
title: The hosted console
description: Connect an agent to app.sponsio.dev. Stream its runs, keep its rulebook in the cloud, and pull the reviewed version back.
---

# The hosted console

Everything else in these docs runs on your machine with no account. This
page is the other half: streaming a run to [app.sponsio.dev](https://app.sponsio.dev)
so you can see what crossed the boundary, and keeping the rulebook there
so a human reviews a rule before it arms.

Enforcement never depends on any of it. The console is where runs are
read and rules are reviewed; if the network is down your agent is
unaffected.

---

## 1. Sign in

Create a key in the console under **Setup**, then:

```bash
sponsio login --key sk_sp_v1_...
```

It checks the key before saving it to `~/.sponsio/credentials` (mode
0600) and prints the projects it can reach.

Confirm what you are pointed at:

```bash
sponsio doctor
```

The **Cloud** row names the endpoint, the tenant, and every agent that
has a rulebook. If it shows a URL you did not expect, a
previous `sponsio login --url ...` is still stored and outranks the
default. Log in again with the right `--url`.

Prefer environment variables in CI: `SPONSIO_API_KEY` and
`SPONSIO_API_URL` are read the same way and need no credentials file.

---

## 2. Stream a run to the console

**This is the step that puts your agent on screen, and it is one line.**
Attaching a bridge to a guard projects each guarded call into a console
step and sends it as the run progresses:

```python
import sponsio
import sponsio.bridge          # note: a submodule, import it explicitly

guard = sponsio.Sponsio(config="sponsio.yaml", agent_id="mailer",
                        mode="enforce")
run = sponsio.bridge.attach(guard)

# ... your agent loop, unchanged ...

run.finish()                   # flushes the final state
```

That is all. `attach` wraps `guard_before` so every checked call is
recorded; `finish()` sends the last frame.

Two rules this path is built to respect:

- **Sending never blocks the agent.** Every upload is best-effort. If the
  console is unreachable the run is unaffected and the failure is
  reported at the end, not raised.
- **Nothing is invented.** A field the run does not carry is left out
  rather than guessed.

### Naming a run

`attach` generates an id. Pass your own when you want to find it again:

```python
run = sponsio.bridge.attach(guard, session_id="nightly-2026-09-03")
```

### Multi-agent runs

For a single-agent loop the default `auto=True` records every guarded
call for you. When several agents share one guard, turn it off and
attribute each step yourself:

```python
run = sponsio.bridge.attach(guard, auto=False,
                            agents=["planner", "writer"])
...
step = run.record(tool_name, args, agent="planner")
```

`agents=` takes bare names, or dicts (`{"id": ..., "role": ...,
"tools": [...]}`) when you want roles on the graph.

### What does not stream a run

Two things look like they should and do not:

- `Sponsio(..., dashboard=True)` targets a **local** dashboard on
  127.0.0.1, not the cloud.
- `sponsio export-sessions --to <url>` ships OTLP spans to the trace
  store. Those are spans, not runs: they do not appear under **Sessions**
  and they carry no contract verdicts.

---

## 3. Keep the rulebook in the cloud

### Push

```bash
sponsio push sponsio.yaml         # the path is required
```

Each agent block becomes the next version of that agent's book.

> **`push` uploads a draft. It does not publish.** The version sits
> unarmed until a human publishes it, which is the point: an agent that
> can publish its own enforcement rules is one nobody can be held
> responsible for. `push` says so on its last line.

### Publish

Publishing is a human action, in the console: open **Rulebook**, review
the version, and publish it. There is no `sponsio publish` command.

List what the key can reach:

```bash
sponsio projects                  # projects, and agents that have a book
```

That list does not say which books are published. An agent with nothing
but drafts is in it. `sponsio pull --agent <name>` is what tells you which
head an agent actually has.

### Pull

```bash
sponsio pull --agent mailer -o sponsio.cloud.yaml
```

`pull` returns the **published** version. A book that has never been
published has no published head to hand out, so you get its latest draft
instead, and the output says so:

```
wrote sponsio.cloud.yaml · 1 · DRAFT (nothing published yet; publish it to pin what pull returns)
```

Read that line. Without it you cannot tell whether the rules you just
wrote into the file your guard loads were reviewed by anyone.

Pin an exact version when you need to reproduce a run:

```bash
sponsio pull --agent mailer --version 46 -o sponsio.v46.yaml
```

### Or let the guard pull for itself

```python
guard = sponsio.Sponsio(config="sponsio://default", agent_id="mailer")
```

A `sponsio://<project>` ref resolves to a cloud checkout when a key is
set and the service answers, the last cached copy when it does not, and
a local yaml if you never pulled. Enforcement never waits on the network,
and the run records which version it enforced.

---

## The loop

```
sponsio login              →  sign in once
sponsio.bridge.attach()    →  runs appear under Sessions
sponsio push sponsio.yaml  →  rulebook uploaded as a draft
(console: Rulebook)        →  a human reviews and publishes
sponsio pull               →  the reviewed version comes back
```

The console's copilot mines proposals from your runs. A proposal is
never a rule until someone arms it, and each one states how thin its own
evidence is. Read that number before arming.

---

**Related:** [Install](install.md) · [Write your first contract](first-contract.md) · [CLI reference](../reference/cli.md) · [Config file](../reference/config-yaml.md)

# Onboarding prompts (paste into Claude Code / Cursor)

When you want an IDE agent (Claude Code, Cursor, Codex) to install
Sponsio for you and propose framework-aware contracts, paste the
matching prompt below into the chat.  The CLI handles the install
(``sponsio init``); the agent does the contract-authoring step the
AST cannot do (reading README / policy / git log to propose
deterministic rules with a one-sentence rationale per rule).

Two prompts, one per language, so neither has to branch on
runtime.  Pick the one that matches your project.

## Python project

```
You are Sponsio's onboarding wizard for a Python project.  Three
steps.

══════════════ Step 0: install the Sponsio CLI ══════════════

  command -v sponsio >/dev/null 2>&1 || pip install sponsio

Quiet check first.  Re-running ``pip install`` against an already-
present version is fine but noisy in IDE-agent transcripts.  Use
``pipx install sponsio`` instead if the user lives in a
pipx-managed shell, or ``uv pip install sponsio`` for uv users;
defer to whatever Python install convention the project's README
shows.

══════════════ Step 1: install Sponsio in the project ══════════════
  sponsio init

The CLI detects framework + IDEs, asks the picks (framework wrap,
per-IDE level, mode), runs onboard / host install / skill install,
and verifies via ``sponsio doctor``.  Surface output verbatim;
when the post-install demo prompt fires, ask the user before
accepting.

If your environment cannot drive an interactive TTY, gather the
picks from the user in chat first, then dispatch:
  sponsio init --apply 'framework=<name>;ides=<ide>:<level>,...;mode=<m>'

The two scopes Sponsio operates at (pick any combination):

  PROJECT scope (axis 1: framework wrap).  Wraps the LLM agent
  the user is wiring Sponsio INTO, e.g. their LangGraph,
  Vercel-AI, or CrewAI program.  Writes ``<project>/sponsio.yaml``
  and splices ``wrap_snippet`` into the agent entry file.  Does
  not touch any IDE.  Pick this alone (axis 2 = all-none) when
  you just want to harden a specific agent the user runs at
  deploy time.  No IDE-side install needed.

  IDE scope (axis 2: per-IDE level).  Decoupled from axis 1.
  Lets the user gate THIS IDE's own tool calls AND/OR teach
  develop agents the Sponsio playbook.  Independent of whether
  the project also has its own LLM agent.

Per-IDE level picks (axis 2):

  none:   leave this IDE alone, no skill, no plugin.

  skill:  knowledge layer only.

           Drops the Sponsio Agent Skill (SKILL.md) into the IDE's
           skill directory.  The next time a develop agent in this
           IDE works on a project ("set up Sponsio in this repo",
           "tune contracts based on policy.md", "explain why C1
           fired"), it has the playbook.  Works across every
           project the user opens in that IDE.

           Does not gate any tool calls at runtime.

  full:   knowledge layer + runtime gating.

           Same skill drop as above, PLUS:
             - Writes a PreToolUse hook into the IDE's settings
               (e.g. ``~/.claude/settings.json``).
             - Bootstraps a contract library at
               ``~/.sponsio/plugins/_host_<ide>/sponsio.yaml``.

           The hook routes every Bash / Edit / Write / MCP call
           THIS IDE itself makes through Sponsio's guard,
           enforcing the rules in that yaml.

           So full is dual control:
             - skill side teaches develop agents (any IDE with
               the skill, any project).
             - plugin side gates THIS IDE's own tool calls (this
               machine, this IDE's runtime).

══════════════ Step 2: propose contracts + wire entry file ══════════════

Skip this step entirely when ``framework=`` was empty (the user
chose to wire it themselves).

  sponsio onboard . --emit-context > /tmp/sponsio-onboard-context.json
  sponsio prompt onboard

Apply the printed template to the JSON in your own context.  The
template owns the rules: what to read beyond the JSON (README /
SECURITY / POLICY / git log / tests), deterministic atoms only,
dedupe via ``pre_existing_contracts``, render as a YAML diff with
a ``rationale:`` per contract citing evidence.  WAIT for the user
to pick which proposals to merge.

After merge:
  - Patch the agent entry file: take ``entry_file_candidates``
    from the JSON, or dedupe ``tool_inventory[*].filepath`` if
    the candidates list is empty.  Skip if the file already
    imports ``from sponsio.<adapter>``.  Otherwise splice
    ``wrap_snippet`` into the file.  Show the diff and WAIT for
    "ok" before writing.
  - sponsio validate --config sponsio.yaml

Done.  Host-plugin tuning, flip to enforce, or debugging a specific
contract: those live in the ``sponsio`` skill, not in this prompt.
```

## TypeScript project

> **Note**: ``npx sponsio init`` is currently the older single-
> axis init (provider / mode / agent only).  The four-axis wizard
> (framework × per-IDE level × mode, with ``host install`` /
> ``skill install`` dispatch) lives in the Python ``sponsio``
> package today.  If you want full IDE-host protection, use the
> Python prompt above instead.  This TS prompt is the path for
> a TS-only setup where Python is not an option.

```
You are Sponsio's onboarding wizard for a TypeScript / JavaScript
project.  Three steps.

══════════════ Step 0: install the Sponsio CLI ══════════════

  command -v sponsio >/dev/null 2>&1 || npm install -D @sponsio/sdk

Quiet check first.  Re-running ``npm install`` against an already-
present version is fine but noisy in IDE-agent transcripts.  The
binary lands in ``node_modules/.bin/sponsio`` so ``npx sponsio``
resolves locally without a global install.

══════════════ Step 1: install Sponsio in the project ══════════════
  npx sponsio init

The CLI detects framework + IDEs, asks the picks (framework wrap,
per-IDE level, mode), runs onboard / host install / skill install,
and verifies via ``npx sponsio doctor``.  Surface output verbatim;
when the post-install demo prompt fires, ask the user before
accepting.

If your environment cannot drive an interactive TTY, gather the
picks from the user in chat first, then dispatch:
  npx sponsio init --apply 'framework=<name>;ides=<ide>:<level>,...;mode=<m>'

The two scopes Sponsio operates at (pick any combination):

  PROJECT scope (axis 1: framework wrap).  Wraps the LLM agent
  the user is wiring Sponsio INTO, e.g. their Vercel-AI,
  LangGraph, or Anthropic-SDK program.  Writes
  ``<project>/sponsio.yaml`` and splices ``wrap_snippet`` into
  the agent entry file.  Does not touch any IDE.  Pick this alone
  (axis 2 = all-none) when you just want to harden a specific
  agent the user runs at deploy time.  No IDE-side install needed.

  IDE scope (axis 2: per-IDE level).  Decoupled from axis 1.
  Lets the user gate THIS IDE's own tool calls AND/OR teach
  develop agents the Sponsio playbook.  Independent of whether
  the project also has its own LLM agent.

Per-IDE level picks (axis 2):

  none:   leave this IDE alone, no skill, no plugin.

  skill:  knowledge layer only.

           Drops the Sponsio Agent Skill (SKILL.md) into the IDE's
           skill directory.  The next time a develop agent in this
           IDE works on a project ("set up Sponsio in this repo",
           "tune contracts based on policy.md", "explain why C1
           fired"), it has the playbook.  Works across every
           project the user opens in that IDE.

           Does not gate any tool calls at runtime.

  full:   knowledge layer + runtime gating.

           Same skill drop as above, PLUS:
             - Writes a PreToolUse hook into the IDE's settings
               (e.g. ``~/.claude/settings.json``).
             - Bootstraps a contract library at
               ``~/.sponsio/plugins/_host_<ide>/sponsio.yaml``.

           The hook routes every Bash / Edit / Write / MCP call
           THIS IDE itself makes through Sponsio's guard,
           enforcing the rules in that yaml.

           So full is dual control:
             - skill side teaches develop agents (any IDE with
               the skill, any project).
             - plugin side gates THIS IDE's own tool calls (this
               machine, this IDE's runtime).

══════════════ Step 2: propose contracts + wire entry file ══════════════

Skip this step entirely when ``framework=`` was empty (the user
chose to wire it themselves).

  npx sponsio onboard . --emit-context > /tmp/sponsio-onboard-context.json
  npx sponsio prompt onboard

Apply the printed template to the JSON in your own context.  The
template owns the rules: what to read beyond the JSON (README /
SECURITY / POLICY / git log / tests), deterministic atoms only,
dedupe via ``pre_existing_contracts``, render as a YAML diff with
a ``rationale:`` per contract citing evidence.  WAIT for the user
to pick which proposals to merge.

After merge:
  - Patch the agent entry file: take ``entry_file_candidates``
    from the JSON, or dedupe ``tool_inventory[*].filepath`` if
    the candidates list is empty.  Skip if the file already
    imports from ``@sponsio/sdk``.  Otherwise splice
    ``wrap_snippet`` into the file.  Show the diff and WAIT for
    "ok" before writing.
  - npx sponsio validate sponsio.yaml

Done.  Host-plugin tuning, flip to enforce, or debugging a specific
contract: those live in the ``sponsio`` skill, not in this prompt.
```

## Why two phases (CLI then agent)

The CLI is faster + deterministic at install: it knows the
package layout, the host-bucket conventions, the doctor checks,
the demo, the dispatch rules.  The agent's value-add is everywhere
the AST cannot reach: reading prose policy docs, KPI strings in
the system prompt, test invariants, and git history's "we got
burned by X last time" notes.  Splitting the work that way avoids
the agent silently auto-picking install choices on the user's
behalf, AND gives the user concrete framework-specific contracts
at the end instead of just a starter pack.

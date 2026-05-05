# Onboarding prompts (paste into Claude Code / Cursor)

When you want an IDE agent — Claude Code, Cursor, Codex — to install
Sponsio for you and propose framework-aware contracts, paste the
matching prompt below into the chat.  The CLI handles the install
(``sponsio init``); the agent does the contract-authoring step the
AST can't do (reading README / policy / git log to propose
deterministic rules with a one-sentence rationale per rule).

Two prompts — one per language — so neither has to branch on
runtime.  Pick the one that matches your project.

## Python project

```
You are Sponsio's onboarding wizard for a Python project.  Three
steps.

══════════════ Step 0 — install the Sponsio CLI ══════════════

  command -v sponsio >/dev/null 2>&1 || pip install sponsio

Quiet check first — re-running ``pip install`` against an already-
present version is fine but noisy in IDE-agent transcripts.  Use
``pipx install sponsio`` instead if the user lives in a
pipx-managed shell, or ``uv pip install sponsio`` for uv users;
defer to whatever Python install convention the project's README
shows.

══════════════ Step 1 — install Sponsio in the project ══════════════
  sponsio init

The CLI detects framework + IDEs, asks the picks (framework wrap,
per-IDE level, mode), runs onboard / host install / skill install,
and verifies via ``sponsio doctor``.  Surface output verbatim;
when the post-install demo prompt fires, ask the user before
accepting.

If your environment can't drive an interactive TTY, gather the
picks from the user in chat first, then dispatch:
  sponsio init --apply 'framework=<name>;ides=<ide>:<level>,...;mode=<m>'
where <level> is one of ``none`` / ``skill`` / ``full``.

══════════════ Step 2 — propose contracts + wire entry file ══════════════

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

Done.  Host-plugin tuning, refresh from traces, flip to enforce,
or debugging a specific contract — those live in the ``sponsio``
skill, not in this prompt.
```

## TypeScript project

> **Note** — ``npx sponsio init`` is currently the older single-
> axis init (provider / mode / agent only).  The four-axis wizard
> (framework × per-IDE level × mode, with ``host install`` /
> ``skill install`` dispatch) lives in the Python ``sponsio``
> package today.  If you want full IDE-host protection, use the
> Python prompt above instead.  This TS prompt is the path for
> a TS-only setup where Python isn't an option.

```
You are Sponsio's onboarding wizard for a TypeScript / JavaScript
project.  Three steps.

══════════════ Step 0 — install the Sponsio CLI ══════════════

  command -v sponsio >/dev/null 2>&1 || npm install -D @sponsio/sdk

Quiet check first — re-running ``npm install`` against an already-
present version is fine but noisy in IDE-agent transcripts.  The
binary lands in ``node_modules/.bin/sponsio`` so ``npx sponsio``
resolves locally without a global install.

══════════════ Step 1 — install Sponsio in the project ══════════════
  npx sponsio init

The CLI detects framework + IDEs, asks the picks (framework wrap,
per-IDE level, mode), runs onboard / host install / skill install,
and verifies via ``npx sponsio doctor``.  Surface output verbatim;
when the post-install demo prompt fires, ask the user before
accepting.

If your environment can't drive an interactive TTY, gather the
picks from the user in chat first, then dispatch:
  npx sponsio init --apply 'framework=<name>;ides=<ide>:<level>,...;mode=<m>'
where <level> is one of ``none`` / ``skill`` / ``full``.

══════════════ Step 2 — propose contracts + wire entry file ══════════════

Skip this step entirely when ``framework=`` was empty.

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

Done.  Host-plugin tuning, refresh from traces, flip to enforce,
or debugging a specific contract — those live in the ``sponsio``
skill, not in this prompt.
```

## Why two phases (CLI then agent)

The CLI is faster + deterministic at install: it knows the
package layout, the host-bucket conventions, the doctor checks,
the demo, the dispatch rules.  The agent's value-add is everywhere
the AST can't reach — reading prose policy docs, KPI strings in
the system prompt, test invariants, git history's "we got burned
by X last time".  Splitting the work that way avoids the agent
silently auto-picking install choices on the user's behalf, AND
gives the user concrete framework-specific contracts at the end
instead of just a starter pack.

# Manual verification: Sponsio Agent Skill in real tools

`tests/test_skill_doc_sync.py` automatically verifies that the CLI surface
promised in `SKILL.md` actually exists. What it **cannot** verify is
whether real coding agents (Cursor, Claude Code, Codex) correctly
**discover** and **trigger** the skill. That's a product-level check:
it depends on third-party tooling and user phrasing.

Run this checklist after any material change to
`sponsio/skills/sponsio/SKILL.md` (especially frontmatter
`description:`, workflow names, or trigger phrases), or before a
release.

## Setup (~2 min)

In a clean workspace:

```bash
pip install -e ".[all]"
sponsio --version
sponsio skill install --tool all   # copies into ~/.cursor, ~/.claude, ~/.codex
ls -la ~/.cursor/skills/sponsio/ ~/.claude/skills/sponsio/
```

Expected: each destination has `SKILL.md` with a non-zero byte count,
frontmatter `name: sponsio`.

## T1 — Cursor Desktop discovers the skill

1. Open a fresh throwaway project in Cursor (e.g. `mkdir /tmp/skill-t1
   && cd /tmp/skill-t1 && touch agent.py`).
2. In the Cursor chat, type exactly: **"add sponsio to this project"**.
3. Observe whether Cursor's agent invokes the `sponsio` skill (should
   be visible in the activity panel; phrasing varies by version — look
   for something like "using skill: sponsio").

Pass: skill triggers, agent paraphrases the **W1 — Initial setup**
workflow (runs `sponsio onboard .`, then shows the generated
`sponsio.yaml`, the applied patch, and any `doctor` warnings).

Fail: skill doesn't trigger. Try these backup phrasings, record which
ones work:

- "install sponsio"
- "add guardrails to my agent"
- "harden this agent"
- "monitor my agent"

If NONE trigger, the frontmatter `description:` is losing the auto-dispatch
fight. File back into `SKILL.md` frontmatter: emphasise verbs that match
Cursor's own dispatcher heuristics.

## T2 — Claude Code discovers the skill

Same sequence as T1 but in Claude Code with `cd /tmp/skill-t1` in a
terminal, then the equivalent "add sponsio to this project" prompt.
Claude Code's skill discovery uses the same frontmatter; the triggers
should behave identically.

Pass/fail criteria same as T1.

## T3 — Workflow dispatch is correct

Within the same session (Cursor OR Claude Code), after setup succeeds,
test each workflow phrase lands on the right branch:

| Phrase | Expected workflow |
|---|---|
| "add sponsio to this project" | W1 Initial setup |
| "explain my sponsio.yaml" | W2 Audit & refine (explain-only path) |
| "sponsio report — what would have been blocked" | W3 Tune in observe |
| "refresh contracts from traces" | W3b Refresh from traces |
| "flip sponsio to enforce mode" | W4 Flip to enforce |
| "why is this rule firing" | W5 Troubleshoot |

The agent should paraphrase the specific workflow, not mix them.
(It's fine if it asks one clarifying question first — see each workflow's
"if ambiguous" clause.)

## T4 — Destructive-action gates actually gate

Test that the skill does NOT silently run dangerous commands:

1. In a project where `sponsio.yaml` already exists, prompt: **"run
   sponsio onboard"**. Skill should notice the existing file and
   either prompt to overwrite or skip.
2. In W4 Flip-to-enforce, before agreeing to `mode: enforce`, the
   skill should run the dry-run regression (`sponsio check --trace`)
   per the workflow. If it skips to `enforce` without verifying: FAIL.

## T5 — Uninstall & re-install

```bash
rm -rf ~/.cursor/skills/sponsio ~/.claude/skills/sponsio ~/.codex/skills/sponsio
sponsio skill install --link
```

Restart Cursor / Claude Code. Skill should still be discoverable (symlink
points into `site-packages/sponsio/skills/sponsio/`). `pip install -U
sponsio` should propagate to the symlinked install without needing
another `sponsio skill install`.

## What counts as "skill done"

- T1 **and** T2 pass with the primary phrase ("add sponsio to this
  project") on at least one fresh project.
- T3 shows at least 4 of 6 workflow phrases dispatching correctly. The
  rest are polish, not blockers.
- T5 symlink upgrade works.

Everything else is nice-to-have. Record failures with the exact
phrasing that didn't work so the `description:` field can be tuned in
the next pass.

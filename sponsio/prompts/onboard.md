# Contract authoring prompt — sponsio onboard

You are a security engineer authoring a `sponsio.yaml` for a project
the user just installed Sponsio in.  The CLI has already done the
deterministic work (framework detection, AST-based tool inventory,
auto-selected packs).  Your job is the **semantic gap**: rules a
name-heuristic can't see.

## Input

A JSON object from `sponsio onboard --emit-context`:

```json
{
  "framework": {"name": "...", "evidence": "..."},
  "agent_id": "...",
  "tool_inventory": [
    {"name": "...", "description": "...", "params": {...}, "source_file": "..."}
  ],
  "auto_selected_packs": ["sponsio:core/universal", ...],
  "existing_yaml": "<current sponsio.yaml content if any>",
  "policy_docs": [
    {"path": "security.md", "content": "..."}
  ]
}
```

## What you produce

A single YAML document — the new (or revised) `sponsio.yaml`.
Structure:

```yaml
version: "1"
mode: observe
agents:
  <agent_id>:
    include:
      - <pack from auto_selected_packs>
      - ...
    contracts:
      - desc: "..."
        E:
          pattern: ...
          args: [...]
        A:
          # optional — when conditional
          pattern: ...
          args: [...]
```

## Rules of thumb (apply in this order)

1. **Pack first** — every pack in `auto_selected_packs` goes in
   `include:`.  Don't try to inline what the pack already covers;
   redundant rules just clutter review.
2. **Tool-specific contracts** — for each tool the inventory shows:
   - **Destructive verbs** (`delete_*`, `remove_*`, `transfer_*`,
     `force_*`) → `irreversible_once` if irreversible, otherwise
     tight `rate_limit`.
   - **Outbound side-effects** (`send_email`, `post_*`,
     `create_*_comment`) → `rate_limit` 5–10 + `arg_blacklist` for
     target identifiers if you can constrain them.
   - **Path / URL params** → `arg_blacklist` for sensitive paths
     (`\.env`, `\.aws/credentials`, `\.ssh/`) and internal hosts.
   - **Read tools** → skip unless they touch credentials.
3. **Policy doc** — if `policy_docs` is non-empty, lift specific
   rules from them.  Each one becomes a contract with
   `source: policy`.
4. **Existing YAML** — if `existing_yaml` is non-empty, **merge**:
   keep all user-written contracts intact, add new ones for tools
   that aren't covered.  Never silently delete.

## Pattern vocabulary

(Same as plugin scan — `arg_blacklist`, `arg_value_range`,
`arg_length_limit`, `rate_limit`, `loop_detection`,
`irreversible_once`, `must_precede`.  See
``sponsio plugin prompt mcp-bare`` for full signatures.)

## Source tagging

Every contract YOU author should carry `source: agent-extracted` so
future `sponsio refresh` runs know they were agent-generated and
can be re-considered.  Don't tag pack-included rules — those have
their own source from the pack.

## What to do after

You produce **two** edits, in this order:

### 1. Write `sponsio.yaml`

Write the YAML you authored above to the path in `out_path` (default:
project root). Use the host's Edit/Write tool. If `existing_yaml`
was non-empty, merge — don't clobber.

### 2. Patch the agent entry file

`wrap_snippet` shows the imports and guard construction. Your job is
to splice those into the actual entry file so Sponsio is wired into
the runtime, not just printed in the docs.

**Locate the entry file** (use `entry_file_candidates` if present in
the context — the CLI's deterministic scan flagged files that import
the framework). If absent, fall back to:

  - Python: files that instantiate the framework's agent class
    (e.g. `Agent(...)`, `LangGraph(...)`, `ClaudeAgent(...)`),
    pointed at by `pyproject.toml`'s entry points or the project's
    run script.
  - TypeScript: files that call `generateText` / construct the
    Anthropic / OpenAI client / build a LangChain ToolNode, pointed
    at by `package.json`'s `main` / `bin` / `scripts.start`.

If multiple candidates remain after that filter, **stop and ask the
user** rather than guessing — picking wrong creates a duplicate
guard or a partial wire-up that's worse than nothing.

**Apply the wrap** (framework-specific):

  - **Vercel AI SDK**: change every `model: <provider>("...")` site
    to `model: wrapLanguageModel({ model: <provider>("..."),
    middleware: sponsioMiddleware(guard) })`. Add the imports at the
    top.
  - **LangChain / LangGraph**: replace `tools` arrays passed to
    `ToolNode` / `createReactAgent` with `wrapTools(tools, guard)`.
  - **Claude Agent SDK / OpenAI Agents SDK**: wrap the underlying
    client per the snippet.
  - **Bare function-calling loop** (no framework): insert
    `guard.guardBefore(toolName, args)` before the tool executes,
    `guard.guardAfter(toolName, result)` after. Show the user the
    diff before applying — this path edits more lines.

**Always**: keep the original imports intact, add Sponsio's *above*
the agent entry's existing imports, and re-run the project's type
check / lint after edit.

### 3. Hand off

Show the user a short summary: which contracts you wrote, which file
you patched (with line range), and the next command to run
(`sponsio doctor`, then their normal `npm test` / `pytest`).

# Contract extraction prompt — target host: bare MCP

You are a security engineer reviewing an MCP server's tool inventory
for runtime contract enforcement with Sponsio.

The host hasn't been specified, so use the bare tool names exactly
as they appear in `tools/list`. Be conservative.

## How to think about each tool

(Same heuristics as the host-specific prompts — destructive verbs
get `irreversible_once` or tight `rate_limit`; outbound
side-effects get caps + arg blacklists; sensitive-path params get
`arg_blacklist`.)

## Pattern vocabulary

(Loaded from `_pattern_vocabulary.md` — use ONLY those patterns.)

## Output schema

```json
{
  "contracts": [
    {"desc": "...", "pattern": "...", "args": [...]}
  ]
}
```

0–3 contracts per tool. Skip benign tools.

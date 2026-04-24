/**
 * Grounding: convert tool-call events into per-timestep predicate valuations.
 *
 * Port of sponsio/tracer/grounding.py (Det-relevant atoms).
 *
 * Supported atoms:
 *   called(tool)                       — tool was called at this timestep
 *   count(tool)                        — cumulative invocation count
 *   called_with(tool, pattern)         — tool args match regex pattern
 *   count_with(tool, pattern)          — cumulative count of pattern matches
 *   consecutive_count(tool)            — how many times same tool called in a row
 *   arg_has(tool, pattern)             — tool args (serialized) match regex
 *   arg_field_has(tool, field, p)      — specific arg field matches regex
 *   arg_length_exceeds(tool, field, N) — field length > N chars
 *   arg_numeric(tool, field)           — numeric value extracted from args (int)
 *   arg_paths_within(tool, *prefixes)  — all paths in args within prefix set
 *   token_count(type)                  — cumulative tokens (from event.args.tokens)
 *   delegation_depth                   — depth of agent-to-agent delegation
 */

import { predKey } from "./formula.js";
import type { Formula, Atom as AtomNode, Var as VarNode } from "./formula.js";

export interface ToolEvent {
  tool: string;
  args?: Record<string, unknown>;
}

export interface GroundingState {
  callCounts: Record<string, number>;
  callWithCounts: Record<string, number>;
  lastTool: string | null;
  consecutiveCounts: Record<string, number>;
  tokenCounts: Record<string, number>;
  delegationDepth: number;
}

export function newGroundingState(): GroundingState {
  return {
    callCounts: {},
    callWithCounts: {},
    lastTool: null,
    consecutiveCounts: {},
    tokenCounts: {},
    delegationDepth: 0,
  };
}

/**
 * Collect content atoms from a formula tree.
 * These are atoms whose grounding depends on event content (arg patterns).
 */
export function collectContentAtoms(
  formulas: Formula[],
): Record<string, Set<string>> {
  const atoms: Record<string, Set<string>> = {};

  function walk(node: Formula) {
    if (node.kind === "Atom") {
      const pred = node.predicate;
      if ([
        "called_with", "count_with",
        "arg_has", "arg_field_has", "arg_length_exceeds", "arg_paths_within",
      ].includes(pred)) {
        if (!atoms[pred]) atoms[pred] = new Set();
        atoms[pred].add(node.args.join("|"));
      }
    } else if (node.kind === "Var") {
      if ([
        "count_with", "count", "consecutive_count",
        "arg_numeric", "token_count", "delegation_depth",
      ].includes(node.name)) {
        const pred = node.name;
        if (!atoms[pred]) atoms[pred] = new Set();
        atoms[pred].add(node.args.join("|"));
      }
    }

    // Recurse into children
    if ("child" in node && node.child) walk(node.child as Formula);
    if ("left" in node && node.left) walk(node.left as Formula);
    if ("right" in node && node.right) walk(node.right as Formula);
  }

  for (const f of formulas) walk(f);
  return atoms;
}

/**
 * Ground a single tool-call event into a predicate valuation dict.
 */
export function groundEvent(
  event: ToolEvent,
  state: GroundingState,
  contentAtoms?: Record<string, Set<string>>,
): Record<string, boolean | number> {
  const v: Record<string, boolean | number> = {};
  const tool = event.tool;
  const argsStr = event.args ? JSON.stringify(event.args) : "";

  // called(tool)
  v[predKey("called", tool)] = true;

  // called_any — fires whenever any tool is invoked. Used by
  // ``tool_allowlist`` to encode "whenever any tool runs, it must
  // be one of the allowed ones".
  v[predKey("called_any")] = true;

  // count(tool)
  state.callCounts[tool] = (state.callCounts[tool] || 0) + 1;
  v[predKey("count", tool)] = state.callCounts[tool];

  // consecutive_count(tool)
  if (tool === state.lastTool) {
    state.consecutiveCounts[tool] = (state.consecutiveCounts[tool] || 1) + 1;
  } else {
    if (state.lastTool) state.consecutiveCounts[state.lastTool] = 0;
    state.consecutiveCounts[tool] = 1;
  }
  state.lastTool = tool;
  v[predKey("consecutive_count", tool)] = state.consecutiveCounts[tool];

  // called_with / count_with — regex on serialized args
  if (contentAtoms) {
    const cwPatterns = new Set<string>();
    for (const key of ["called_with", "count_with"]) {
      const s = contentAtoms[key];
      if (s) for (const p of s) cwPatterns.add(p);
    }

    for (const raw of cwPatterns) {
      const parts = raw.split("|");
      if (parts.length >= 2) {
        const targetTool = parts[0];
        const pattern = parts.slice(1).join("|");
        if (targetTool === tool) {
          const matched = new RegExp(pattern).test(argsStr);
          v[predKey("called_with", targetTool, pattern)] = matched;
          if (matched) {
            const cwKey = `${targetTool}|${pattern}`;
            state.callWithCounts[cwKey] = (state.callWithCounts[cwKey] || 0) + 1;
          }
          v[predKey("count_with", targetTool, pattern)] =
            state.callWithCounts[`${targetTool}|${pattern}`] || 0;
        }
      }
    }

    // arg_has(tool, pattern)
    const argHasPatterns = contentAtoms["arg_has"];
    if (argHasPatterns && argsStr) {
      for (const raw of argHasPatterns) {
        const parts = raw.split("|");
        if (parts.length >= 2) {
          const targetTool = parts[0];
          const pattern = parts.slice(1).join("|");
          if (targetTool === tool) {
            v[predKey("arg_has", targetTool, pattern)] = new RegExp(pattern).test(argsStr);
          }
        }
      }
    }

    // arg_field_has(tool, field, pattern)
    const afhPatterns = contentAtoms["arg_field_has"];
    if (afhPatterns && event.args) {
      for (const raw of afhPatterns) {
        const parts = raw.split("|");
        if (parts.length >= 3) {
          const targetTool = parts[0];
          const field = parts[1];
          const pattern = parts.slice(2).join("|");
          if (targetTool === tool) {
            const fieldVal = event.args[field];
            const matched = fieldVal != null && new RegExp(pattern).test(String(fieldVal));
            v[predKey("arg_field_has", targetTool, field, pattern)] = matched;
          }
        }
      }
    }

    // arg_length_exceeds(tool, field, max_chars)
    const ale = contentAtoms["arg_length_exceeds"];
    if (ale && event.args) {
      for (const raw of ale) {
        const parts = raw.split("|");
        if (parts.length >= 3) {
          const targetTool = parts[0];
          const field = parts[1];
          const maxChars = parseInt(parts[2], 10) || 500;
          if (targetTool === tool) {
            const fieldVal = event.args[field] ?? "";
            v[predKey("arg_length_exceeds", targetTool, field, String(maxChars))] =
              String(fieldVal).length > maxChars;
          }
        }
      }
    }

    // arg_numeric(tool, field) — extract numeric value via 3 strategies:
    //   1. Direct dict key
    //   2. CLI flag "--field VALUE"
    //   3. Positional token in command string
    const anPatterns = contentAtoms["arg_numeric"];
    if (anPatterns) {
      for (const raw of anPatterns) {
        const parts = raw.split("|");
        if (parts.length >= 2) {
          const targetTool = parts[0];
          const field = parts[1];
          if (targetTool === tool) {
            let numericVal: number | null = null;

            // Strategy 1: direct dict key
            if (event.args && field in event.args) {
              const val = event.args[field];
              const n = typeof val === "number" ? val : parseFloat(String(val));
              if (!isNaN(n)) numericVal = n;
            }

            // Strategy 2: CLI --field VALUE
            if (numericVal == null && argsStr) {
              const flagRe = new RegExp(`--${field.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s+([+-]?\\d+(?:\\.\\d+)?)`);
              const m = argsStr.match(flagRe);
              if (m) numericVal = parseFloat(m[1]);
            }

            // Strategy 3: positional index
            if (numericVal == null && event.args && /^\d+$/.test(field)) {
              const cmdStr = String(event.args.command ?? "");
              const tokens = cmdStr.split(/\s+/);
              const pos = parseInt(field, 10);
              if (pos < tokens.length) {
                const n = parseFloat(tokens[pos]);
                if (!isNaN(n)) numericVal = n;
              }
            }

            if (numericVal != null) {
              v[predKey("arg_numeric", targetTool, field)] = numericVal;
            }
          }
        }
      }
    }

    // arg_paths_within(tool, *prefixes) — all /-paths in args within prefix set
    const apw = contentAtoms["arg_paths_within"];
    if (apw && argsStr) {
      for (const raw of apw) {
        const parts = raw.split("|");
        if (parts.length >= 2) {
          const targetTool = parts[0];
          const prefixes = parts.slice(1);
          if (targetTool === tool) {
            const paths = argsStr.match(/(\/[^\s;|&>"']+)/g) ?? [];
            const allWithin = paths.length === 0 ||
              paths.every((p) => prefixes.some((pre) => p.startsWith(pre)));
            v[predKey("arg_paths_within", targetTool, ...prefixes)] = allWithin;
          }
        }
      }
    }
  }

  // token_count(type) — cumulative token count
  // Extract from event.args.tokens if present (gen_ai.usage.* convention)
  if (event.args) {
    const tokens = event.args.tokens as Record<string, number> | undefined;
    if (tokens) {
      if (typeof tokens.input === "number") {
        state.tokenCounts["input_tokens"] = (state.tokenCounts["input_tokens"] || 0) + tokens.input;
      }
      if (typeof tokens.output === "number") {
        state.tokenCounts["output_tokens"] = (state.tokenCounts["output_tokens"] || 0) + tokens.output;
      }
      const total = (tokens.input || 0) + (tokens.output || 0);
      state.tokenCounts["total"] = (state.tokenCounts["total"] || 0) + total;
    }
  }
  for (const [scope, cnt] of Object.entries(state.tokenCounts)) {
    v[predKey("token_count", scope)] = cnt;
  }

  // delegation_depth — incremented by event.event_type === "delegation"
  if ((event as unknown as { event_type?: string }).event_type === "delegation") {
    state.delegationDepth += 1;
  }
  v[predKey("delegation_depth")] = state.delegationDepth;

  // Emit count/count_with for all tracked vars
  for (const [t, cnt] of Object.entries(state.callCounts)) {
    v[predKey("count", t)] = cnt;
  }
  for (const [key, cnt] of Object.entries(state.callWithCounts)) {
    const [t, p] = [key.split("|")[0], key.split("|").slice(1).join("|")];
    v[predKey("count_with", t, p)] = cnt;
  }

  return v;
}

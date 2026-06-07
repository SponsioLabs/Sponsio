/**
 * Grounding: convert events into per-timestep predicate valuations.
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
 *   ctx(key, value)                    — external fact pushed via observe_context
 *   ctx_matches(key, pattern)          — current ctx[key] matches regex
 *   llm_said(pattern)                  — llm_response content matches regex
 *   response_words / response_chars    — response length on llm_response
 *   time_since(predicate_key)          — event-clock delta since predicate last True
 */

import { predKey } from "./formula.js";
import type { Formula } from "./formula.js";

/**
 * Public event shape consumed by the grounding kernel. ``tool`` is
 * required for the dominant ``tool_call`` path; the optional fields
 * cover ``llm_response`` (response-length / regex content checks) and
 * ``context_update`` (external-fact ``ctx`` atoms).
 */
export interface ToolEvent {
  tool: string;
  args?: Record<string, unknown>;
  /** ``"tool_call"`` (default) | ``"llm_response"`` | ``"context_update"`` | ``"delegation"``. */
  event_type?: string;
  /** Free-form text — only consumed for ``llm_response`` events (regex matching, length). */
  content?: string;
  /** Optional event timestamp; if omitted, the grounding state's monotonic clock advances by 1. */
  ts?: number;
}

export interface GroundingState {
  callCounts: Record<string, number>;
  callWithCounts: Record<string, number>;
  lastTool: string | null;
  consecutiveCounts: Record<string, number>;
  tokenCounts: Record<string, number>;
  delegationDepth: number;
  /**
   * External facts pushed by the integration via ``observeContext``.
   * Persists across events; re-emitted as ``ctx(k, v)`` atoms each
   * timestep so ``G(called(x) → ctx(k, v))`` fires as expected.
   */
  currentCtx: Record<string, string>;
  /**
   * Event clock — advances by 1 per event when no explicit ``ts`` is
   * supplied. Used as the numerator for ``time_since(key)`` and
   * accessible to formulas via ``Var("now")``.
   */
  now: number;
  /**
   * Per-predicate-key timestamp of the most recent False→True
   * transition. Re-emissions of a sustained predicate (forward-
   * propagated ``flow``/``contains``, ``ctx`` atoms held in scope)
   * do NOT refresh this — that's what makes ``time_since(approval)``
   * measure "since granted" rather than "since last re-emit".
   */
  lastTs: Record<string, number>;
  /** Predicates that were True at the previous event (post-emission). */
  trueAtPrev: Set<string>;
}

export function newGroundingState(): GroundingState {
  return {
    callCounts: {},
    callWithCounts: {},
    lastTool: null,
    consecutiveCounts: {},
    tokenCounts: {},
    delegationDepth: 0,
    currentCtx: {},
    now: 0,
    lastTs: {},
    trueAtPrev: new Set(),
  };
}

/**
 * Predicates whose grounding requires the formula AST to tell
 * grounding which arg tuples to check (regex on content, ctx_matches,
 * time_since key targets, …). Mirrors Python's ``_CONTENT_PREDICATES``.
 */
const CONTENT_PREDICATES = new Set<string>([
  "called_with",
  "count_with",
  "arg_has",
  "arg_field_has",
  "arg_length_exceeds",
  "arg_paths_within",
  "arg_numeric",
  "llm_said",
  "ctx_matches",
  "time_since",
]);

/**
 * Var names that must be extracted from the formula tree for grounding.
 * Includes both the atom-style content predicates and bare arithmetic
 * Vars so ``Le(Var(count, x), Const(N))`` carries ``("x",)`` through.
 */
const VAR_PREDICATES = new Set<string>([
  "count_with",
  "count",
  "consecutive_count",
  "arg_numeric",
  "token_count",
  "delegation_depth",
  "time_since",
]);

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
      if (CONTENT_PREDICATES.has(pred)) {
        if (!atoms[pred]) atoms[pred] = new Set();
        atoms[pred].add(node.args.join("|"));
      }
    } else if (node.kind === "Var") {
      if (VAR_PREDICATES.has(node.name)) {
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

function escapeRegexLiteral(literal: string): string {
  return literal.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Ground a single event into a predicate valuation dict.
 *
 * Routes by ``event.event_type``:
 *   - ``tool_call`` (default) — emits the full tool-call atom set
 *   - ``llm_response`` — emits ``llm_said(pattern)`` + length atoms
 *   - ``context_update`` — merges ``event.args`` into ``state.currentCtx``
 *
 * Ctx atoms (``ctx(k, v)``), accumulator snapshots, and ``time_since``
 * fire on every event regardless of type (matching Python parity).
 */
export function groundEvent(
  event: ToolEvent,
  state: GroundingState,
  contentAtoms?: Record<string, Set<string>>,
): Record<string, unknown> {
  const v: Record<string, unknown> = {};
  const eventType = event.event_type ?? "tool_call";

  // Advance the event clock (mirrors ``state.now = float(event.ts)``).
  if (typeof event.ts === "number") {
    state.now = event.ts;
  } else {
    state.now += 1;
  }

  if (eventType === "tool_call") {
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

    // arg_value(tool, field) — raw value for Term-based lookups.
    // ``ArgValue(tool, field)`` reads state[predKey("arg_value", tool,
    // field)] for runtime comparisons (e.g. Eq(ArgValue(tool, "amount"),
    // CtxValue("approved_amount"))). Emit unconditionally for every arg
    // field, every tool_call — Terms read by direct key lookup, not via
    // contentAtoms extraction.
    if (event.args) {
      for (const [field, val] of Object.entries(event.args)) {
        v[predKey("arg_value", tool, field)] = val;
      }
    }

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
                const flagRe = new RegExp(`--${escapeRegexLiteral(field)}\\s+([+-]?\\d+(?:\\.\\d+)?)`);
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
  } else if (eventType === "llm_response") {
    // ── llm_said(pattern) ───────────────────────────────────────
    const content = event.content ?? "";
    if (content && contentAtoms && contentAtoms["llm_said"]) {
      for (const raw of contentAtoms["llm_said"]) {
        const pattern = raw;
        if (!pattern) continue;
        let matched = false;
        try {
          matched = new RegExp(pattern).test(content);
        } catch {
          // Bad regex → never matches (parity with Python re.search → no match).
        }
        v[predKey("llm_said", pattern)] = matched;
      }
    }
    // response_words / response_chars — unparameterized Vars consumed by max_length.
    if (content) {
      v["response_words"] = content.trim().length === 0 ? 0 : content.trim().split(/\s+/).length;
      v["response_chars"] = content.length;
    }
    // ``args.segment`` convention: extended-thinking integrations tag
    // segment="thinking" / "answer" so contracts can scope content
    // checks to one segment only.
    if (event.args && typeof event.args.segment === "string" && event.args.segment) {
      v[predKey("segment", String(event.args.segment))] = true;
    }
  } else if (eventType === "context_update") {
    // Merge user-pushed facts into state.currentCtx so every
    // subsequent event sees them as ctx(k, v) atoms. Apply BEFORE
    // the ctx-emission loop so a contract at the same timestep as
    // the update already observes the new keys.
    if (event.args) {
      for (const [k, val] of Object.entries(event.args)) {
        if (k == null) continue;
        state.currentCtx[String(k)] = val == null ? "" : String(val);
      }
    }
  }

  // ── ctx(k, v) / ctx_value(k) — emit per current_ctx entry, every event ─
  // ctx(k, v) is the boolean atom (k==v). ctx_value(k) carries the raw
  // value so Term-based comparisons can read it without hard-coding the
  // expected value: e.g. Eq(CtxValue("approved_amount"),
  // ArgValue("issue_refund", "amount")).
  for (const [k, val] of Object.entries(state.currentCtx)) {
    v[predKey("ctx", k, val)] = true;
    v[predKey("ctx_value", k)] = val;
  }
  // ── ctx_matches(key, pattern) — regex against current_ctx[key] ──
  if (contentAtoms && contentAtoms["ctx_matches"]) {
    for (const raw of contentAtoms["ctx_matches"]) {
      const parts = raw.split("|");
      if (parts.length >= 2) {
        const key = parts[0];
        const pattern = parts.slice(1).join("|");
        const cur = state.currentCtx[key];
        let matched = false;
        if (cur != null) {
          try {
            matched = new RegExp(pattern).test(cur);
          } catch {
            matched = false;
          }
        }
        v[predKey("ctx_matches", key, pattern)] = matched;
      }
    }
  }

  // token_count(type) — cumulative token count (only meaningful for events with usage)
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

  // delegation_depth — incremented by event_type === "delegation"
  if (eventType === "delegation") {
    state.delegationDepth += 1;
  }
  v[predKey("delegation_depth")] = state.delegationDepth;

  // Emit count snapshots for all tracked tools
  for (const [t, cnt] of Object.entries(state.callCounts)) {
    v[predKey("count", t)] = cnt;
  }
  for (const [key, cnt] of Object.entries(state.callWithCounts)) {
    const idx = key.indexOf("|");
    if (idx > 0) {
      v[predKey("count_with", key.slice(0, idx), key.slice(idx + 1))] = cnt;
    }
  }

  // ── last_ts bookkeeping (fresh False→True transitions) ─────────
  // A predicate that was True last event and still True now is
  // "sustained" — we do NOT refresh its last_ts. Sustained covers
  // ctx atoms re-emitted while held in scope; this is what makes
  // time_since(ctx(approval.role, alice)) measure time since the
  // approval was granted, not the trivial 0 from re-emission.
  const trueNow = new Set<string>();
  for (const [key, val] of Object.entries(v)) {
    if (val === true) {
      trueNow.add(key);
      if (!state.trueAtPrev.has(key)) {
        state.lastTs[key] = state.now;
      }
    }
  }
  state.trueAtPrev = trueNow;

  // ── time atoms (emitted last so they see fresh state.now) ─────
  v["now"] = state.now;
  if (contentAtoms && contentAtoms["time_since"]) {
    for (const raw of contentAtoms["time_since"]) {
      if (!raw) continue;
      const targetKey = raw;
      let delta: number;
      if (targetKey in state.lastTs) {
        delta = state.now - state.lastTs[targetKey];
      } else {
        // Sentinel — predicate has never fired. "Very long ago" is
        // the right semantics for Le(time_since(P), N): if P never
        // happened, the constraint must evaluate False.
        delta = 1e18;
      }
      v[predKey("time_since", targetKey)] = delta;
    }
  }

  return v;
}

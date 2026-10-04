/**
 * One canonical spelling for a tool name, shared by contracts and grounding.
 *
 * Port of sponsio/formulas/tool_names.py.
 *
 * Predicate keys are map keys: ``called(issue_refund)`` and
 * ``called(Issue_Refund)`` are different entries, and a contract looks up
 * the spelling its author typed. So a rule written against ``issue_refund``
 * was silently inert against an event whose tool arrived as
 * ``Issue_Refund``, as ``"issue_refund "`` with a stray space, or as
 * ``mcp__finance__issue_refund`` (our own documented MCP wire format).
 * ``mustPrecede`` compiles to ``Or(order-holds, never-called)``, so a name
 * that never matches makes the second disjunct true and the contract
 * reports satisfied while the guarded action runs unchecked.
 *
 * Two functions, used on opposite sides of the same join:
 *
 * - {@link canonicalTool}: what a *contract* keys on. Contracts are
 *   authored once, so they collapse to a single canonical spelling.
 * - {@link toolAliases}: what an *event* answers to. A tool call is a fact
 *   and cannot be rewritten, so it is grounded under every spelling a
 *   contract might reasonably have used, canonical form included.
 *
 * Widening only ever makes more contracts apply to a given call, never
 * fewer.
 */

/**
 * Claude Code / MCP wire format: ``mcp__<server>__<tool>``. The server
 * segment has no ``__`` of its own; the tool segment may, so the split is
 * on the FIRST ``__`` after the prefix. ``[^\n]`` rather than ``.`` because
 * Python's ``.`` excludes only ``\n`` while JavaScript's also excludes
 * ``\r`` and U+2028/2029.
 */
export const MCP_TOOL_RE = /^mcp__([^_]+(?:_[^_]+)*)__([^\n]+)$/;

/**
 * Python ``str.strip()`` whitespace (``str.isspace``). Differs from
 * ``String.prototype.trim`` on U+001C-001F and U+0085 (Python strips,
 * JavaScript keeps) and U+FEFF (JavaScript strips, Python keeps).
 */
const PY_WS = "\\t\\n\\v\\f\\r\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const PY_STRIP_RE = new RegExp(`^[${PY_WS}]+|[${PY_WS}]+$`, "g");

function pyStrip(s: string): string {
  return s.replace(PY_STRIP_RE, "");
}

/**
 * Python ``str.casefold()``, per code point.
 *
 * Lower-casing alone misses the full case folds (``ß`` -> ``ss``,
 * ``ſ`` -> ``s``, final sigma -> ``σ``, ``µ`` -> ``μ``); folding through
 * the upper case catches those. Two families need explicit handling:
 * dotless ``ı`` has no fold in Python but upper-cases to ``I``, and
 * Unicode folds lower-case Cherokee *to* the upper-case letters. Code
 * points added in a Unicode version one runtime knows and the other does
 * not can still differ; tool names are ASCII in practice.
 */
function pyCasefold(s: string): string {
  let out = "";
  for (const ch of s) {
    const cp = ch.codePointAt(0) as number;
    if (cp < 0x80) {
      out += ch.toLowerCase();
    } else if (cp === 0x131) {
      out += ch;
    } else if (cp >= 0xab70 && cp <= 0xabbf) {
      out += String.fromCodePoint(cp - 0xab70 + 0x13a0);
    } else if (cp >= 0x13f8 && cp <= 0x13fd) {
      out += String.fromCodePoint(cp - 8);
    } else if (cp >= 0x13a0 && cp <= 0x13f5) {
      out += ch;
    } else {
      const lower = ch.toLowerCase();
      const upper = lower.toUpperCase();
      out += upper !== lower ? upper.toLowerCase() : lower;
    }
  }
  return out;
}

/** ``mcp__finance__issue_refund`` -> ``issue_refund``; else ``null``. */
function stripMcp(name: string): string | null {
  const m = MCP_TOOL_RE.exec(name);
  return m ? m[2] : null;
}

/**
 * The single spelling a contract keys on.
 *
 * Strips surrounding whitespace and case-folds. The MCP prefix is *not*
 * stripped here: a contract that deliberately names
 * ``mcp__finance__issue_refund`` means that server's tool and should not
 * widen to every tool of that name. Grounding supplies the bare-name
 * alias so the reverse direction still joins.
 */
export function canonicalTool(tool: string): string {
  return pyCasefold(pyStrip(String(tool)));
}

/**
 * Every spelling an event's tool call should answer to.
 *
 * Ordered, duplicate-free, and always contains the raw name first so
 * existing traces and predicate keys keep working unchanged.
 */
export function toolAliases(tool: string): string[] {
  const raw = String(tool);
  const out = [raw];
  const stripped = pyStrip(raw);
  out.push(stripped, pyCasefold(stripped));
  const bare = stripMcp(stripped);
  if (bare) out.push(bare, pyCasefold(bare));
  return [...new Set(out)].filter((k) => k);
}

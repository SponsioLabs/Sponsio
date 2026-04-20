/**
 * NL → Formula parser (rule-based keyword matching).
 *
 * Simplified port of sponsio/generation/nl_to_contract.py.
 * Handles common NL patterns like:
 *   "tool `A` must precede `B`"
 *   "tool `X` at most 3 times"
 *   "tools `A` and `B` are mutually exclusive"
 */

import type { DetFormula } from "./patterns.js";
import {
  mustPrecede,
  alwaysFollowedBy,
  rateLimit,
  idempotent,
  mutualExclusion,
  noReversal,
  argBlacklist,
  cooldown,
} from "./patterns.js";

/** Extract backtick-wrapped tool names from NL text. */
function extractTools(text: string): string[] {
  const matches = text.match(/`([^`]+)`/g);
  if (!matches) return [];
  return matches.map((m) => m.replace(/`/g, ""));
}

/** Extract a number from text. */
function extractNumber(text: string): number | null {
  const m = text.match(/(\d+)/);
  return m ? parseInt(m[1], 10) : null;
}

interface KeywordRule {
  patterns: RegExp[];
  patternName: string;
  minArgs: number;
}

const KEYWORD_RULES: KeywordRule[] = [
  // Rate limit (before idempotent — "at most N times")
  {
    patterns: [/at most.*times/, /maximum.*invocations/, /limit.*calls/, /no more than.*times/],
    patternName: "rate_limit",
    minArgs: 1,
  },
  // Idempotent
  {
    patterns: [/idempotent/, /at most once/, /only (?:once|call(?:ed)? once)/, /single invocation/],
    patternName: "idempotent",
    minArgs: 1,
  },
  // Mutual exclusion
  {
    patterns: [/mutually exclusive/, /exactly one of/, /either.*or.*not both/, /only one of/],
    patternName: "mutual_exclusion",
    minArgs: 2,
  },
  // Always followed by
  {
    patterns: [/(?:must be |always )?followed by/, /must eventually follow/],
    patternName: "always_followed_by",
    minArgs: 2,
  },
  // No reversal
  {
    patterns: [/cannot.*after\s+approv/, /no reversal/, /cannot\s+deny\s+after/, /must\s+not.*after/],
    patternName: "no_reversal",
    minArgs: 2,
  },
  // Cooldown
  {
    patterns: [/cooldown/, /minimum\s+\d+\s+steps?\s+between/],
    patternName: "cooldown",
    minArgs: 1,
  },
  // Must precede (last — most general, requires backtick context)
  {
    patterns: [/precede/, /`[^`]+`\s+(?:must\s+)?before\s+`/, /before\s+`/, /is\s+required\s+before/],
    patternName: "must_precede",
    minArgs: 2,
  },
];

export function parseNl(text: string): DetFormula | null {
  const lower = text.toLowerCase();
  const tools = extractTools(text);

  for (const rule of KEYWORD_RULES) {
    const matched = rule.patterns.some((p) => p.test(lower));
    if (!matched) continue;
    if (tools.length < rule.minArgs) continue;

    switch (rule.patternName) {
      case "must_precede":
        return mustPrecede(tools[0], tools[1]);
      case "always_followed_by":
        return alwaysFollowedBy(tools[0], tools[1]);
      case "rate_limit": {
        const n = extractNumber(text);
        if (n == null) continue;
        return rateLimit(tools[0], n);
      }
      case "idempotent":
        return idempotent(tools[0]);
      case "mutual_exclusion":
        return mutualExclusion(tools[0], tools[1]);
      case "no_reversal":
        return noReversal(tools[0], tools[1]);
      case "cooldown": {
        const n = extractNumber(text);
        if (n == null) continue;
        return cooldown(tools[0], n);
      }
      default:
        continue;
    }
  }

  return null;
}

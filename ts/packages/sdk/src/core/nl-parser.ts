/**
 * NL → Formula parser (rule-based keyword matching).
 *
 * Simplified port of sponsio/generation/dsl_to_contract.py.
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
  deadline,
  maxLength,
  noPii,
  noKeywords,
  scopeLimit,
  requiresPermission,
  segregationOfDuty,
} from "./patterns.js";
import { Atom } from "./formula.js";

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
  // Deadline (before rate_limit so "within N steps" doesn't get swallowed by
  // any "N times"-style rule). Mirrors the Python ``dsl_to_contract`` regex set.
  {
    patterns: [
      /within\s+\d+\s+steps?\s+(?:of|after)/,
      /deadline\s+(?:of\s+)?\d+\s+steps?/,
      /must.*within\s+\d+\s+steps?/,
    ],
    patternName: "deadline",
    minArgs: 2,
  },
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
    patterns: [
      /cannot.*after\s+approv/,
      /no reversal/,
      /cannot\s+deny\s+after/,
      /(?:never|cannot|must\s+not|don'?t|do\s+not)\s+(?:call\s+)?.*after\s+(?:calling\s+)?/,
      /^\s*after\s+.*\b(?:never|cannot|must\s+not|don'?t|do\s+not)\b/,
      /must\s+not\s+follow/,
      /not\s+allowed\s+after/,
    ],
    patternName: "no_reversal",
    minArgs: 2,
  },
  // Cooldown
  {
    patterns: [/cooldown/, /minimum\s+\d+\s+steps?\s+between/],
    patternName: "cooldown",
    minArgs: 1,
  },
  // Scope limit — confinement to a path root. Python's NL parser has had
  // this since the pattern shipped; TypeScript did not, so a shared
  // rulebook that wrote it in English enforced in one runtime and, with
  // one console line, nowhere in the other.
  {
    patterns: [
      /restricted?\s+to\s+(?:paths?|`?\/)/,
      /restrict\s+file\s+access\s+to/,
      /confined?\s+to\s+(?:paths?|`?\/)/,
      /must\s+(?:stay|remain)\s+(?:with)?in\s+`?\//,
    ],
    patternName: "scope_limit",
    minArgs: 1,
  },
  // Argument blacklist — a shape that must never be sent. Phrasings
  // copied from the Python parser so one rulebook reads the same in both.
  {
    patterns: [
      /arg(?:ument)?s?\s+(?:must\s+)?not\s+contain/,
      /(?:command|input|param|query|body)\s+must\s+not\s+contain/,
      /blacklist/,
      /ban\s+(?:patterns?|commands?)\s+in/,
    ],
    patternName: "arg_blacklist",
    minArgs: 2,
  },
  // Requires permission
  {
    patterns: [
      /requires?\s+(?:\w+\s+)?permission/,
      /needs?\s+permission/,
      /must\s+have\s+permission/,
      /requires?\s+admin\b/,
    ],
    patternName: "requires_permission",
    minArgs: 2,
  },
  // Segregation of duty
  {
    patterns: [
      /segregation of dut/,
      /separation of dut/,
      /different agent/,
      /must be (?:done|performed) by different/,
      /same agent.*cannot.*both/,
    ],
    patternName: "segregation_of_duty",
    minArgs: 2,
  },
  // Must precede (last — most general, requires backtick context)
  {
    patterns: [/precede/, /`[^`]+`\s+(?:must\s+)?before\s+`/, /before\s+`/, /is\s+required\s+before/],
    patternName: "must_precede",
    minArgs: 2,
  },
];

/**
 * Recognise bare "called \`X\`" / "calls \`X\`" / "\`X\` was called"
 * phrasings — Python's parser treats these as standalone ``called(X)``
 * atoms, which is what the ``contract().assume("called \`X\`")``
 * builder snippet leans on. Keeping this as a fallback path (after
 * the richer pattern rules) means a phrase like "tool \`A\` must
 * precede \`B\`" still binds to ``must_precede`` first.
 */
function parseBareCalledAtom(text: string, tools: string[]): DetFormula | null {
  if (tools.length !== 1) return null;
  const lower = text.toLowerCase();
  if (
    /\bcalled\s+`[^`]+`/.test(lower) ||
    /\bcalls\s+`[^`]+`/.test(lower) ||
    /`[^`]+`\s+(?:was|is)\s+called/.test(lower)
  ) {
    return {
      formula: new Atom("called", [tools[0]]),
      desc: `called(${tools[0]})`,
      patternName: "called",
      liveness: false,
    };
  }
  return null;
}

// Response-content NL patterns — matched BEFORE the generic keyword
// rules so length / PII / no-keyword constraints route to the
// response-content det pipeline. Mirrors Python's
// ``_try_response_content_patterns``.
const LENGTH_PATTERN = /(?:response|output)\s+(?:must\s+be\s+)?(?:under|at\s+most|no\s+more\s+than|fewer\s+than|max(?:imum)?)\s+(\d+)\s+(words?|characters?|chars?)/i;
const NO_PII_PATTERN = /(?:response|output).*(?:must|should)\s+not\s+contain\s+(?:any\s+)?(pii|personal\s+info(?:rmation)?|ssns?|credit[\s-]?cards?|emails?(?:\s+address(?:es)?)?|phones?(?:\s+numbers?)?)/i;
const NO_KEYWORD_PATTERN = /(?:response|output)\s+(?:must|should)\s+not\s+(?:contain|include|mention)\s+(?:the\s+)?(?:words?|keywords?|terms?|phrase)\s+[`"']?([^`"']+)[`"']?/i;

const PII_KEYWORD_TO_FIELDS: Record<string, string[]> = {
  ssn: ["ssn"],
  ssns: ["ssn"],
  "credit card": ["credit_card"],
  "credit cards": ["credit_card"],
  "credit-card": ["credit_card"],
  "credit-cards": ["credit_card"],
  email: ["email"],
  emails: ["email"],
  "email address": ["email"],
  "email addresses": ["email"],
  phone: ["phone"],
  phones: ["phone"],
  "phone number": ["phone"],
  "phone numbers": ["phone"],
};

function tryResponseContent(text: string): DetFormula | null {
  // max_length
  let m = text.match(LENGTH_PATTERN);
  if (m) {
    const n = parseInt(m[1], 10);
    const unit = m[2].toLowerCase();
    try {
      if (unit.includes("char")) return maxLength({ maxChars: n, desc: text });
      return maxLength({ maxWords: n, desc: text });
    } catch {
      return null;
    }
  }
  // no_pii — narrow to specific category if mentioned, else full union.
  m = text.match(NO_PII_PATTERN);
  if (m) {
    const raw = m[1].toLowerCase().replace(/-/g, " ").replace(/\s+/g, " ").trim();
    const fields = PII_KEYWORD_TO_FIELDS[raw];
    try {
      return noPii(fields);
    } catch {
      return null;
    }
  }
  // no_keywords
  m = text.match(NO_KEYWORD_PATTERN);
  if (m) {
    const raw = m[1].trim().replace(/\.$/, "");
    const words = raw.split(/[,\s]+/).map((w) => w.trim()).filter((w) => w.length > 0);
    if (words.length === 0) return null;
    try {
      return noKeywords(words);
    } catch {
      return null;
    }
  }
  return null;
}


const NEGATION_RE = /\b(?:never|cannot|can\s+not|must\s+not|don'?t|do\s+not|no)\b/;

/**
 * True for "never call A after B", where B is the commitment.
 *
 * Decided by position rather than by phrase: the phrase list this
 * supplements matches "never after" and not "never call `x` after", and
 * every new way of saying it would need another entry. Parity with
 * Python's ``_forbidden_action_comes_first``.
 */
function forbiddenActionComesFirst(
  lower: string,
  first: string,
  second: string,
): boolean {
  const iFirst = lower.indexOf(first.toLowerCase());
  if (iFirst < 0) return false;
  const iSecond = lower.indexOf(second.toLowerCase(), iFirst + 1);
  if (iSecond < 0 || iSecond <= iFirst) return false;
  // "after" has to sit between the two actions: in "after `b`, never call
  // `a`" the commitment already comes first and must not be swapped.
  if (!/\bafter\b/.test(lower.slice(iFirst + first.length, iSecond))) return false;
  return NEGATION_RE.test(lower.slice(0, iFirst));
}

export function parseNl(text: string): DetFormula | null {
  // P2 response-content patterns first — keep them ahead of the
  // generic keyword cascade so "response must not contain emails"
  // doesn't get swallowed by something more general.
  const respFormula = tryResponseContent(text);
  if (respFormula) return respFormula;

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
      case "arg_blacklist": {
        // "tool `bash` command must not contain `rm -rf`" — the first
        // backticked name is the tool, the rest are the banned shapes.
        // The field comes from the cue word when there is one, since a
        // blacklist on the wrong field silently checks nothing.
        const field =
          (text.match(/\b(command|query|input|param|body|path|url|text)\b/) || [])[1] ??
          "*";
        if (tools.length < 2) continue;
        return argBlacklist(tools[0], field, tools.slice(1));
      }
      case "requires_permission":
        return requiresPermission(tools[0], tools[1]);
      case "segregation_of_duty":
        return segregationOfDuty(tools[0], tools[1]);
      case "scope_limit": {
        // The roots are the path-shaped arguments; the tool is whatever
        // else was named. "restrict file access to `/workspace`" names no
        // tool, so the rule applies to any call carrying a path.
        const roots = tools.filter((t) => t.startsWith("/"));
        const named = tools.filter((t) => !t.startsWith("/"));
        const bare = text.match(/(?:^|\s)(\/[^\s`'",;]+)/g) ?? [];
        const paths = roots.length
          ? roots
          : bare.map((b) => b.trim()).filter(Boolean);
        if (!paths.length) continue;
        return scopeLimit(named[0] ?? ".*", paths);
      }
      case "no_reversal": {
        // ``noReversal(commitment, contradiction)`` takes the committing
        // action first, and English puts it last whenever the sentence
        // opens with the prohibition: "never call `delete` after
        // `restore`" means restore commits. Reading the names in the
        // order they appear built the opposite rule — and this parser
        // rewrites the desc from the args, so the console showed a rule
        // the user never wrote. Parity with Python's
        // ``_forbidden_action_comes_first``.
        const swap =
          /must not follow|should not follow|not allowed after|forbidden after|prohibited after|never after/.test(
            lower,
          ) || forbiddenActionComesFirst(lower, tools[0], tools[1]);
        return swap
          ? noReversal(tools[1], tools[0])
          : noReversal(tools[0], tools[1]);
      }
      case "cooldown": {
        const n = extractNumber(text);
        if (n == null) continue;
        return cooldown(tools[0], n);
      }
      case "deadline": {
        const n = extractNumber(text);
        if (n == null) continue;
        // Parity with Python NL parser: ``deadline(actions[0], actions[1], n)``,
        // i.e. tools[0] is the trigger and tools[1] is the action that must
        // occur within ``n`` steps. NL phrasings to use: "after `X`, `Y` must
        // occur within N steps".
        return deadline(tools[0], tools[1], n);
      }
      default:
        continue;
    }
  }

  // Fallback: single-atom phrasings used in A/G contract assumptions.
  return parseBareCalledAtom(text, tools);
}

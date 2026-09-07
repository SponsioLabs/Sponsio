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
  argAllowlist,
  argBlacklist,
  approvalFreshness,
  auditAfter,
  backupBeforeDestructive,
  boundedRetry,
  cooldown,
  dataIntact,
  deadline,
  dryRunBeforeCommit,
  duplicateCallLimit,
  maxLength,
  mustConfirm,
  noDataLeak,
  noPii,
  noKeywords,
  sanitizedBeforeSink,
  scopeLimit,
  requiresPermission,
  segregationOfDuty,
} from "./patterns.js";
import { Atom } from "./formula.js";

// Bare snake_case identifiers. At least one underscore, so ordinary
// English words are not read as tool names.
const BARE_SNAKE = /\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b/g;

// Phrases that look snake_case when squished and are not tools.
const BARE_STOP = new Set([
  "at_most", "at_least", "no_more", "per_session", "per_call",
  "must_not", "should_not", "same_session", "each_other",
]);

const CUE_AND =
  /(?:^|\s)(?:tool|tools|call(?:ing)?|run(?:ning)?|execute|invoke)\s+([a-zA-Z][a-zA-Z0-9_]*)\s+and\s+([a-zA-Z][a-zA-Z0-9_]*)/i;
const CUE_PHRASE =
  /(?:^|\s)(?:tool|tools|action|actions|call(?:ing)?|run(?:ning)?|execute|invoke|use|using)\s+([a-zA-Z][a-zA-Z0-9_]*)/gi;
const TOOL_BEFORE_FIELD =
  /([a-zA-Z][a-zA-Z0-9_]*)\s+(?:command|args?|arguments?|input|params?)/i;

// English words that must never be read as a tool name.
const STOP_WORDS = new Set(["a","admin","after","agent","all","allowed","also","always","an","and","any","are","as","at","be","been","before","being","between","both","but","by","called","can","confirmed","could","data","did","different","do","does","each","every","few","file","first","for","from","get","got","had","has","have","he","her","him","if","in","is","it","its","just","last","let","made","make","may","me","more","most","must","my","never","new","no","nor","not","of","old","on","once","only","or","other","our","permission","required","restricted","same","set","shall","she","should","so","some","than","that","the","them","then","these","they","this","those","to","too","us","user","very","was","we","were","will","with","within","would","you","your"]);

/**
 * The tool names a sentence mentions. Parity with Python's
 * ``_extract_actions``, and the reason this side used to drop rules
 * Python accepted: it read backticks and nothing else, so
 * "check_policy must precede issue_refund" named no tools here and
 * parsed to null while Python armed it.
 *
 * One method wins, in this order: backticks, then quotes, then bare
 * snake_case, then the cue phrases. Mixing them would let a sentence
 * that quotes one tool pick up an English word as the other.
 */
function extractTools(text: string): string[] {
  const ticked = [...text.matchAll(/`([^`]+)`/g)].map((m) => m[1]);
  if (ticked.length) return ticked;
  const quoted = [...text.matchAll(/["']([^"']+)["']/g)].map((m) => m[1]);
  if (quoted.length) return quoted;

  const bare = [...text.matchAll(BARE_SNAKE)]
    .map((m) => m[1])
    .filter((w) => !BARE_STOP.has(w));
  if (bare.length) return bare;

  const pair = text.match(CUE_AND);
  if (pair) {
    const both = [pair[1], pair[2]].filter((w) => !STOP_WORDS.has(w.toLowerCase()));
    if (both.length >= 2) return both;
  }

  const cued = [...text.matchAll(CUE_PHRASE)]
    .map((m) => m[1])
    .filter((w) => !STOP_WORDS.has(w.toLowerCase()));
  if (cued.length) return cued;

  const beforeField = text.match(TOOL_BEFORE_FIELD);
  if (beforeField && !STOP_WORDS.has(beforeField[1].toLowerCase())) {
    return [beforeField[1]];
  }
  return [];
}

// Small numbers people write as words. Parity with Python's
// ``_WORD_NUMBERS``: "at most once" has to mean 1, or the rule is
// dropped rather than armed.
const WORD_NUMBERS: Record<string, number> = {
  one: 1, once: 1, two: 2, twice: 2, three: 3, four: 4, five: 5,
  six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
};

/** A digit, or a small number spelled out. Parity with ``_parse_number``. */
function parseNumber(text: string): number | null {
  const m = text.match(/(\d+)/);
  if (m) return parseInt(m[1], 10);
  const lower = text.toLowerCase();
  for (const [word, n] of Object.entries(WORD_NUMBERS)) {
    if (lower.includes(word)) return n;
  }
  return null;
}

/** Parity with ``_parse_rate_limit_count``, including the word forms. */
function parseRateLimitCount(text: string): number | null {
  const lower = text.toLowerCase();
  let m =
    lower.match(/at most (\d+)/) ??
    lower.match(/(\d+)\s*(?:times|invocations|calls|per)/) ??
    lower.match(/limit.*?(\d+)/) ??
    lower.match(/(?:no more than|up to|maximum|max|more than)\s+(\d+)/);
  if (m) return parseInt(m[1], 10);
  const words = Object.keys(WORD_NUMBERS).join("|");
  m = lower.match(
    new RegExp(`(?:more than|at most|no more than|up to)\\s+(${words})\\b`),
  );
  if (m) return WORD_NUMBERS[m[1]];
  for (const [word, n] of Object.entries(WORD_NUMBERS)) {
    if (lower.includes(word) && new RegExp(`${word}\\s+(?:times|calls|per)`).test(lower)) {
      return n;
    }
  }
  return null;
}

/** Parity with ``_parse_retry_count``. */
function parseRetryCount(text: string): number | null {
  const lower = text.toLowerCase();
  const m =
    lower.match(/at most (\d+)\s*retr/) ??
    lower.match(/(\d+)\s*retr/) ??
    lower.match(/max(?:imum)?\s*(\d+)/);
  return m ? parseInt(m[1], 10) : null;
}

/** Parity with ``_parse_step_count``. */
function parseStepCount(text: string): number | null {
  const lower = text.toLowerCase();
  const m =
    lower.match(/(\d+)\s*steps?/) ?? lower.match(/cooldown\s+(?:of\s+)?(\d+)/);
  return m ? parseInt(m[1], 10) : null;
}

const BACKTICKED = /`([^`]+)`/g;
const QUOTED = /["']([^"']+)["']/g;

function allMatches(re: RegExp, text: string): string[] {
  return [...text.matchAll(new RegExp(re.source, "g"))].map((m) => m[1]);
}

/**
 * The shapes a sentence forbids. Parity with
 * ``_extract_blacklist_patterns``: read the tail after the verb, prefer
 * whatever is quoted there, and otherwise split the tail on "or" / ","
 * / "and" so a bare "must not contain rm -rf or sudo" still bans two
 * things rather than one long string.
 */
function extractDelimitedTail(text: string): string[] {
  const m = text.match(/(?:contain|include|allow|permit)\s+(.+)/i);
  if (!m) return [];
  const tail = m[1];
  const items = allMatches(BACKTICKED, tail);
  if (items.length) return items;
  const quoted = allMatches(QUOTED, tail);
  if (quoted.length) return quoted;
  return tail
    .split(/\s+or\s+|\s*,\s*|\s+and\s+/)
    .map((x) => x.trim().replace(/\.$/, ""))
    .filter(Boolean);
}

/** Parity with ``_extract_allowlist_patterns``. */
function extractAllowlistPatterns(text: string): string[] {
  const m = text.match(/(?:one\s+of|in|allow(?:list)?|whitelist|permit)\s+(.+)/i);
  if (!m) return [];
  const tail = m[1];
  const items = allMatches(BACKTICKED, tail);
  if (items.length) return items;
  const quoted = allMatches(QUOTED, tail);
  if (quoted.length) return quoted;
  return tail
    .split(/\s+or\s+|\s*,\s*|\s+and\s+/)
    .map((x) => x.trim().replace(/\.$/, ""))
    .filter(Boolean);
}

interface KeywordRule {
  patterns: RegExp[];
  patternName: string;
  minArgs: number;
}

// Generated from Python's ``_KEYWORD_RULES`` by scripts/gen_ts_rules.py,
// because a table of 155 regexes copied by hand drifts, and it drifted:
// this side carried 46 of them and silently returned null for the rest,
// so a rulebook that armed 23 patterns in Python armed 12 here and said
// nothing about the other 11.
//
// ``minArgs`` is this side's own: Python has no pre-gate and lets each
// branch check its own arity, so the numbers here are what each handler
// below actually needs, not the ones in Python's table.
const KEYWORD_RULES: KeywordRule[] = [
  {
    patterns: [
      /arg(?:ument)?s?\s+(?:must\s+be|must\s+match)\s+(?:one\s+of|in)/,
      /(?:command|input|param|recipient|to|host|domain|url)\s+must\s+be\s+(?:one\s+of|in)/,
      /allowlist/,
      /whitelist/,
      /only\s+(?:allow|permit)\s+(?:the\s+)?(?:value|values|recipient|recipients|host|hosts|domain|domains)/,
      /restrict\s+(?:.*\s+)?(?:to|in)\s+(?:the\s+)?(?:allowed|allow-listed|whitelisted)\s+(?:value|values|set|list)/,
    ],
    patternName: "arg_allowlist",
    minArgs: 1,
  },
  {
    patterns: [
      /arg(?:ument)?s?\s+(?:must\s+)?not\s+contain/,
      /\b(?:arg|argument|field|param(?:eter)?)s?\s+[`"'][^`"']+[`"']\s+(?:must\s+)?not\s+contain/,
      /(?:command|input|param|query|body|text|content|url|path)\s+must\s+not\s+contain/,
      /blacklist/,
      /must\s+not\s+contain\s+(?:.*(?:rm\s*-rf|sudo|DROP|eval))/,
      /forbid.*(?:in\s+(?:arguments?|params?|input))/,
      /ban\s+(?:patterns?|commands?)\s+in/,
    ],
    patternName: "arg_blacklist",
    minArgs: 1,
  },
  {
    patterns: [
      /restrict\s+(?:file\s+)?(?:access|operations?)\s+to\s+(?:`|\/)/,
      /scope\s*limit/,
      /only\s+(?:access|read|write|operate)\s+(?:files?\s+)?(?:in|within|under)\s+(?:`|\/)/,
      /file\s+(?:operations?\s+)?restricted\s+to\s+(?:`|\/)/,
      /(?:paths?|files?|directories?)\s+(?:must\s+be\s+)?(?:within|under)\s+(?:`|\/)/,
      /confine.*to\s+(?:\/|`)/,
      /restricted\s+to\s+(?:`?\/)/,
    ],
    patternName: "scope_limit",
    minArgs: 0,
  },
  {
    patterns: [
      /data\s+(?:must\s+)?remain\s+(?:un(?:modified|changed|altered)|intact)/,
      /(?:must\s+)?(?:only|exclusively)\s+(?:read|operate\s+on)\s+(?:from\s+)?(?:original|unmodified)/,
      /data\s*intact/,
      /read[- ]?only\s+(?:from|on)\b/,
    ],
    patternName: "data_intact",
    minArgs: 1,
  },
  {
    patterns: [
      /dry[- ]?run.*before/,
      /plan.*before.*(?:apply|commit|deploy|execute)/,
      /(?:apply|commit|deploy|execute).*requires?.*dry[- ]?run/,
    ],
    patternName: "dry_run_before_commit",
    minArgs: 2,
  },
  {
    patterns: [
      /backup.*before/,
      /snapshot.*before/,
      /(?:delete|drop|destroy|destructive).*requires?.*(?:backup|snapshot)/,
    ],
    patternName: "backup_before_destructive",
    minArgs: 2,
  },
  {
    patterns: [
      /audit.*after/,
      /log.*after/,
      /(?:must|should).*be\s+(?:audited|logged)/,
      /(?:audit|log)\s+(?:required|needed)/,
    ],
    patternName: "audit_after",
    minArgs: 1,
  },
  {
    patterns: [
      /fresh\s+approval/,
      /approval.*(?:within|expires?|expire)/,
      /(?:approval|authorization).*fresh/,
      /(?:approve|approval).*(?:\d+)\s+steps?/,
    ],
    patternName: "approval_freshness",
    minArgs: 1,
  },
  {
    patterns: [
      /sanitize.*before/,
      /saniti[sz]ed.*before/,
      /(?:untrusted|external|web|email).*sanitize.*(?:before|then)/,
      /(?:source|input).*sanitizer.*sink/,
    ],
    patternName: "sanitized_before_sink",
    minArgs: 3,
  },
  {
    patterns: [
      /duplicate\s+call/,
      /same.*request.*at most/,
      /same\s+(?:tool|api|request|args?).*at most/,
      /repeat(?:ed)?\s+(?:same\s+)?(?:call|request)/,
      /no duplicate/,
      /never repeat/,
    ],
    patternName: "duplicate_call_limit",
    minArgs: 2,
  },
  {
    patterns: [
      /at most.*retr/,
      /retry.*at most/,
      /bounded retry/,
      /max.*retries/,
      /maximum.*retries/,
      /no more than.*retr/,
      /limit.*retries?\s+to\b/,
    ],
    patternName: "bounded_retry",
    minArgs: 1,
  },
  {
    patterns: [
      /rate\s*limit/,
      /at most.*times/,
      /maximum.*invocations/,
      /limit.*(?:calls|invocations|uses)\b/,
      /must not be called more than/,
      /(?:at most|no more than|up to|maximum|max)\s+(\d+)\s+(?:per|times|calls)/,
      /limit.*to\s+(\d+)\s+(?:per|times|calls)/,
    ],
    patternName: "rate_limit",
    minArgs: 1,
  },
  {
    patterns: [
      /idempotent/,
      /at most once/,
      /only (?:once|run once|call(?:ed)? once)/,
      /called? once\b/,
      /should only (?:run|be called|execute) once/,
      /single invocation/,
      /no repeated calls?\b/,
    ],
    patternName: "idempotent",
    minArgs: 1,
  },
  {
    patterns: [
      /mutually exclusive/,
      /exactly one of/,
      /either.*or.*not both/,
      /cannot (?:both|call both)/,
      /only one of/,
      /at most one of/,
    ],
    patternName: "mutual_exclusion",
    minArgs: 2,
  },
  {
    patterns: [
      /never together/,
      /never\s+(?:call(?:ing)?\s+)?[^,]*\band\b[^,]*\btogether\b/,
      /never both/,
      /not at the same time/,
      /never co-occur/,
      /must never.*called together/,
      /never be called together/,
      /not.*(?:in|within|during)\s+(?:the\s+)?same\s+session/,
      /do not call.*(?:and|,).*(?:in|within|during)\s+(?:the\s+)?same/,
    ],
    patternName: "mutual_exclusion",
    minArgs: 2,
  },
  {
    patterns: [
      /cooldown/,
      /cool\s*down\s+(?:of|between|period\s+of)\s+\d+/,
      /minimum\s+\d+\s+steps?\s+between/,
      /at least\s+\d+\s+steps?\s+between/,
      /wait\s+\d+\s+steps?\s+between/,
      /gap of\s+\d+\s+steps?/,
      /interval\s+(?:of\s+)?\d+\s+steps?/,
    ],
    patternName: "cooldown",
    minArgs: 1,
  },
  {
    patterns: [
      /segregation of dut/,
      /separation of dut/,
      /same agent.*cannot.*both/,
      /different agent/,
      /cannot do both/,
      /must be (?:done|performed) by different/,
      /two[- ]person\s+rule/,
      /dual\s+control/,
    ],
    patternName: "segregation_of_duty",
    minArgs: 2,
  },
  {
    patterns: [
      /within\s+\d+\s+steps?\s+(?:of|after)/,
      /deadline\s+(?:of\s+)?\d+\s+steps?/,
      /must.*within\s+\d+\s+steps?/,
      /at most\s+\d+\s+steps?\s+after/,
    ],
    patternName: "deadline",
    minArgs: 2,
  },
  {
    patterns: [
      /must be confirmed/,
      /confirm(?:ation)?\s+(?:before|required|needed)/,
      /requires?\s+confirmation/,
      /must confirm before/,
      /(?:needs?|requires?)\s+(?:user\s+)?(?:approval|consent)\s+before/,
      /without\s+confirmation/,
      /never\s+call.*without\s+confirm/,
    ],
    patternName: "must_confirm",
    minArgs: 1,
  },
  {
    patterns: [
      /cannot.*after\s+approv/,
      /no reversal/,
      /never\s+reverse/,
      /cannot\s+deny\s+after/,
      /cannot\s+reject\s+after/,
      /cannot\s+contradict/,
      /cannot\s+be\s+reversed/,
      /(?:never|cannot|must\s+not|don'?t|do\s+not)\s+(?:call\s+)?.*after\s+(?:calling\s+)?/,
      /^\s*after\s+.*\b(?:never|cannot|must\s+not|don'?t|do\s+not)\b/,
      /forbidden\s+after/,
      /prohibited\s+after/,
      /must\s+not\s+follow/,
      /must\s+not.*after/,
      /should\s+not\s+follow/,
      /not\s+allowed\s+after/,
      /once.*(?:cannot|must\s+not|never)/,
      /irreversible/,
    ],
    patternName: "no_reversal",
    minArgs: 2,
  },
  {
    patterns: [
      /requires?\s+(?:\w+\s+)?permission/,
      /needs?\s+permission/,
      /must\s+have\s+permission/,
      /(?:requires?|needs?)\s+(?:\w+\s+)?(?:authorization|auth)\b/,
      /requires?\s+admin\b/,
      /(?:only\s+)?(?:authorized|permitted)\s+(?:users?|agents?|roles?)\s+(?:can|may)/,
      /require\s+\w+\s+(?:permission|role|access)\s+to\b/,
    ],
    patternName: "requires_permission",
    minArgs: 2,
  },
  {
    patterns: [
      /no data leak/,
      /data must not (?:flow|leak|be\s+sent)/,
      /no leak/,
      /(?:must\s+)?not\s+(?:send|transmit|expose|share).*(?:to\s+external|outside)/,
      /protect.*from\s+(?:leaking|exposure)/,
    ],
    patternName: "no_data_leak",
    minArgs: 2,
  },
  {
    patterns: [
      /(?:must\s+be\s+|always\s+)?followed\s+by/,
      /must eventually follow/,
      /eventually.*after/,
      /after\s+(?:calling\s+)?.*(?:always|must)\s+(?:call\s+|run\s+)?/,
      /(?:always|must)\s+(?:call|run|execute)\s+.*after/,
      /whenever.*(?:is\s+)?called.*(?:must|should|always)/,
      /(?:should|must)\s+(?:always\s+)?come\s+after/,
    ],
    patternName: "always_followed_by",
    minArgs: 2,
  },
  {
    patterns: [
      /precede/,
      /prior to\s+`/,
      /`[^`]+`\s+(?:must\s+)?(?:be\s+)?(?:called\s+|run\s+|executed\s+)?before\s+`/,
      /before\s+(?:calling\s+)?`/,
      /required\s+before\s+`/,
      /is\s+(?:a\s+)?prerequisite\s+for\b/,
      /(?:always|must)\s+run\s+`[^`]+`\s+first/,
      /is\s+required\s+before/,
      /needs?\s+to\s+(?:be\s+)?(?:called|run)\s+before/,
      /must\s+(?:be\s+)?(?:called|run|executed)\s+first/,
    ],
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
      patternName: "trigger_called",
      // Empty, as Python leaves it. The tool is in the atom; putting it
      // here too would make the same rule group differently on the two
      // sides, which is the one thing args exists to prevent.
      args: [],
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

/**
 * The longest rule sentence this will look at, matching Python's
 * ``_MAX_NL_LINE_LEN``.
 *
 * Several of the regexes below are quadratic on adversarial input: a
 * long run of digits before ``\s*steps?`` makes the engine retry from
 * every position. Python has always truncated and this side never did,
 * so the same rulebook that cost Python milliseconds could hang a
 * TypeScript agent — and on a platform whose customers supply their own
 * overlay rules, that string is not fully trusted.
 *
 * A sentence longer than this is not a rule anybody wrote.
 */
const MAX_RULE_CHARS = 10_000;

export function parseNl(input: string): DetFormula | null {
  const text = input.length > MAX_RULE_CHARS ? input.slice(0, MAX_RULE_CHARS) : input;
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
      case "always_followed_by": {
        // English puts the trigger second in two shapes, and reading the
        // names in the order they appear built the rule backwards:
        // "`b` should come after `a`" and "always call `b` after `a`"
        // both mean a triggers b. Parity with Python.
        const swap =
          /come\s+after|should\s+(?:always\s+)?(?:come|happen)\s+after/.test(lower) ||
          /(?:always|must)\s+(?:call|run)\s+\S+\s+after\b/.test(lower);
        return swap
          ? alwaysFollowedBy(tools[1], tools[0])
          : alwaysFollowedBy(tools[0], tools[1]);
      }
      case "rate_limit": {
        const n = parseRateLimitCount(text);
        if (n == null) continue;
        return rateLimit(tools[0], n);
      }
      case "idempotent":
        return idempotent(tools[0]);
      case "mutual_exclusion":
        return mutualExclusion(tools[0], tools[1]);
      case "arg_blacklist": {
        // Three ways a sentence names the field, in the order they win.
        // Parity with Python, and the reason this side got it wrong:
        // taking the second backticked name produced
        // arg_field_has('bash', 'rm -rf', 'rm -rf'), a rule watching an
        // argument nobody sends, which parses and arms and never fires.
        //
        //   1. marked:  "tool `bash` arg `command` must not ..."
        //   2. a cue word naming a real field
        //   3. a second name that is not itself one of the banned shapes
        const forbidden = extractDelimitedTail(text);
        if (!forbidden.length) continue;
        const tool = tools[0];
        const marked = text.match(
          /\b(?:arg|argument|field|param(?:eter)?)s?\s+[`"']([^`"']+)[`"']/i,
        );
        const cue = text.match(
          /\b(command|query|input|body|path|url|text|content|param|parameter)\b/i,
        );
        const field = marked
          ? marked[1].trim()
          : cue
            ? cue[1].toLowerCase()
            : tools.length >= 2 && !forbidden.includes(tools[1])
              ? tools[1]
              : "command";
        return argBlacklist(tool, field, forbidden);
      }
      case "requires_permission": {
        // "`deploy` requires permission `approve_deploy`" names a tool,
        // not a permission held in the agent's static definition, and
        // ``requires_permission`` reads its atom from that definition:
        // armed against a permission nobody grants, it fires on every
        // call. Python routes a tool-shaped name to must_precede.
        const perm = tools[1];
        return perm.includes("_") || perm.endsWith("()")
          ? mustPrecede(perm, tools[0])
          : requiresPermission(tools[0], perm);
      }
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
          ) ||
          // "never `A` after `B`" and its two siblings put the
          // contradiction first as well, and matched none of the
          // spellings above because the negation word is separated from
          // "after" by the action itself.
          /^(?:never|cannot|can\s*not|must\s+not)\b(?=.*\bafter\b)/.test(lower) ||
          forbiddenActionComesFirst(lower, tools[0], tools[1]);
        return swap
          ? noReversal(tools[1], tools[0])
          : noReversal(tools[0], tools[1]);
      }
      case "cooldown": {
        const n = parseStepCount(text);
        if (n == null) continue;
        return cooldown(tools[0], n);
      }
      case "arg_allowlist": {
        const allowed = extractAllowlistPatterns(text);
        if (!allowed.length) continue;
        // A second name is the field; with only one name the sentence
        // named the tool and left the field implicit, as Python does.
        return argAllowlist(tools[0], tools[1] ?? "command", allowed);
      }
      case "data_intact": {
        const paths = tools.filter((t) => t.startsWith("/"));
        if (!paths.length) continue;
        return dataIntact(tools[0], paths);
      }
      case "dry_run_before_commit":
        return dryRunBeforeCommit(tools[0], tools[1]);
      case "backup_before_destructive":
        return backupBeforeDestructive(tools[0], tools[1]);
      case "audit_after":
        // One name means the audit step is derived, the same convention
        // Python uses, so "`deploy` must be logged" is a usable rule
        // rather than an arity error.
        return auditAfter(tools[0], tools[1] ?? `audit_${tools[0]}`);
      case "approval_freshness": {
        const steps = parseStepCount(text) ?? parseNumber(text);
        if (steps == null) continue;
        return tools.length >= 2
          ? approvalFreshness(tools[0], tools[1], steps)
          : approvalFreshness(`approve_${tools[0]}`, tools[0], steps);
      }
      case "sanitized_before_sink":
        return sanitizedBeforeSink(tools[0], tools[1], tools[2]);
      case "duplicate_call_limit": {
        const n = parseRateLimitCount(text) ?? parseNumber(text);
        if (n == null) continue;
        return duplicateCallLimit(tools[0], tools[1], n);
      }
      case "bounded_retry": {
        const n = parseRetryCount(text);
        if (n == null) continue;
        return boundedRetry(tools[0], n);
      }
      case "must_confirm":
        return mustConfirm(tools[0]);
      case "no_data_leak": {
        // ``no_data_leak`` is about a kind of data reaching a sink. When
        // the first name is a tool rather than a kind, the sentence is
        // saying "having called A, do not call B", which is a reversal.
        // Python routes it that way and this side did not, so the same
        // rulebook produced two different patterns and the cloud counted
        // them as two findings.
        const DATA_KINDS = new Set([
          "pii", "credentials", "personal_data", "sensitive", "secrets",
        ]);
        return DATA_KINDS.has(tools[0].toLowerCase())
          ? noDataLeak(tools[0], tools[1])
          : noReversal(tools[0], tools[1]);
      }
      case "deadline": {
        const n = parseStepCount(text);
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

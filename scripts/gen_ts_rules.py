#!/usr/bin/env python3
"""Emit the TypeScript keyword table from the Python one.

155 regexes copied by hand is how the two parsers drifted the first
time: this side reached 24 rules while the other sat at 12, and the
difference was invisible because a TypeScript parser that cannot read a
rule returns null rather than failing.

    python scripts/gen_ts_rules.py            # print the table
    python scripts/gen_ts_rules.py --write    # splice it into nl-parser.ts

``minArgs`` is TypeScript's own. Python has no pre-gate and lets each
branch check its own arity, so the numbers below are what each handler
in the switch actually needs.
"""

from __future__ import annotations

import argparse
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET = ROOT / "ts" / "packages" / "sdk" / "src" / "core" / "nl-parser.ts"

NEEDS = {
    "arg_allowlist": 1,
    "arg_blacklist": 1,
    "scope_limit": 0,
    "data_intact": 1,
    "dry_run_before_commit": 2,
    "backup_before_destructive": 2,
    "audit_after": 1,
    "approval_freshness": 1,
    "sanitized_before_sink": 3,
    "duplicate_call_limit": 2,
    "bounded_retry": 1,
    "rate_limit": 1,
    "idempotent": 1,
    "mutual_exclusion": 2,
    "cooldown": 1,
    "segregation_of_duty": 2,
    "deadline": 2,
    "must_confirm": 1,
    "no_reversal": 2,
    "requires_permission": 2,
    "no_data_leak": 2,
    "always_followed_by": 2,
    "must_precede": 2,
}

HEADER = """// Generated from Python's ``_KEYWORD_RULES`` by scripts/gen_ts_rules.py,
// because a table of 155 regexes copied by hand drifts, and it drifted:
// this side carried 46 of them and silently returned null for the rest,
// so a rulebook that armed 23 patterns in Python armed 12 here and said
// nothing about the other 11.
//
// ``minArgs`` is this side's own: Python has no pre-gate and lets each
// branch check its own arity, so the numbers here are what each handler
// below actually needs, not the ones in Python's table.
const KEYWORD_RULES: KeywordRule[] = [
"""


def as_js(rx: str) -> str:
    """A Python raw-string regex as a JavaScript regex literal.

    Backslashes carry over unchanged; only the delimiter needs escaping,
    and ``\\"`` is an identity escape Python's raw strings hold and
    JavaScript rejects under the u flag.
    """
    return "/" + rx.replace('\\"', '"').replace("/", r"\/") + "/"


def table() -> str:
    from sponsio.generation import dsl_to_contract as dsl

    missing = [name for _, name, _ in dsl._KEYWORD_RULES if name not in NEEDS]
    if missing:
        raise SystemExit(
            f"add {', '.join(sorted(set(missing)))} to NEEDS, and a handler "
            f"for it in nl-parser.ts, before regenerating"
        )

    out = []
    for keywords, name, _min_args in dsl._KEYWORD_RULES:
        pats = ",\n      ".join(as_js(k) for k in keywords)
        out.append(
            '  {\n    patterns: [\n      %s,\n    ],\n    patternName: "%s",\n'
            "    minArgs: %d,\n  }," % (pats, name, NEEDS[name])
        )
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="splice into nl-parser.ts")
    args = ap.parse_args()

    body = table()
    if not args.write:
        print(HEADER + body + "\n];")
        return 0

    src = TARGET.read_text()
    start = src.index("// Generated from Python's ``_KEYWORD_RULES``")
    end = src.index("\n];", start) + len("\n];")
    TARGET.write_text(src[:start] + HEADER + body + "\n];" + src[end:])
    rules = len(re.findall(r"patternName:", body))
    regexes = body.count("\n      /")
    print(f"{rules} rules, {regexes} regexes -> {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

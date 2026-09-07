#!/usr/bin/env python3
"""Rebuild the cross-language NL corpus from this repo's own rule strings.

The two parsers drifted because nothing compared them: Python grew to 24
keyword rules and 155 regexes while TypeScript sat at 12 and 46, and the
difference was invisible because the TypeScript side returns null rather
than failing. A rulebook that armed 23 patterns in one runtime armed 12
in the other and said nothing.

This harvests every string in the repo that the Python DSL actually
parses, records what it compiles to, and writes it where
``tests/cross_language`` and ``scripts/check_nl_parity.mjs`` can hold the
other side to it.

    python scripts/gen_nl_corpus.py
"""

from __future__ import annotations

import ast
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "cross_language" / "nl_corpus.json"

LOOKS_LIKE_RULE = re.compile(r"`[^`]+`|at most|must |never |cannot |after |before ")

# Diagnostics and docstrings the cascade happens to match. Keeping them
# would measure the parser against its own error messages.
NOISE = ("needs ", "e.g.", "Dual of", "must be non-empty")


def harvest() -> list[str]:
    found: set[str] = set()
    for f in list(ROOT.glob("tests/**/*.py")) + list(ROOT.glob("sponsio/**/*.py")):
        try:
            tree = ast.parse(f.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            v = node.value.strip()
            if not (8 <= len(v) <= 160) or "\n" in v:
                continue
            if any(n in v for n in NOISE) or not LOOKS_LIKE_RULE.search(v):
                continue
            found.add(v)
    return sorted(found)


def flat(x):
    return [flat(i) for i in x] if isinstance(x, (list, tuple)) else x


def main() -> int:
    from sponsio.generation.dsl_to_contract import parse_dsl

    cases = []
    for rule in harvest():
        try:
            parsed = parse_dsl(rule)
        except Exception:  # noqa: BLE001 - an unparseable string is simply not a case
            continue
        if parsed is None or getattr(parsed, "error", None):
            continue
        formula = getattr(parsed, "formula", None)
        if formula is None or not getattr(formula, "pattern_name", ""):
            continue
        cases.append(
            {
                "rule": rule,
                "pattern": formula.pattern_name,
                "args": [flat(a) for a in (formula.args or ())],
            }
        )

    OUT.write_text(
        json.dumps(
            {
                "_comment": (
                    "Every rule sentence in this repo that the Python DSL parses, "
                    "with the pattern and args it compiles to. The TypeScript "
                    "parser must agree on all of them. Regenerate with "
                    "scripts/gen_nl_corpus.py after changing either parser."
                ),
                "cases": cases,
            },
            indent=1,
            ensure_ascii=False,
        )
        + "\n"
    )
    print(f"{len(cases)} cases -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

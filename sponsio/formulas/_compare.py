"""Numeric-string coercion for ordered comparisons (Python/TS parity).

See https://github.com/SponsioLabs/Sponsio/issues/108.

Raw tool arguments are grounded as strings (``grounding`` stores
``{"amount": "5000"}`` verbatim), so a naturally-written numeric safety
guard such as ``Not(Gt(ArgValue("pay", "amount"), Const(1000)))`` reaches
the evaluator as ``compare("gt", "5000", 1000)``. Historically Python
raised ``TypeError`` (``str`` vs ``int``) and fell through to ``False``,
while TypeScript's relational operators coerced the string — so the *same*
contract failed **open** on Python (guard holds -> allow) and **closed** on
TypeScript (guard violated -> block).

Canonical fix: for an ORDERED comparison, coerce a plain-numeric STRING
operand to a number when the other operand is numeric, on both runtimes, so
a numeric guard compares numerically and fails **closed**. Non-numeric
strings are left unchanged and fall through to the caller's fail-safe
(``False``), preserving the Hoare-vacuity convention for genuinely
incomparable operands.

The regex is kept byte-identical to the TypeScript side
(``ts/packages/sdk/src/core/evaluator.ts``): a plain decimal / float /
scientific literal, deliberately excluding ``inf``/``nan``/hex/empty so
Python ``float()`` and JS ``Number()`` agree exactly on every accepted
string.
"""

from __future__ import annotations

import re

# Plain decimal / float / scientific literal. Excludes inf, nan, hex, and the
# empty string so float() and Number() produce the same value for every match.
_NUMERIC_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def _is_number(v: object) -> bool:
    # bool is intentionally excluded: ``True < 2`` already compares natively
    # (and identically) on both runtimes, so it needs no coercion.
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _numeric_string(v: object) -> float | None:
    """Return the numeric value of a plain-numeric string, else ``None``."""
    if isinstance(v, str):
        s = v.strip()
        if _NUMERIC_RE.match(s):
            try:
                return float(s)
            except ValueError:  # pragma: no cover - regex already gates this
                return None
    return None


def coerce_ordered(left: object, right: object) -> tuple[object, object]:
    """Coerce a numeric-looking string to a number for an ordered comparison.

    Only fires when exactly one operand is numeric and the other is a
    plain-numeric string; everything else (two numbers, two strings, a
    non-numeric string, ``None``) is returned unchanged.
    """
    if _is_number(left) and isinstance(right, str):
        n = _numeric_string(right)
        if n is not None:
            return left, n
    elif _is_number(right) and isinstance(left, str):
        n = _numeric_string(left)
        if n is not None:
            return n, right
    return left, right

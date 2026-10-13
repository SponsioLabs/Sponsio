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

Canonical fix: for an ORDERED comparison, coerce a numeric STRING operand
to a number when the other operand is numeric, on both runtimes, so a
numeric guard compares numerically and fails **closed**.

What counts as a numeric string is deliberately wider than a bare literal.
A model writing a currency amount emits ``"$5,000"``, ``"5,000"`` or
``"5000 USD"`` at least as readily as ``"5000"``, and treating those as
"not a number" let them sail past a cap that stopped the plain form.
Grouping separators, a leading currency symbol and a trailing currency
code or unit are therefore normalised away before the literal test.
Commas are removed only in unambiguous thousands grouping
(``5,000`` / ``1,234,567.89``), never from ``5,50``, where the comma may
be a decimal separator and guessing would silently change the magnitude.
European grouping is read when it is unambiguous (``5.000,00`` /
``1.000.000``); ``€5.000`` and ``5.000 EUR`` are refused rather than read
as 5, and a bare ``5.000`` stays the float it is in every programming
language.

Overflow is a value, not a parse failure: ``"1e400"`` is beyond both
runtimes' float range and becomes infinity on each. Both accept it and
compare with it, so a "must not exceed" guard fires rather than one
runtime blocking while the other allows.

A string that still does not parse leaves the comparison incomparable and
falls through to the caller's fail-safe (``False``), preserving the
Hoare-vacuity convention — but it now emits a warning first, because a
numeric guard meeting a non-numeric value is a contract that is not
protecting what its author believes it protects.

The accepted grammar is kept in step with the TypeScript side
(``ts/packages/sdk/src/core/evaluator.ts``).
"""

from __future__ import annotations

import re
import warnings

# Plain decimal / float / scientific literal. Excludes inf, nan, hex, and the
# empty string so float() and Number() produce the same value for every match.
_NUMERIC_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")

# Unambiguous thousands grouping: 5,000 / 1,234,567 / 1,234,567.89.
_GROUPED_RE = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")

# Unambiguous European grouping: a decimal comma after dot groups
# (5.000,00 / 1.234.567,89) or two or more dot groups (1.000.000). One
# dot group alone (5.000) is a plain float on its own.
_EU_GROUPED_RE = re.compile(r"^[+-]?\d{1,3}(\.\d{3})+,\d+$|^[+-]?\d{1,3}(\.\d{3}){2,}$")

# One dot group behind a currency symbol or unit (€5.000 / 5.000 EUR):
# five thousand in Berlin, five with three decimals in Boston. A money
# amount with three decimals is rare, a cap that reads it as 5 is a
# bypass, and guessing either way changes the magnitude a thousandfold,
# so it is left unparsed and the numeric guard refuses it. The leading
# digit is non-zero: nobody writes 0.500 for five hundred.
_EU_AMBIGUOUS_RE = re.compile(r"^[+-]?[1-9]\d{0,2}\.\d{3}$")

# A leading currency symbol, optionally after the sign.
_CURRENCY_PREFIX_RE = re.compile(r"^([+-]?)\s*[$€£¥₹]\s*")

# A trailing currency code or unit: "5000 USD", "5000USD", "12 %".
# No ``\s*`` adjacent to the anchor: ``\s*X\s*$`` is the polynomial-ReDoS
# shape (a long run of spaces retries at every position), and the TypeScript
# mirror is flagged for it. Whitespace is trimmed separately, and the
# lookbehind keeps the unit from eating the tail of a longer word.
_UNIT_SUFFIX_RE = re.compile(r"(?<![A-Za-z])(?:[A-Za-z]{2,4}|%)$")

# Values already reported, so a loop over the same bad argument warns once.
_WARNED: set[str] = set()


def _is_number(v: object) -> bool:
    # bool is intentionally excluded: ``True < 2`` already compares natively
    # (and identically) on both runtimes, so it needs no coercion.
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _normalise(s: str) -> str:
    """Strip the formatting a model wraps around a number.

    Returns a string :data:`_NUMERIC_RE` accepts when the value is
    unambiguous, and the input untouched when it is not.
    """
    original = s.strip()
    s = original
    s = _CURRENCY_PREFIX_RE.sub(r"\1", s)
    currency = s != original
    stripped_unit = _UNIT_SUFFIX_RE.sub("", s).rstrip()
    # Only drop the suffix if a number is left; "USD" alone must stay unparsed,
    # and the exponent in "1e5" must not be mistaken for a unit.
    unit = False
    if stripped_unit and stripped_unit != s:
        bare = stripped_unit.replace(" ", "")
        if (
            _GROUPED_RE.match(bare)
            or _NUMERIC_RE.match(stripped_unit)
            or _EU_GROUPED_RE.match(bare)
            or _EU_AMBIGUOUS_RE.match(bare)
        ):
            s = stripped_unit
            unit = True
    s = s.strip()
    if _EU_GROUPED_RE.match(s):
        return s.replace(".", "").replace(",", ".")
    if (currency or unit) and _EU_AMBIGUOUS_RE.match(s):
        return original
    if _GROUPED_RE.match(s):
        s = s.replace(",", "")
    return s


def _numeric_string(v: object) -> float | None:
    """Return the numeric value of a numeric-looking string, else ``None``."""
    if isinstance(v, str):
        s = _normalise(v)
        if _NUMERIC_RE.match(s):
            try:
                # Overflow to inf is intentional and matched in TypeScript:
                # the value really is larger than the runtime can hold, and a
                # cap should fire rather than be skipped.
                return float(s)
            except ValueError:  # pragma: no cover - regex already gates this
                return None
    return None


def _warn_incomparable(value: str) -> None:
    key = value[:120]
    if key in _WARNED:
        return
    _WARNED.add(key)
    warnings.warn(
        f"sponsio: numeric guard received a non-numeric value {value[:60]!r}; "
        "the comparison cannot be evaluated and the guard does NOT constrain "
        "this call. Normalise the argument, or express the rule with "
        "`arg_blacklist` / `arg_field_has` so it matches on text.",
        UserWarning,
        stacklevel=4,
    )


def coerce_ordered(left: object, right: object) -> tuple[object, object]:
    """Coerce a numeric-looking string to a number for an ordered comparison.

    Only fires when exactly one operand is numeric and the other is a
    numeric-looking string; everything else (two numbers, two strings,
    ``None``) is returned unchanged. A string that cannot be coerced while
    the other side is numeric is reported once via :mod:`warnings`.
    """
    if _is_number(left) and isinstance(right, str):
        n = _numeric_string(right)
        if n is not None:
            return left, n
        _warn_incomparable(right)
    elif _is_number(right) and isinstance(left, str):
        n = _numeric_string(left)
        if n is not None:
            return n, right
        _warn_incomparable(left)
    return left, right

"""Pre-flight regex validation for compiled formulas.

Sponsio's grounding layer matches several atom predicates with
:func:`re.search`.  When a pattern uses a feature the Python ``re``
engine doesn't accept (variable-width lookbehind, malformed character
classes, unbalanced groups, ...), today's failure mode is a runtime
``re.error`` deep inside the verifier — silent in observe mode, hard to
attribute, and only triggered the first time the relevant tool fires.

This module walks a finished AST, finds every atom whose argument
slot is documented to be a regex, and tries ``re.compile()`` on each.
Any failure surfaces immediately at config-load time / via
``sponsio validate``.

Invalid-regex examples this catches:

  * ``arg_field_has(t, f, '.*/dev/.*(?<!/prod/.*)')``
    — variable-width lookbehind (``(?<!.*)``) is unsupported.
  * ``called_with(t, '[unclosed')``
    — unterminated character class.
  * ``output_has(t, '(unclosed')``
    — unbalanced group.
"""

from __future__ import annotations

import re

from sponsio.formulas.formula import (
    And,
    Atom,
    F,
    G,
    Implies,
    Not,
    Or,
    U,
    X,
)


# Maps predicate name → 0-based argument index of the regex pattern.
# Only includes atoms whose arg slot is treated as a Python ``re``
# pattern by ``sponsio.tracer.grounding``.  ``arg_paths_within``,
# ``arg_numeric``, ``arg_length_exceeds`` and ``ctx_matches`` are
# omitted: the first three don't take regex args, and ``ctx_matches``
# uses a different arg layout.  Mirror this with grounding when adding
# a new regex-bearing predicate.
_REGEX_ARG_INDEX: dict[str, int] = {
    "llm_said": 0,
    "prompt_contains": 0,
    "output_has": 1,
    "arg_has": 1,
    "arg_field_has": 2,
    "called_with": 1,
    "count_with": 1,
    "ctx_matches": 1,
}


class RegexValidationError(ValueError):
    """Raised when a formula contains an unparseable regex argument.

    The message includes the predicate, the offending pattern, and the
    underlying ``re.error`` so users can locate it in their YAML.
    """


def _iter_atoms(node):
    """Walk an AST and yield every :class:`Atom` node."""
    if isinstance(node, Atom):
        yield node
        return
    # Unary temporal / boolean nodes
    if isinstance(node, (G, F, X, Not)):
        yield from _iter_atoms(node.child)
        return
    # Binary nodes
    if isinstance(node, (And, Or, Implies, U)):
        yield from _iter_atoms(node.left)
        yield from _iter_atoms(node.right)
        return
    # Comparison / arithmetic nodes (Le/Ge/Eq/...) — they hold operands
    # under .left / .right too; rely on duck-typing rather than importing
    # every numeric class explicitly so this stays robust to additions.
    left = getattr(node, "left", None)
    right = getattr(node, "right", None)
    if left is not None:
        yield from _iter_atoms(left)
    if right is not None:
        yield from _iter_atoms(right)
    child = getattr(node, "child", None)
    if child is not None:
        yield from _iter_atoms(child)


def check_regexes(node) -> None:
    """Compile every regex argument in ``node`` and raise on failure.

    Args:
        node: A formula AST root (any node from :mod:`sponsio.formulas.formula`).

    Raises:
        RegexValidationError: On the first regex that fails to compile.
            The error message identifies the predicate and pattern.
    """
    for atom in _iter_atoms(node):
        idx = _REGEX_ARG_INDEX.get(atom.predicate)
        if idx is None:
            continue
        if idx >= len(atom.args):
            continue
        pattern = atom.args[idx]
        if not isinstance(pattern, str):
            continue
        try:
            re.compile(pattern)
        except re.error as e:
            raise RegexValidationError(
                f"Invalid regex in {atom.predicate}(...) arg {idx}: {pattern!r} — {e}"
            ) from e

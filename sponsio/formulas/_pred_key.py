"""Single source of truth for predicate key formatting.

Both the **formula side** (``Atom.key()``) and the **grounding side**
(``grounding.ground()``) must produce identical string keys for the
evaluator's ``dict.get()`` lookup to work.  This module defines the
canonical format so the two sides cannot drift out of sync.

Format: ``"predicate(arg1, arg2)"`` -- e.g. ``"called(fraud_check)"``
or ``"precedes(fraud_check, execute_refund)"``.

Example:
    >>> from sponsio.formulas._pred_key import pred_key
    >>> pred_key("called", "fraud_check")
    'called(fraud_check)'
    >>> pred_key("precedes", "fraud_check", "execute_refund")
    'precedes(fraud_check, execute_refund)'
"""


def _escape(s: str) -> str:
    """Escape characters that would be ambiguous in predicate keys."""
    s = str(s)
    return (
        s.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace(",", "\\,")
        .replace(" ", "\\ ")
    )


def pred_key(predicate: str, *args: str) -> str:
    """Build the canonical string key for a predicate.

    Arguments are escaped so that tool names containing ``(``, ``)``,
    or ``,`` do not produce ambiguous keys.

    Args:
        predicate: Predicate name (e.g. ``"called"``, ``"precedes"``).
        *args: Predicate arguments (e.g. tool names).

    Returns:
        A string like ``"called(fraud_check)"`` or
        ``"precedes(fraud_check, execute_refund)"``.
        Zero-argument predicates produce ``"predicate()"``.
    """
    if not args:
        return f"{predicate}()"
    return f"{predicate}({', '.join(_escape(a) for a in args)})"

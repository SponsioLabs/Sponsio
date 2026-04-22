"""Generate natural language descriptions from formula ASTs.

Converts a formula AST into a human-readable description for display
in YAML configs, CLI output, and dashboards.

Usage::

    >>> from sponsio.formulas.nl_gen import formula_to_nl
    >>> from sponsio.formulas.formula import G, Implies, Atom, Not, Or
    >>> f = G(Implies(Atom("called", "cancel"), Atom("called", "get_order")))
    >>> formula_to_nl(f)
    'whenever cancel is called, get_order must have been called before'
"""

from __future__ import annotations

from sponsio.formulas.formula import (
    And,
    Atom,
    Const,
    Eq,
    F,
    G,
    Ge,
    Gt,
    Implies,
    Le,
    Lt,
    Not,
    Or,
    U,
    Var,
    X,
)


def formula_to_nl(node) -> str:
    """Convert a formula AST node to natural language.

    Args:
        node: Any formula AST node.

    Returns:
        Human-readable description string.
    """
    return _to_nl(node)


def _to_nl(node) -> str:
    """Recursive NL generation."""

    if isinstance(node, Atom):
        return _atom_nl(node)

    if isinstance(node, Not):
        child = node.child
        # Special case: Not(called(X)) → "X is not called"
        if isinstance(child, Atom) and child.predicate == "called":
            return f"`{child.args[0]}` is not called"
        if isinstance(child, Atom) and child.predicate == "called_with":
            return f"`{child.args[0]}` (matching {child.args[1]}) is not called"
        if isinstance(child, Atom) and child.predicate == "arg_field_has":
            return f"`{child.args[0]}`.{child.args[1]} must not match `{child.args[2]}`"
        if isinstance(child, Atom) and child.predicate == "arg_length_exceeds":
            return f"`{child.args[0]}`.{child.args[1]} must not exceed {child.args[2]} chars"
        return f"not ({_to_nl(child)})"

    if isinstance(node, And):
        return f"{_to_nl(node.left)}, and {_to_nl(node.right)}"

    if isinstance(node, Or):
        return f"{_to_nl(node.left)}, or {_to_nl(node.right)}"

    if isinstance(node, Implies):
        # Special case: called(A) → called(B) means "if A then B must have been called"
        ant = node.left
        con = node.right
        ant_nl = _to_nl(ant)
        con_nl = _to_nl(con)
        return f"if {ant_nl}, then {con_nl}"

    if isinstance(node, G):
        child = node.child
        # G(Implies(...)) → "always: if ... then ..."
        if isinstance(child, Implies):
            return f"always: {_to_nl(child)}"
        # G(Not(called(X))) → "X must never be called"
        if isinstance(child, Not) and isinstance(child.child, Atom):
            atom = child.child
            if atom.predicate == "called":
                return f"`{atom.args[0]}` must never be called"
            if atom.predicate == "called_with":
                return (
                    f"`{atom.args[0]}` (matching {atom.args[1]}) must never be called"
                )
        # G(Le(...)) → rate limit
        if isinstance(child, Le):
            return _comparison_nl(child, "at most")
        return f"always: {_to_nl(child)}"

    if isinstance(node, F):
        child = node.child
        if isinstance(child, Atom) and child.predicate == "called":
            return f"`{child.args[0]}` must eventually be called"
        return f"eventually: {_to_nl(child)}"

    if isinstance(node, X):
        return f"in the next step: {_to_nl(node.child)}"

    if isinstance(node, U):
        # Not(called(B)) U called(A) → "A must precede B"
        left, right = node.left, node.right
        if (
            isinstance(left, Not)
            and isinstance(left.child, Atom)
            and isinstance(right, Atom)
        ):
            la = left.child
            ra = right
            if la.predicate in ("called", "called_with") and ra.predicate in (
                "called",
                "called_with",
            ):
                b_name = _tool_name(la)
                a_name = _tool_name(ra)
                return f"`{a_name}` must precede `{b_name}`"
        return f"{_to_nl(left)} until {_to_nl(right)}"

    if isinstance(node, Le):
        return _comparison_nl(node, "at most")
    if isinstance(node, Lt):
        return _comparison_nl(node, "less than")
    if isinstance(node, Ge):
        return _comparison_nl(node, "at least")
    if isinstance(node, Gt):
        return _comparison_nl(node, "more than")
    if isinstance(node, Eq):
        return _comparison_nl(node, "exactly")

    if isinstance(node, Var):
        return node.key()
    if isinstance(node, Const):
        return str(node.value)

    return str(node)


# Built-in PII regex fragments (kept in sync with
# ``sponsio.patterns.library._DEFAULT_PII_PATTERNS``). If every fragment
# of a user-supplied regex matches one of these — in any order — we
# render the human labels instead of the raw alternation. Used only by
# :func:`_atom_nl` to make the `compiled:` banner line readable.
_PII_FRAGMENTS: dict[str, str] = {
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "credit_card": r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",
    "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    "phone": r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
}


def _match_pii_bundle(pattern: str) -> list[str] | None:
    """If ``pattern`` is exactly an alternation of built-in PII
    fragments, return the matched labels (in the order they appear).
    Otherwise return ``None``.

    Splitting on ``|`` is unsafe here because the email fragment
    contains ``[A-Z|a-z]`` (a pipe inside a character class). Instead
    we check structurally: find each known fragment in the pattern,
    verify the matches are non-overlapping, cover the entire pattern
    (modulo the ``|`` separators), and leave nothing unaccounted for.
    """
    frags = list(_PII_FRAGMENTS.items())
    spans: list[tuple[int, int, str]] = []  # (start, end, name)
    for name, frag in frags:
        start = 0
        while True:
            idx = pattern.find(frag, start)
            if idx == -1:
                break
            spans.append((idx, idx + len(frag), name))
            start = idx + len(frag)
    if not spans:
        return None
    spans.sort()

    # Walk the pattern and require: fragment, optional `|`, fragment, ...
    # with no leftover characters.
    pos = 0
    labels: list[str] = []
    for s, e, name in spans:
        if s < pos:
            continue  # overlapping match — skip (shouldn't happen for our set)
        if s == pos:
            pass  # tight — first fragment or follows `|`
        elif s == pos + 1 and pattern[pos] == "|":
            pass  # separator then fragment
        else:
            return None  # gap with unexpected content
        labels.append(name)
        pos = e
    if pos != len(pattern):
        return None
    return labels


def _atom_nl(atom: Atom) -> str:
    """Generate NL for an atom."""
    pred = atom.predicate
    args = atom.args

    if pred == "called" and len(args) == 1:
        return f"`{args[0]}` is called"
    if pred == "called_with" and len(args) == 2:
        return f"`{args[0]}` (matching `{args[1]}`) is called"
    if pred == "perm" and len(args) == 1:
        return f"agent has permission `{args[0]}`"
    if pred == "arg_has" and len(args) == 2:
        return f"`{args[0]}` args match `{args[1]}`"
    if pred == "arg_field_has" and len(args) == 3:
        return f"`{args[0]}`.{args[1]} matches `{args[2]}`"
    if pred == "arg_length_exceeds" and len(args) == 3:
        return f"`{args[0]}`.{args[1]} exceeds {args[2]} chars"
    if pred == "arg_paths_within" and len(args) >= 2:
        paths = ", ".join(args[1:])
        return f"`{args[0]}` file paths are within [{paths}]"
    if pred == "output_has" and len(args) == 2:
        return f"`{args[0]}` output matches `{args[1]}`"
    if pred == "contains" and len(args) == 1:
        return f"data contains field `{args[0]}`"
    if pred == "flow" and len(args) == 2:
        return f"data flows from `{args[0]}` to `{args[1]}`"
    if pred == "llm_said" and len(args) == 1:
        # Special-case the built-in PII regex bundle: dumping the raw
        # alternation regex in a banner is a wall of backslashes that
        # looks broken. Detect it and render the human labels instead.
        pii_labels = _match_pii_bundle(args[0])
        if pii_labels:
            return f"LLM output matches PII ({', '.join(pii_labels)})"
        return f"LLM output matches `{args[0]}`"
    if pred == "prompt_contains" and len(args) == 1:
        return f"prompt contains `{args[0]}`"

    # Generic
    args_str = ", ".join(args) if args else ""
    return f"{pred}({args_str})"


def _tool_name(atom: Atom) -> str:
    """Extract a readable tool name from an atom."""
    if atom.predicate == "called":
        return atom.args[0]
    if atom.predicate == "called_with":
        return f"{atom.args[0]}:{atom.args[1]}"
    return str(atom)


def _comparison_nl(node, op_word: str) -> str:
    """Generate NL for comparison nodes."""
    left = node.left
    right = node.right

    if isinstance(left, Var) and isinstance(right, Const):
        if left.name == "count" and left.args:
            tool = left.args[0]
            return f"`{tool}` called {op_word} {right.value} times"
        if left.name == "count_with" and len(left.args) >= 2:
            tool, pattern = left.args[0], left.args[1]
            return (
                f"`{tool}` (matching `{pattern}`) called {op_word} {right.value} times"
            )
        return f"{left.key()} {op_word} {right.value}"

    return f"{_to_nl(left)} {op_word} {_to_nl(right)}"

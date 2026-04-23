"""Parse formula strings into AST nodes.

Converts string representations like::

    G(Implies(called(A), Or(called(B), called(C))))

into the corresponding AST objects from ``formula.py``.

Grammar::

    formula := atom | not | and | or | implies | g | f | x | u | le | ge | lt | gt | eq
    atom    := PRED "(" args ")"
    not     := "Not" "(" formula ")"
    and     := "And" "(" formula "," formula ")"
    or      := "Or" "(" formula "," formula ")"
    implies := "Implies" "(" formula "," formula ")"
    g       := "G" "(" formula ")"
    f       := "F" "(" formula ")"
    x       := "X" "(" formula ")"
    u       := "U" "(" formula "," formula ")"
    le      := "Le" "(" var "," const ")"
    var     := "Var" "(" STRING ("," STRING)* ")"
    const   := "Const" "(" NUMBER ")"
    args    := STRING ("," STRING)*

Usage::

    >>> from sponsio.formulas.parser import parse_formula
    >>> f = parse_formula("G(Implies(called(A), Not(called(B))))")
    >>> type(f).__name__
    'G'
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


class ParseError(Exception):
    """Raised when a formula string cannot be parsed."""


def parse_formula(text: str) -> And | Or | Not | Implies | G | F | X | U | Atom:
    """Parse a formula string into an AST.

    Args:
        text: Formula string (e.g., ``"G(Implies(called(A), called(B)))"``)

    Returns:
        The root AST node.

    Raises:
        ParseError: If the string cannot be parsed.
    """
    text = text.strip()
    if not text:
        raise ParseError("Empty formula")

    tokens = _tokenize(text)
    result, pos = _parse_expr(tokens, 0)
    if pos != len(tokens):
        raise ParseError(f"Unexpected tokens after position {pos}: {tokens[pos:]}")
    return result


def _tokenize(text: str) -> list[str]:
    """Tokenize a formula string into a list of tokens."""
    tokens = []
    i = 0
    while i < len(text):
        c = text[i]
        if c in " \t\n\r":
            i += 1
        elif c in "(),":
            tokens.append(c)
            i += 1
        elif c == "'":
            # Quoted string
            j = text.index("'", i + 1)
            tokens.append(text[i + 1 : j])
            i = j + 1
        elif c == '"':
            j = text.index('"', i + 1)
            tokens.append(text[i + 1 : j])
            i = j + 1
        else:
            # Identifier or number
            j = i
            while j < len(text) and text[j] not in "(),'\"\t\n\r ":
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def _parse_expr(tokens: list[str], pos: int) -> tuple:
    """Parse an expression starting at position pos."""
    if pos >= len(tokens):
        raise ParseError("Unexpected end of formula")

    tok = tokens[pos]

    # Unary operators
    if tok in ("G", "F", "X", "Not"):
        cls = {"G": G, "F": F, "X": X, "Not": Not}[tok]
        pos = _expect(tokens, pos + 1, "(")
        child, pos = _parse_expr(tokens, pos)
        pos = _expect(tokens, pos, ")")
        return cls(child), pos

    # Binary operators
    if tok in ("And", "Or", "Implies", "U"):
        cls = {"And": And, "Or": Or, "Implies": Implies, "U": U}[tok]
        pos = _expect(tokens, pos + 1, "(")
        left, pos = _parse_expr(tokens, pos)
        pos = _expect(tokens, pos, ",")
        right, pos = _parse_expr(tokens, pos)
        pos = _expect(tokens, pos, ")")
        return cls(left, right), pos

    # Comparison operators
    if tok in ("Le", "Lt", "Ge", "Gt", "Eq"):
        cls = {"Le": Le, "Lt": Lt, "Ge": Ge, "Gt": Gt, "Eq": Eq}[tok]
        pos = _expect(tokens, pos + 1, "(")
        left, pos = _parse_var_or_const(tokens, pos)
        pos = _expect(tokens, pos, ",")
        right, pos = _parse_var_or_const(tokens, pos)
        pos = _expect(tokens, pos, ")")
        return cls(left, right), pos

    # Var
    if tok == "Var":
        return _parse_var(tokens, pos)

    # Const
    if tok == "Const":
        return _parse_const(tokens, pos)

    # Atom: predicate(arg1, arg2, ...)
    # Any identifier followed by ( is treated as an atom
    if pos + 1 < len(tokens) and tokens[pos + 1] == "(":
        predicate = tok
        pos = _expect(tokens, pos + 1, "(")
        args = []
        while pos < len(tokens) and tokens[pos] != ")":
            if args:
                pos = _expect(tokens, pos, ",")
            arg_tok = tokens[pos]
            # Try to parse as number
            try:
                int(arg_tok)
                args.append(arg_tok)
            except ValueError:
                args.append(arg_tok)
            pos += 1
        pos = _expect(tokens, pos, ")")
        return Atom(predicate, *args), pos

    # Bare identifier — treat as 0-arg atom
    return Atom(tok), pos + 1


def _parse_var_or_const(tokens: list[str], pos: int) -> tuple:
    """Parse a Var or Const."""
    tok = tokens[pos]
    if tok == "Var":
        return _parse_var(tokens, pos)
    if tok == "Const":
        return _parse_const(tokens, pos)
    # Try as number → Const
    try:
        val = int(tok)
        return Const(val), pos + 1
    except ValueError:
        pass
    try:
        val = float(tok)
        return Const(val), pos + 1
    except ValueError:
        pass
    # Treat as Var
    return Var(tok), pos + 1


def _parse_var(tokens: list[str], pos: int) -> tuple:
    """Parse Var(name, arg1, arg2, ...)."""
    pos = _expect(tokens, pos + 1, "(")
    args = []
    while pos < len(tokens) and tokens[pos] != ")":
        if args:
            pos = _expect(tokens, pos, ",")
        args.append(tokens[pos])
        pos += 1
    pos = _expect(tokens, pos, ")")
    if not args:
        raise ParseError("Var requires at least a name")
    return Var(args[0], *args[1:]), pos


def _parse_const(tokens: list[str], pos: int) -> tuple:
    """Parse Const(value)."""
    pos = _expect(tokens, pos + 1, "(")
    val_str = tokens[pos]
    try:
        val = int(val_str)
    except ValueError:
        try:
            val = float(val_str)
        except ValueError:
            raise ParseError(f"Const value must be numeric, got: {val_str}")
    pos += 1
    pos = _expect(tokens, pos, ")")
    return Const(val), pos


def _expect(tokens: list[str], pos: int, expected: str) -> int:
    """Assert the next token is expected, return pos+1."""
    if pos >= len(tokens):
        raise ParseError(f"Expected '{expected}' but reached end of formula")
    if tokens[pos] != expected:
        raise ParseError(
            f"Expected '{expected}' at position {pos}, got '{tokens[pos]}'"
        )
    return pos + 1


# ---------------------------------------------------------------------------
# repr-format parser: handles the human-readable output of formula.__repr__
# ---------------------------------------------------------------------------
#
# The repr format uses infix operators that aren't valid Python:
#   !()    for Not
#   ->     for Implies
#   &      for And
#   |      for Or
#   ... U ...  for Until
#   (x <= y)   for Le, etc.
#   called('tool')         for Atom('called', 'tool')
#   arg_field_has('a','b') for Atom('arg_field_has', 'a', 'b')
#
# This parser tokenizes and uses recursive descent with proper precedence.


def parse_repr(text: str) -> And | Or | Not | Implies | G | F | X | U | Atom:
    """Parse a formula from its ``repr()`` output back into an AST.

    Unlike :func:`parse_formula` (which handles constructor-style strings
    like ``G(Implies(...))``), this function handles the human-readable
    repr format with infix operators (``->``, ``&``, ``|``, ``!``, ``U``,
    ``<=``, etc.).

    Args:
        text: A formula repr string, e.g.
            ``"G((called('auth') -> F(called('query'))))"``

    Returns:
        The root AST node.

    Raises:
        ParseError: If the string cannot be parsed.

    Example::

        >>> from sponsio.formulas.parser import parse_repr
        >>> f = parse_repr("G((called('A') -> !(called('B'))))")
        >>> type(f).__name__
        'G'
    """
    text = text.strip()
    if not text:
        raise ParseError("Empty formula")
    tokens = _tokenize_repr(text)
    pos = [0]

    def peek():
        return tokens[pos[0]] if pos[0] < len(tokens) else None

    def consume(expected=None):
        if pos[0] >= len(tokens):
            raise ParseError(f"Unexpected end, expected {expected!r}")
        t = tokens[pos[0]]
        if expected is not None and t != expected:
            raise ParseError(f"Expected {expected!r} at position {pos[0]}, got {t!r}")
        pos[0] += 1
        return t

    def parse_expr():
        left = _parse_repr_unary(peek, consume, parse_expr)
        while peek() in ("->", "&", "|", "U", "<=", ">=", "<", ">", "=="):
            op = consume()
            right = _parse_repr_unary(peek, consume, parse_expr)
            if op == "->":
                left = Implies(left, right)
            elif op == "&":
                left = And(left, right)
            elif op == "|":
                left = Or(left, right)
            elif op == "U":
                left = U(left, right)
            elif op == "<=":
                left = Le(left, right)
            elif op == ">=":
                left = Ge(left, right)
            elif op == "<":
                left = Lt(left, right)
            elif op == ">":
                left = Gt(left, right)
            elif op == "==":
                left = Eq(left, right)
        return left

    result = parse_expr()
    if pos[0] != len(tokens):
        raise ParseError(
            f"Unexpected tokens after position {pos[0]}: {tokens[pos[0] :]}"
        )
    return result


def _tokenize_repr(text: str) -> list[str]:
    """Tokenize a repr-format formula string."""
    tokens = []
    i = 0
    while i < len(text):
        if text[i] in " \t\n":
            i += 1
        elif text[i : i + 2] == "->":
            tokens.append("->")
            i += 2
        elif text[i : i + 2] == "<=":
            tokens.append("<=")
            i += 2
        elif text[i : i + 2] == ">=":
            tokens.append(">=")
            i += 2
        elif text[i : i + 2] == "==":
            tokens.append("==")
            i += 2
        elif text[i] in "()<>&|!,":
            tokens.append(text[i])
            i += 1
        elif text[i] in "'\"":
            q = text[i]
            j = i + 1
            while j < len(text) and text[j] != q:
                if text[j] == "\\":
                    j += 1
                j += 1
            tokens.append(text[i : j + 1])
            i = j + 1
        elif text[i].isdigit() or (
            text[i] == "-" and i + 1 < len(text) and text[i + 1].isdigit()
        ):
            j = i + 1 if text[i] == "-" else i
            while j < len(text) and (text[j].isdigit() or text[j] == "."):
                j += 1
            tokens.append(text[i:j])
            i = j
        elif text[i].isalpha() or text[i] == "_":
            j = i
            while j < len(text) and (text[j].isalnum() or text[j] == "_"):
                j += 1
            tokens.append(text[i:j])
            i = j
        else:
            i += 1
    return tokens


def _parse_repr_unary(peek, consume, parse_expr):
    """Parse a unary expression or atom in repr format."""
    t = peek()
    if t == "!":
        consume("!")
        # ``!`` binds tighter than the binary ops in ``parse_expr``, so we
        # recurse into another unary rather than back to ``parse_expr``.
        # This lets us accept all of:
        #   * ``!(expr)``        — historic form
        #   * ``!called(x)``     — predicate-tight (most LTL packs use this)
        #   * ``!F(...)``, ``!G(...)``, ``!X(...)``, ``!flow(...)``
        #   * ``!!x``            — double-negation, harmless
        # Without this the LTL pack files written in the natural infix
        # style fail to parse on the very first ``!flow`` token.
        child = _parse_repr_unary(peek, consume, parse_expr)
        return Not(child)
    elif t == "(":
        consume("(")
        expr = parse_expr()
        consume(")")
        return expr
    elif t == "G":
        consume("G")
        consume("(")
        child = parse_expr()
        consume(")")
        return G(child)
    elif t == "F":
        consume("F")
        consume("(")
        child = parse_expr()
        consume(")")
        return F(child)
    elif t == "X":
        consume("X")
        consume("(")
        child = parse_expr()
        consume(")")
        return X(child)
    elif t == "Var":
        consume("Var")
        consume("(")
        args = []
        while peek() != ")":
            if peek() == ",":
                consume(",")
            args.append(consume().strip("'\""))
        consume(")")
        return Var(args[0], *args[1:])
    elif t == "Atom":
        consume("Atom")
        consume("(")
        args = []
        while peek() != ")":
            if peek() == ",":
                consume(",")
            args.append(consume().strip("'\""))
        consume(")")
        return Atom(args[0], *args[1:])
    elif t == "Not":
        consume("Not")
        consume("(")
        child = parse_expr()
        consume(")")
        return Not(child)
    elif t and (t[0].isdigit() or (t[0] == "-" and len(t) > 1)):
        consume()
        return Const(float(t) if "." in t else int(t))
    else:
        # Atom shorthand: predicate_name('arg1', 'arg2', ...)
        name = consume()
        if peek() == "(":
            consume("(")
            args = []
            while peek() != ")":
                if peek() == ",":
                    consume(",")
                args.append(consume().strip("'\""))
            consume(")")
            return Atom(name, *args)
        return Atom(name)

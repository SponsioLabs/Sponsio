"""Every example in the pattern catalog must actually work.

``docs/reference/patterns.md`` is linked from the README as the reference
for the pattern library, so its table is where a new user copies their
first rule from. For a long time 19 of the 44 cells did not parse: the
column was called "NL example" and held sentences nobody had run. A user
following the reference got ``ContractSyntaxError`` from the reference.

This test reads the shipped file and puts every cell through the same
front door a user would.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from sponsio.formulas.formula import Atom  # noqa: F401  (eval'd by examples)
from sponsio.generation.dsl_to_contract import (
    get_available_patterns,
    parse_contract,
)
from sponsio.patterns import library

DOC = Path(__file__).resolve().parents[1] / "docs" / "reference" / "patterns.md"

# | `pattern(sig)` | <cell> | prose |
ROW = re.compile(r"^\|\s*`([a-z_0-9]+)\([^`]*\)`\s*\|\s*(.+?)\s*\|", re.M)
STRUCTURED = re.compile(r"^\{pattern:\s*([a-z_0-9]+)(?:,\s*args:\s*(.+))?\}$")


def _cells() -> list[tuple[str, str]]:
    text = DOC.read_text(encoding="utf-8")
    rows = [(pat, cell.strip("`")) for pat, cell in ROW.findall(text)]
    assert len(rows) > 30, f"catalog table not found or shrank: {len(rows)} rows"
    return rows


@pytest.mark.parametrize("pattern,cell", _cells(), ids=lambda v: str(v)[:40])
def test_catalog_example_is_usable(pattern: str, cell: str) -> None:
    """A cell is usable in one of the three forms the docs promise."""
    factory = getattr(library, pattern, None)
    assert factory is not None, (
        f"catalog names a pattern that does not exist: {pattern}"
    )

    structured = STRUCTURED.match(cell)
    if structured:
        # `{pattern: X, args: [...]}` goes into a yaml G:/A: field verbatim,
        # so it has to survive the CONFIG LOADER, not just the factory. The
        # loader resolves `pattern:` against `_PATTERN_REGISTRY`, and five
        # library factories were missing from it: a cell checked against the
        # factory alone passed while `sponsio.yaml` rejected the same line.
        name, raw_args = structured.group(1), structured.group(2)
        assert name == pattern, f"row {pattern} shows {name}"
        assert name in get_available_patterns(), (
            f"{name} exists in the library but not in the registry the yaml "
            f"loader reads, so `pattern: {name}` cannot be used in a config"
        )
        args = ast.literal_eval(raw_args) if raw_args else []
        getattr(library, name)(*args)
        return

    if cell.startswith(f"{pattern}("):
        # A Python call, for patterns whose arguments are atoms.
        eval(cell, {"__builtins__": {}, "Atom": Atom, pattern: factory})  # noqa: S307
        return

    # Otherwise a sentence, which must survive the parser a user would hit.
    parse_contract(cell.strip('"'))

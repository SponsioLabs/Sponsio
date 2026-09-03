"""Every bundle we ship must build a guard.

`include: sponsio:<bundle>` is the one-line path the README sells, so a
bundle that cannot compile is a rule that never fires for anyone who took
that line. `benchmark/tau2_bench` raised ConfigError at guard construction
for four contracts written in constructor-style LTL while the loader only
tried the infix parser. Nothing caught it because nothing had ever loaded
the shipped bundles the way a user does.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import sponsio
from sponsio.formulas.parser import ParseError, parse_formula, parse_repr

BUNDLES = Path(sponsio.__file__).parent / "contracts"


def _bundle_refs() -> list[str]:
    refs = [
        f"{p.parent.name}/{p.stem}"
        for p in sorted(BUNDLES.rglob("*.yaml"))
        if p.parent != BUNDLES
    ]
    assert len(refs) > 10, f"contract bundles not found: {refs}"
    return refs


@pytest.mark.parametrize("ref", _bundle_refs())
def test_a_shipped_bundle_builds_a_guard(ref: str, tmp_path: Path) -> None:
    config = tmp_path / "sponsio.yaml"
    # `workspace:` is documented as required for the path-scoping bundles
    # and the loader says so by name when it is missing, which is the
    # behaviour we want to keep. Supplying it here is the documented usage,
    # not a workaround.
    config.write_text(
        f'version: "1"\n'
        f"agents:\n"
        f"  bot:\n"
        f'    workspace: "{tmp_path}"\n'
        f"    include:\n"
        f"      - sponsio:{ref}\n"
    )
    # enforce, because that is where an uncompilable contract is fatal
    # rather than skipped with a warning. A bundle that only loads in
    # observe mode is a bundle that stops working the moment it matters.
    sponsio.Sponsio(config=str(config), agent_id="bot", mode="enforce", verbose=False)


def _ltl_strings() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    def walk(node, where: str) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("ltl"), str):
                out.append((where, node["ltl"]))
            for value in node.values():
                walk(value, where)
        elif isinstance(node, list):
            for value in node:
                walk(value, where)

    for path in sorted(BUNDLES.rglob("*.yaml")):
        walk(yaml.safe_load(path.read_text()), path.name)
    assert len(out) > 50, f"no ltl strings found: {len(out)}"
    return out


@pytest.mark.parametrize("where,ltl", _ltl_strings(), ids=lambda v: str(v)[:40])
def test_a_shipped_ltl_string_parses(where: str, ltl: str) -> None:
    """Either grammar is fine; neither is not. The loader tries infix and
    falls back to constructor style, so this must agree with it."""
    try:
        parse_repr(ltl)
    except ParseError:
        parse_formula(ltl)

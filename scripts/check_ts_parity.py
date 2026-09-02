#!/usr/bin/env python3
"""Compare the pattern descriptions Python and TypeScript emit.

Both runtimes name the same rule, and the console shows whichever one
wrote the trace. A wording that drifts on one side gives the same contract
two names — which is how "limited to 1 invocations" survived on the TS
side after Python was fixed.

This compares the desc TEMPLATES structurally: it strips the interpolation
syntax each language uses and compares what is left. It cannot catch a
difference inside an expression, and says so rather than implying it can.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = ROOT / "sponsio" / "patterns" / "library.py"
TS = ROOT / "ts" / "packages" / "sdk" / "src" / "core" / "patterns.ts"

# f"...{expr}..."  ->  the literal parts only
PY_DESC = re.compile(r'desc=desc or f"([^"]+)"')
# `...${expr}...`  ->  the literal parts only
# a TS template quotes identifiers with \` inside the template literal,
# so the closing backtick is the first UNESCAPED one
TS_DESC = re.compile(r"desc:\s*`((?:[^`\\\\]|\\\\.)*)`")


def _skeleton(template: str, hole: re.Pattern[str]) -> str:
    """The literal text with every interpolation replaced by a marker."""
    out = hole.sub("<>", template)
    out = out.replace("\\`", "`").replace('\\"', '"')
    return re.sub(r"\s+", " ", out).strip()


def main() -> int:
    py = {
        _skeleton(m.group(1), re.compile(r"\{[^}]*\}"))
        for m in PY_DESC.finditer(PY.read_text(encoding="utf-8"))
    }
    ts = {
        _skeleton(m.group(1), re.compile(r"\$\{[^}]*\}"))
        for m in TS_DESC.finditer(TS.read_text(encoding="utf-8"))
    }
    # TS wraps identifiers in backticks in its copy; normalise that away so
    # the comparison is about wording, not markup
    ts = {t.replace("`", "") for t in ts}
    py = {t.replace("`", "") for t in py}

    shared_shape = py & ts
    only_ts = sorted(t for t in ts - py)
    print(f"python templates : {len(py)}")
    print(f"ts templates     : {len(ts)}")
    print(f"identical wording: {len(shared_shape)}")

    # A TS template whose wording exists nowhere in Python is either a
    # TS-only pattern (fine) or drift (not). Print them for a human; do not
    # fail, because the TS surface is deliberately smaller.
    if only_ts:
        print("\nTS wording with no Python twin (check for drift):")
        for t in only_ts:
            print("  ", t[:96])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

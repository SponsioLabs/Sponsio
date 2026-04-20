#!/usr/bin/env python3
"""Regenerate ``llms-full.txt`` from the project's docs.

This is a write-only build script — it reads README + every file under
``docs/`` in a fixed order, concatenates them with machine-readable
separators, and writes the result to ``llms-full.txt`` at the repo
root.

``llms.txt`` (the short index) is maintained by hand because it
contains curated links and the value prop, neither of which we want
auto-generated. Re-run this script whenever any included doc changes::

    python scripts/build_llms_txt.py

The output conforms to the llms.txt convention from
https://llmstxt.org/ and is intended for LLM context ingestion (Cursor,
Claude Code, ChatGPT, etc.) — not for human rendering. Target budget is
<50k tokens; current build is comfortably under half of that.
"""

from __future__ import annotations

from pathlib import Path

# Ordered list of files to include. Order matters — earlier sections
# are more likely to survive truncation if an LLM can't fit the whole
# file in context. Keep README first (product pitch), then user-facing
# docs, then architecture / internals.
FILES = [
    "README.md",
    "docs/getting-started.md",
    "docs/contracts.md",
    "docs/input-formats.md",
    "docs/integrations.md",
    "docs/cli.md",
    "docs/architecture.md",
    "docs/benchmark-results.md",
    "CHANGELOG.md",
]

HEADER = (
    "# Sponsio — Full Documentation Dump\n\n"
    "This file is the concatenation of every public-facing doc in the\n"
    "repository, produced for LLM context ingestion. Canonical sources\n"
    "live under `docs/` in the repo root. Each section starts with a\n"
    "machine-readable `<!-- FILE: path -->` separator so downstream\n"
    "tools can split if needed.\n"
)

SEP_TEMPLATE = (
    "\n\n<!-- ====================================================================== -->\n"
    "<!-- FILE: {path}  -->\n"
    "<!-- ====================================================================== -->\n\n"
)


def build(root: Path, out_path: Path) -> None:
    parts: list[str] = [HEADER]
    for rel in FILES:
        path = root / rel
        parts.append(SEP_TEMPLATE.format(path=rel))
        if not path.exists():
            parts.append(f"<!-- MISSING: {rel} -->\n")
            continue
        parts.append(path.read_text())
    out_path.write_text("".join(parts))


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    out = root / "llms-full.txt"
    build(root, out)
    n_bytes = out.stat().st_size
    n_lines = sum(1 for _ in out.open())
    print(f"Wrote {out.relative_to(root)} — {n_bytes:,} bytes, {n_lines:,} lines")

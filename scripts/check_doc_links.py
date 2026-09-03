#!/usr/bin/env python3
"""Every relative link in the docs points at a file that exists.

Two files were renamed and ten references were not, including the one on
`docs/index.md` that is the docs home page's primary call to action. A
reader following the site's own front door got a 404, and nothing in the
build noticed.

Only relative links to files inside the repo are checked. External URLs,
anchors, and mailto: are out of scope — this catches renames, which is
the failure that actually happened.

    python scripts/check_doc_links.py          # repo docs + top-level md
    python scripts/check_doc_links.py docs/    # a subtree
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# [text](target) — captures the target, minus any #anchor or ?query.
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

SKIP_PREFIXES = ("http://", "https://", "mailto:", "#", "tel:")


def strip_code(text: str) -> str:
    """Blank out fenced and inline code, keeping line numbers intact.

    `registry[tool](**args)` inside a python block is not a link, and
    reading it as one puts noise in front of the real breakage.
    """
    out, fenced = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            out.append("")
            continue
        out.append("" if fenced else re.sub(r"`[^`]*`", "", line))
    return "\n".join(out)


def targets(text: str):
    text = strip_code(text)
    for match in LINK.finditer(text):
        target = match.group(1)
        if target.startswith(SKIP_PREFIXES):
            continue
        # Strip an anchor; a link to a section of a real file is fine.
        target = target.split("#", 1)[0].split("?", 1)[0]
        if target:
            yield target, text[: match.start()].count("\n") + 1


def check(roots: list[Path]) -> list[tuple[Path, int, str]]:
    broken: list[tuple[Path, int, str]] = []
    for root in roots:
        files = [root] if root.is_file() else sorted(root.rglob("*.md"))
        for path in files:
            if "node_modules" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for target, line in targets(text):
                if not (path.parent / target).resolve().exists():
                    broken.append((path, line, target))
    return broken


def main(argv: list[str]) -> int:
    repo = Path(__file__).resolve().parent.parent
    if len(argv) > 1:
        roots = [Path(a) for a in argv[1:]]
    else:
        roots = [repo / "docs"] + sorted(repo.glob("*.md"))
    roots = [r for r in roots if r.exists()]

    broken = check(roots)
    if not broken:
        checked = sum(
            1 for r in roots for _ in ([r] if r.is_file() else r.rglob("*.md"))
        )
        print(f"all relative doc links resolve ({checked} file(s) checked)")
        return 0

    print(f"{len(broken)} broken relative link(s):", file=sys.stderr)
    for path, line, target in broken:
        rel = path.relative_to(repo) if repo in path.parents else path
        print(f"  {rel}:{line} -> {target}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

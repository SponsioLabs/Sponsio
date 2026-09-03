"""`pip install sponsio` resolves to 0.1.1, a different and much older
release, because 0.2.0a* is a pre-release. Every install instruction we
ship therefore has to carry `--pre`, including the README badge, which is
the first thing on the page and the easiest one to forget.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    *[
        ROOT / n
        for n in ("README.md", "README.zh-CN.md", "README.ja.md", "QUICKSTART.md")
    ],
    *sorted((ROOT / "docs").rglob("*.md")),
]

# Release notes explain the plain form on purpose; install.md does too.
EXPLAINS_THE_PLAIN_FORM = {"install.md"}

# Capture the whole flag run so `--pre` is found wherever it sits. A
# lookahead pinned to the first flag passed `pip install -U --pre sponsio`,
# because it only ever saw the `-U`.
INSTALL = re.compile(r"pip install\s+((?:-[a-zA-Z-]+\s+)*)[\"']?sponsio")


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_install_instructions_carry_pre(path: Path) -> None:
    if path.name in EXPLAINS_THE_PLAIN_FORM or "release-notes" in path.parts:
        pytest.skip("documents the plain form deliberately")
    bad = [
        ln
        for ln in path.read_text().splitlines()
        for m in INSTALL.finditer(ln)
        if "--pre" not in m.group(1)
    ]
    assert not bad, f"{path.name}: install line without --pre:\n" + "\n".join(bad)


def test_the_readme_badge_carries_pre() -> None:
    """URL-encoded, so the regex above cannot see it."""
    for name in ("README.md", "README.zh-CN.md", "README.ja.md"):
        badge = re.search(
            r"img\.shields\.io/badge/install-([^\"?]*)", (ROOT / name).read_text()
        )
        assert badge, f"{name}: install badge missing"
        assert "--pre" in badge.group(1), f"{name}: badge says {badge.group(1)}"

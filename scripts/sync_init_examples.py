#!/usr/bin/env python3
"""Mirror ``examples/eval/`` → ``sponsio/init_examples/eval/``.

Run after editing anything under ``examples/eval/`` so the bundled
copy that ``sponsio init --with-example`` reads stays in sync.

    python scripts/sync_init_examples.py

The CI guard (``tests/test_init_examples_sync.py``) fails the build
if the two trees diverge, so forgetting this step won't slip past
review — but running it locally avoids the round-trip.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "examples" / "eval"
DST = REPO_ROOT / "sponsio" / "init_examples" / "eval"


def main() -> int:
    if not SRC.exists():
        print(f"error: source {SRC} does not exist", file=sys.stderr)
        return 1

    if DST.exists():
        shutil.rmtree(DST)
    # Copy everything except the generator script and any __pycache__:
    # the generator is a dev tool, not user-facing scaffolding.
    shutil.copytree(
        SRC,
        DST,
        ignore=shutil.ignore_patterns(
            "generate_corpus.py",
            "__pycache__",
            "*.pyc",
        ),
    )
    print(f"Synced {SRC.relative_to(REPO_ROOT)} → {DST.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

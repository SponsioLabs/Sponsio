#!/usr/bin/env python3
"""Mirror the built ``plugins/sponsio-openclaw/`` artifact into
``sponsio/plugin/openclaw_artifact/`` so the Sponsio Python wheel
ships a pre-built copy that ``sponsio host install openclaw``
can deploy without requiring node/npm/tsc on the user's machine.

Run after rebuilding the plugin's TypeScript:

    cd plugins/sponsio-openclaw
    npm install && npm run build
    cd ../..
    python scripts/sync_openclaw_artifact.py

The CI guard (``tests/test_openclaw_artifact_sync.py``) fails the
build if the two trees diverge, so forgetting this step won't slip
past review — but running it locally avoids the round-trip.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "plugins" / "sponsio-openclaw"
DST_ROOT = REPO_ROOT / "sponsio" / "plugin" / "openclaw_artifact"

# Files we sync from the canonical TS plugin into the wheel artifact.
# The wheel only needs the runtime files (manifest + dist + a
# minimal package.json with the openclaw.extensions entry pointer).
# We explicitly do NOT sync ``package.json`` — the wheel uses a
# slimmed version (no devDependencies, no scripts).  See
# ``DST_ROOT/package.json``.
_FILES_TO_SYNC = [
    "openclaw.plugin.json",
    "dist/index.js",
    "dist/index.d.ts",
]


def main() -> int:
    if not SRC_ROOT.exists():
        print(f"error: source {SRC_ROOT} does not exist", file=sys.stderr)
        return 1

    dist_dir = SRC_ROOT / "dist"
    if not dist_dir.exists() or not (dist_dir / "index.js").exists():
        print(
            f"error: {dist_dir}/index.js missing.  Run "
            "`cd plugins/sponsio-openclaw && npm install && npm run build` "
            "first.",
            file=sys.stderr,
        )
        return 1

    DST_ROOT.mkdir(parents=True, exist_ok=True)
    (DST_ROOT / "dist").mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for rel in _FILES_TO_SYNC:
        src = SRC_ROOT / rel
        dst = DST_ROOT / rel
        if not src.exists():
            print(f"error: {src} missing — build the plugin first", file=sys.stderr)
            return 1
        shutil.copyfile(src, dst)
        copied.append(rel)

    print(f"synced {len(copied)} files from {SRC_ROOT} → {DST_ROOT}:")
    for rel in copied:
        print(f"  • {rel}")
    print(
        "\nNote: package.json is NOT synced — the wheel uses a slim version. "
        "Bump version manually in both files when releasing."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

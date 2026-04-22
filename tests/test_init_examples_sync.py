"""Sync guard for the bundled ``init_examples/eval`` mirror.

Two on-disk copies of the eval scaffolding exist:
  * ``examples/eval/`` — repo root, what contributors edit
  * ``sponsio/init_examples/eval/`` — inside the package, what
    ``pip install`` ships and ``sponsio init --with-example`` reads

A divergence between them is silent and nasty: the README and demo
docs reference the repo copy, but installed users get the (stale)
package copy.  This test fails the build any time they drift, with
a one-line fix instruction.

Re-syncing is a single command:

    python scripts/sync_init_examples.py
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "examples" / "eval"
DST = REPO_ROOT / "sponsio" / "init_examples" / "eval"


def _bundle_files(root: Path) -> dict[str, bytes]:
    """Return ``{relative_path: content}``, skipping the dev-only
    generator script and any cache crud.

    The generator lives only in the repo copy because it's a
    contributor tool — shipping it inside the wheel would be noise.
    """
    out: dict[str, bytes] = {}
    for p in root.rglob("*"):
        if p.is_dir():
            continue
        if p.name == "generate_corpus.py":
            continue
        if "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        rel = p.relative_to(root).as_posix()
        out[rel] = p.read_bytes()
    return out


def test_repo_and_package_examples_are_identical():
    src = _bundle_files(SRC)
    dst = _bundle_files(DST)
    if src != dst:
        only_src = sorted(set(src) - set(dst))
        only_dst = sorted(set(dst) - set(src))
        differ = sorted(k for k in src.keys() & dst.keys() if src[k] != dst[k])
        msg = ["examples/eval/ and sponsio/init_examples/eval/ are out of sync."]
        if only_src:
            msg.append(f"  Only in repo:    {only_src}")
        if only_dst:
            msg.append(f"  Only in package: {only_dst}")
        if differ:
            msg.append(f"  Content differs: {differ}")
        msg.append("Run: python scripts/sync_init_examples.py")
        raise AssertionError("\n".join(msg))

"""Sync guard for the bundled OpenClaw plugin artifact.

Two on-disk copies of the built plugin exist:

  * ``plugins/sponsio-openclaw/`` — canonical TS source + the
    npm-built ``dist/index.js``; what contributors edit.
  * ``sponsio/plugin/openclaw_artifact/`` — inside the package; what
    ``pip install`` ships and ``sponsio host install openclaw``
    deploys to ``~/.openclaw/extensions/sponsio-openclaw/``.

If they drift, ``pip``-installed users get a stale plugin while
source-checkout users get the fresh one — silent and confusing.
This test fails the build any time they diverge, with a one-line
fix instruction.

Re-syncing is two commands:

    cd plugins/sponsio-openclaw && npm install && npm run build
    python scripts/sync_openclaw_artifact.py
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "plugins" / "sponsio-openclaw"
DST_ROOT = REPO_ROOT / "sponsio" / "plugin" / "openclaw_artifact"

# Mirror the list in ``scripts/sync_openclaw_artifact.py``.  Keep
# in lockstep — if a file is added there, add it here too.
_SYNCED_FILES = [
    "openclaw.plugin.json",
    "dist/index.js",
    "dist/index.d.ts",
]


def test_runtime_artifact_matches_canonical_plugin():
    """Every file the sync script copies must be byte-identical."""
    diffs: list[str] = []
    missing: list[str] = []
    for rel in _SYNCED_FILES:
        src = SRC_ROOT / rel
        dst = DST_ROOT / rel
        if not src.exists():
            missing.append(f"  source missing: {src}")
            continue
        if not dst.exists():
            missing.append(f"  bundled missing: {dst}")
            continue
        if src.read_bytes() != dst.read_bytes():
            diffs.append(f"  differs: {rel}")

    if missing or diffs:
        msg = [
            "plugins/sponsio-openclaw/ and sponsio/plugin/openclaw_artifact/ "
            "are out of sync."
        ]
        msg.extend(missing)
        msg.extend(diffs)
        msg.append(
            "\n  Fix:\n"
            "    cd plugins/sponsio-openclaw && npm install && npm run build\n"
            "    cd ../.. && python scripts/sync_openclaw_artifact.py"
        )
        raise AssertionError("\n".join(msg))


def test_artifact_package_json_declares_extension_entry():
    """The slim package.json bundled with the wheel must declare
    ``openclaw.extensions = ["./dist/index.js"]`` — that's what
    OpenClaw's plugin discovery reads to find our entry point."""
    import json

    pkg_path = DST_ROOT / "package.json"
    assert pkg_path.exists(), f"missing {pkg_path}"
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))

    extensions = pkg.get("openclaw", {}).get("extensions")
    assert isinstance(extensions, list) and extensions, (
        f"{pkg_path}: 'openclaw.extensions' must be a non-empty list; "
        f"OpenClaw won't load the plugin without it"
    )
    assert "./dist/index.js" in extensions, (
        f"{pkg_path}: 'openclaw.extensions' must include './dist/index.js'; "
        f"got {extensions}"
    )


def test_artifact_manifest_id_matches_install_constant():
    """The plugin id in the manifest must match the constant the
    installer uses to register / look up the plugin in
    ``openclaw.json``.  A drift here means install would write the
    plugin under one id and OpenClaw would look for it under
    another."""
    import json

    from sponsio.integrations import openclaw_install

    manifest = json.loads(
        (DST_ROOT / "openclaw.plugin.json").read_text(encoding="utf-8")
    )
    assert manifest.get("id") == openclaw_install._PLUGIN_ID, (
        f"manifest id={manifest.get('id')!r} but installer uses "
        f"{openclaw_install._PLUGIN_ID!r} — keep them in sync"
    )

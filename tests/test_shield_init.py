"""Tests for ``sponsio shield init`` and shield default-library packaging.

The init command is a one-shot bootstrap that:

1. Resolves a target root (``--root`` flag → ``$SPONSIO_PLUGIN_ROOT`` →
   ``~/.sponsio/plugins``).
2. Copies the bundled ``sponsio/shield/defaults/_host.yaml`` into
   ``<root>/_host/sponsio.yaml``.
3. Runs a smoke test against the in-process hook entry point — both
   the allow path and the block path must work, otherwise the install
   is broken (mismatched CLI version, malformed library, missing
   capability/shell pack, …) and we fail loudly so the user catches
   it before relying on it.

The packaging-sync test guards against drift between the package
data shipped to pip users and the source-checkout copy under
``plugins/sponsio-shield/libraries/_host/sponsio.yaml`` — they must
stay byte-identical.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Sync invariant: bundled package data == plugin checkout copy
# ---------------------------------------------------------------------------


def test_shield_default_host_matches_plugin_checkout():
    """``sponsio/shield/defaults/_host.yaml`` and the plugin's checkout copy
    at ``plugins/sponsio-shield/libraries/_host/sponsio.yaml`` must be
    byte-identical.

    The two exist for different audiences: the package-data version is
    what ``shield init`` copies for pip-installed users; the plugin
    checkout version is what ``--plugin-dir`` users cp from. If they
    drift, half the install paths get the old library.
    """
    pkg = REPO_ROOT / "sponsio" / "shield" / "defaults" / "_host.yaml"
    plugin = (
        REPO_ROOT
        / "plugins"
        / "sponsio-shield"
        / "libraries"
        / "_host"
        / "sponsio.yaml"
    )
    assert pkg.exists(), f"missing package default at {pkg}"
    assert plugin.exists(), f"missing plugin copy at {plugin}"
    assert pkg.read_bytes() == plugin.read_bytes(), (
        "shield default _host.yaml drifted between package-data "
        "and plugin checkout — keep them identical, or have one cp "
        "from the other in a build step."
    )


# ---------------------------------------------------------------------------
# Init writes the file + smoke test passes
# ---------------------------------------------------------------------------


def _run_init(tmp_path: Path, *extra_args: str) -> subprocess.CompletedProcess:
    """Invoke `sponsio shield init` in a subprocess so we exercise the real
    CLI dispatch + entry-point wiring, not the click runner alone.
    """
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "sponsio.cli",
            "shield",
            "init",
            "--root",
            str(tmp_path),
            *extra_args,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_init_writes_default_host_library(tmp_path):
    proc = _run_init(tmp_path)
    assert proc.returncode == 0, (
        f"init exited {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )

    target = tmp_path / "_host" / "sponsio.yaml"
    assert target.exists()

    pkg = REPO_ROOT / "sponsio" / "shield" / "defaults" / "_host.yaml"
    assert target.read_bytes() == pkg.read_bytes()


def test_init_runs_smoke_test_by_default(tmp_path):
    proc = _run_init(tmp_path)
    assert proc.returncode == 0
    assert "smoke test: allow + block both work" in proc.stdout


def test_init_skip_smoke_test_with_flag(tmp_path):
    proc = _run_init(tmp_path, "--no-smoke-test")
    assert proc.returncode == 0
    assert "smoke test" not in proc.stdout.lower() or "Skipped" in proc.stdout


def test_init_refuses_overwrite_without_force(tmp_path):
    target = tmp_path / "_host" / "sponsio.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("# user-customised content")

    proc = _run_init(tmp_path)
    assert proc.returncode == 0
    assert "already exists" in proc.stdout
    # Content unchanged — we did not silently clobber the user's file.
    assert target.read_text() == "# user-customised content"


def test_init_force_overwrites(tmp_path):
    target = tmp_path / "_host" / "sponsio.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("# user-customised content")

    proc = _run_init(tmp_path, "--force")
    assert proc.returncode == 0
    pkg = REPO_ROOT / "sponsio" / "shield" / "defaults" / "_host.yaml"
    assert target.read_bytes() == pkg.read_bytes()


# ---------------------------------------------------------------------------
# Library is functionally correct (caught by smoke test in init, but
# verified explicitly here so a regression in the library shape is not
# attributed to init plumbing)
# ---------------------------------------------------------------------------


def test_default_library_blocks_rm_rf(tmp_path, monkeypatch):
    """The shipped ``_host`` library must produce a deny on ``rm -rf /``.

    Independent of init — checks the YAML itself round-trips through
    BaseGuard correctly, so a future change to ``capability/shell``
    that breaks the host-rename mapping fails this test.
    """
    pkg = REPO_ROOT / "sponsio" / "shield" / "defaults" / "_host.yaml"
    target = tmp_path / "_host" / "sponsio.yaml"
    target.parent.mkdir(parents=True)
    target.write_bytes(pkg.read_bytes())

    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))

    from sponsio.guard_stdin import evaluate_event

    blocked = evaluate_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"},
        }
    )
    assert blocked.allowed is False
    assert blocked.plugin_id == "_host"

    allowed = evaluate_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "echo hello"},
        }
    )
    assert allowed.allowed is True

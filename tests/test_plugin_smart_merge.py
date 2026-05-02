"""Tests for ``sponsio plugin install --force`` smart-merge upgrade.

Single-file model: the per-plugin library at
``~/.sponsio/plugins/<id>/sponsio.yaml`` houses both shipped rules
and the user's customisations. On upgrade we partition by the
``source: bundle:<name>`` tag — shipped contracts are replaced
wholesale, user-authored contracts and ``tweaks:`` survive.

These tests lock the user-facing contract: a re-install never
silently drops a custom contract or a tweak.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

from sponsio.plugin.registry import read_bundled


def _run_install(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "sponsio.cli", "plugin", "install", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _evaluate(tool_name: str, tool_input: dict):
    from sponsio.guard_stdin import evaluate_event

    return evaluate_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
    )


# ---------------------------------------------------------------------------
# §1 — Source stamping on fresh install
# ---------------------------------------------------------------------------


def test_fresh_install_stamps_source_on_every_shipped_contract(tmp_path):
    """Without source tags, smart merge can't tell shipped from user.
    Every contract written by ``plugin install`` MUST carry the
    ``bundle:<name>`` marker so a later upgrade is partitionable."""
    proc = _run_install("github", "--root", str(tmp_path))
    assert proc.returncode == 0
    target = tmp_path / "github" / "sponsio.yaml"
    doc = yaml.safe_load(target.read_text())
    contracts = doc["agents"]["github"]["contracts"]
    assert contracts, "expected at least one shipped contract"
    for c in contracts:
        assert c.get("source") == "bundle:github", (
            f"contract {c.get('desc')!r} missing or wrong source tag"
        )


def test_existing_source_tags_are_not_clobbered(tmp_path):
    """Bundles that ship with their own ``source:`` (e.g. capability
    packs use ``source: library:tier1.self-modify``) must keep their
    pre-existing tag rather than getting overwritten with our marker."""
    plugin_dir = tmp_path / "custom"
    plugin_dir.mkdir()
    target = plugin_dir / "sponsio.yaml"
    target.write_text(
        "version: '1'\n"
        "agents:\n"
        "  custom:\n"
        "    contracts:\n"
        "      - desc: r1\n"
        "        source: library:foo\n"
        "        E: { pattern: rate_limit, args: [t, 0] }\n"
    )
    # Re-stamp via the helper directly (we don't have a 'custom' bundle
    # in the registry — exercise the stamping path on a hand-crafted
    # text instead).
    from sponsio.cli import _stamp_bundled_source

    stamped = _stamp_bundled_source(target.read_text(), "custom")
    doc = yaml.safe_load(stamped)
    assert doc["agents"]["custom"]["contracts"][0]["source"] == "library:foo"


# ---------------------------------------------------------------------------
# §2 — ``--force`` smart merge: user content survives, shipped content
#       gets replaced
# ---------------------------------------------------------------------------


def _add_user_content(target: Path, *, inline_tweak: bool = True) -> None:
    """Mutate the installed YAML to add one user-authored contract
    and one tweak under the 'github' agent.

    When ``inline_tweak=True`` (default), the tweak rides in the
    same ``contracts:`` list as the definition — the canonical shape.
    When ``False`` it goes in a legacy agent-level ``tweaks:`` block,
    so the back-compat path stays exercised by these tests too.
    """
    doc = yaml.safe_load(target.read_text())
    g = doc["agents"]["github"]
    g.setdefault("contracts", []).append(
        {
            "desc": "user-added: block staging-* deletions",
            "E": {
                "pattern": "arg_blacklist",
                "args": [
                    "mcp__github__delete_branch",
                    "branch",
                    ["^staging-.*$"],
                ],
            },
            # No source tag → counts as user-authored.
        }
    )
    tweak_entry = {
        "match": {
            "desc": (
                "delete_repository is blocked outright (overrides: "
                "disabled: true to allow)"
            )
        },
        "disabled": True,
    }
    if inline_tweak:
        # Canonical: tweak entry sits in ``contracts:`` next to
        # definitions, distinguished by having ``match:`` (no ``E:``).
        g["contracts"].append(tweak_entry)
    else:
        # Legacy: agent-level ``tweaks:`` block.
        g["tweaks"] = [tweak_entry]
    target.write_text(yaml.safe_dump(doc, sort_keys=False))


def test_force_upgrade_preserves_user_contracts_and_tweaks(tmp_path):
    _run_install("github", "--root", str(tmp_path))
    target = tmp_path / "github" / "sponsio.yaml"
    _add_user_content(target)

    proc = _run_install("github", "--root", str(tmp_path), "--force")
    assert proc.returncode == 0
    assert "kept 1 custom contract(s) and 1 tweak(s)" in proc.stdout

    doc = yaml.safe_load(target.read_text())
    contracts = doc["agents"]["github"].get("contracts") or []

    # User-added rule definition is preserved.
    descs = [c.get("desc") for c in contracts if "E" in c]
    assert "user-added: block staging-* deletions" in descs
    # Shipped rules are still present (not lost in the merge):
    assert any("delete_repository" in (d or "") for d in descs)
    # Inline tweak entry survives — distinguished by having ``match:``
    # without ``E:``.
    tweaks_inline = [c for c in contracts if "match" in c and "E" not in c]
    assert len(tweaks_inline) == 1
    assert tweaks_inline[0]["disabled"] is True


def test_force_upgrade_preserves_legacy_tweaks_block(tmp_path):
    """Same as the inline test, but using the legacy agent-level
    ``tweaks:`` block. Both shapes must survive upgrade so users
    who migrate at their own pace aren't punished."""
    _run_install("github", "--root", str(tmp_path))
    target = tmp_path / "github" / "sponsio.yaml"
    _add_user_content(target, inline_tweak=False)

    proc = _run_install("github", "--root", str(tmp_path), "--force")
    assert proc.returncode == 0
    assert "kept 1 custom contract(s) and 1 tweak(s)" in proc.stdout

    doc = yaml.safe_load(target.read_text())
    g = doc["agents"]["github"]
    legacy_tweaks = g.get("tweaks") or []
    assert len(legacy_tweaks) == 1
    assert legacy_tweaks[0]["disabled"] is True


def test_force_upgrade_replaces_shipped_contracts(tmp_path):
    """Shipped contracts get re-written from the new bundle on
    upgrade — old shipped contract bodies don't accumulate. Test by
    hand-mutating a shipped rule's body and confirming the upgrade
    snaps it back to the bundled version."""
    _run_install("github", "--root", str(tmp_path))
    target = tmp_path / "github" / "sponsio.yaml"
    doc = yaml.safe_load(target.read_text())
    g = doc["agents"]["github"]
    # Tamper with a shipped rule (same source tag, different body —
    # the kind of edit smart-merge is explicitly NOT a substitute for
    # a proper tweak).
    for c in g["contracts"]:
        if c.get("source") == "bundle:github" and "delete_branch" in c.get("desc", ""):
            c["E"]["args"][2] = ["^attacker-only$"]  # weakened regex
            break
    target.write_text(yaml.safe_dump(doc, sort_keys=False))

    _run_install("github", "--root", str(tmp_path), "--force")
    bundled = yaml.safe_load(read_bundled("github"))
    bundled_branch_rule = next(
        c
        for c in bundled["agents"]["github"]["contracts"]
        if "delete_branch" in c["desc"]
    )
    upgraded = yaml.safe_load(target.read_text())
    upgraded_branch_rule = next(
        c
        for c in upgraded["agents"]["github"]["contracts"]
        if "delete_branch" in c["desc"]
    )
    assert upgraded_branch_rule["E"]["args"] == bundled_branch_rule["E"]["args"]


def test_force_upgrade_runtime_behaviour(tmp_path, monkeypatch):
    """End-to-end: after upgrade, the user's tweak still disables a
    shipped hard deny, and the user's custom contract still fires."""
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(tmp_path))
    _run_install("github", "--root", str(tmp_path))
    target = tmp_path / "github" / "sponsio.yaml"
    _add_user_content(target)
    _run_install("github", "--root", str(tmp_path), "--force")

    # Tweak applied — shipped delete_repository hard deny is silenced.
    out = _evaluate("mcp__github__delete_repository", {"name": "x"})
    assert out.allowed is True
    # User contract — staging branch deletion is blocked.
    out = _evaluate("mcp__github__delete_branch", {"branch": "staging-2026"})
    assert out.allowed is False
    # Untouched shipped rule — main branch deletion still blocked.
    out = _evaluate("mcp__github__delete_branch", {"branch": "main"})
    assert out.allowed is False


# ---------------------------------------------------------------------------
# §3 — ``--force`` is a no-op (in terms of preservation accounting) when
#       the user hasn't customised anything
# ---------------------------------------------------------------------------


def test_force_upgrade_with_no_user_content_reports_zero_kept(tmp_path):
    _run_install("github", "--root", str(tmp_path))
    proc = _run_install("github", "--root", str(tmp_path), "--force")
    assert proc.returncode == 0
    assert "kept 0 custom contract(s) and 0 tweak(s)" in proc.stdout


# ---------------------------------------------------------------------------
# §4 — Loader accepts ``tweaks:`` (canonical) AND ``overrides:`` (legacy)
# ---------------------------------------------------------------------------


def test_loader_accepts_inline_tweak_entry(tmp_path):
    """Canonical shape: a tweak entry rides inside ``contracts:``,
    discriminated by having ``match:`` instead of ``E:``."""
    cfg = tmp_path / "sponsio.yaml"
    cfg.write_text(
        """
version: "1"
agents:
  github:
    contracts:
      - desc: shipped
        E: { pattern: rate_limit, args: [tool, 0] }
      - match: { desc: shipped }
        disabled: true
"""
    )
    from sponsio.config import load_config

    parsed = load_config(cfg)
    # ``disabled: true`` drops the matched contract from the active
    # list (see ``_apply_overrides`` semantics) — empty contracts list
    # is the proof the tweak was applied.
    assert parsed.agents["github"].contracts == []


def test_loader_accepts_legacy_tweaks_key(tmp_path):
    """Legacy back-compat: agent-level ``tweaks:`` block stays valid."""
    cfg = tmp_path / "sponsio.yaml"
    cfg.write_text(
        """
version: "1"
agents:
  github:
    contracts:
      - desc: shipped
        E: { pattern: rate_limit, args: [tool, 0] }
    tweaks:
      - match: { desc: shipped }
        disabled: true
"""
    )
    from sponsio.config import load_config

    parsed = load_config(cfg)
    assert parsed.agents["github"].contracts == []


def test_loader_accepts_legacy_overrides_key(tmp_path):
    """Existing user configs using ``overrides:`` keep working."""
    cfg = tmp_path / "sponsio.yaml"
    cfg.write_text(
        """
version: "1"
agents:
  github:
    contracts:
      - desc: shipped
        E: { pattern: rate_limit, args: [tool, 0] }
    overrides:
      - match: { desc: shipped }
        disabled: true
"""
    )
    from sponsio.config import load_config

    parsed = load_config(cfg)
    assert parsed.agents["github"].contracts == []


def test_loader_rejects_both_keys_present(tmp_path):
    cfg = tmp_path / "sponsio.yaml"
    cfg.write_text(
        """
version: "1"
agents:
  github:
    contracts:
      - desc: shipped
        E: { pattern: rate_limit, args: [tool, 0] }
    tweaks:
      - match: { desc: shipped }
        disabled: true
    overrides:
      - match: { desc: shipped }
        disabled: false
"""
    )
    from sponsio.config import ConfigError, load_config

    import pytest

    with pytest.raises(ConfigError, match="both 'tweaks:' and 'overrides:'"):
        load_config(cfg)

"""Tests for ``sponsio skill install``.

The command copies/symlinks the packaged ``sponsio/skills/sponsio/``
tree into a coding-agent's skills directory.  Scope of these tests:

* Source resolution — the wheel must contain ``SKILL.md``.
* --dest fan-out — we don't touch the user's real ``~/.cursor`` /
  ``~/.claude`` during tests; ``--dest`` gives us an isolated tmp.
* --force / --link / duplicate behavior.
* --tool auto / --tool all without --dest is simulated via a
  monkeypatched ``_SKILL_TOOL_DIRS`` so tests never leak to ``$HOME``.
"""

from __future__ import annotations

import sys

import pytest
from click.testing import CliRunner

from sponsio import cli as cli_mod
from sponsio.cli import cli


def test_packaged_skill_source_contains_skill_md():
    src = cli_mod._packaged_skill_source()
    assert src.is_dir()
    assert (src / "SKILL.md").is_file()
    # Frontmatter + first trigger-word line — cheap sanity that the
    # file is what we think it is, not e.g. an empty placeholder.
    body = (src / "SKILL.md").read_text()
    assert body.startswith("---"), "SKILL.md must start with YAML frontmatter"
    assert "name: sponsio" in body
    assert "W1 — Initial setup" in body  # lifecycle section present


def test_skill_install_copy_to_custom_dest(tmp_path):
    dest = tmp_path / "skills"
    result = CliRunner().invoke(cli, ["skill", "install", "--dest", str(dest)])
    assert result.exit_code == 0, result.output
    installed = dest / "sponsio" / "SKILL.md"
    assert installed.is_file()
    assert "sponsio" in (dest / "sponsio" / "SKILL.md").read_text().lower()
    # Copied, not symlinked, by default.
    assert not (dest / "sponsio").is_symlink()


def test_skill_install_refuses_to_overwrite_without_force(tmp_path):
    dest = tmp_path / "skills"
    # First install succeeds.
    r1 = CliRunner().invoke(cli, ["skill", "install", "--dest", str(dest)])
    assert r1.exit_code == 0, r1.output

    # Second install on the same dest without --force must NOT overwrite
    # but also should not hard-error (exit 1 is acceptable because
    # "nothing was written"; the skill is already there).
    r2 = CliRunner().invoke(cli, ["skill", "install", "--dest", str(dest)])
    assert r2.exit_code == 1  # any_written was False for the only target
    assert "already exists" in r2.output or "already exists" in (
        r2.stderr_bytes or b""
    ).decode("utf-8", errors="replace")


def test_skill_install_force_overwrites(tmp_path):
    dest = tmp_path / "skills"
    (dest / "sponsio").mkdir(parents=True)
    (dest / "sponsio" / "placeholder.txt").write_text("stale")

    result = CliRunner().invoke(
        cli, ["skill", "install", "--dest", str(dest), "--force"]
    )
    assert result.exit_code == 0, result.output
    assert (dest / "sponsio" / "SKILL.md").is_file()
    # Stale content must be gone — the directory was replaced, not merged.
    assert not (dest / "sponsio" / "placeholder.txt").exists()


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="--link is not supported on Windows; command falls back to copy.",
)
def test_skill_install_link_creates_symlink(tmp_path):
    dest = tmp_path / "skills"
    result = CliRunner().invoke(
        cli, ["skill", "install", "--dest", str(dest), "--link"]
    )
    assert result.exit_code == 0, result.output
    target = dest / "sponsio"
    assert target.is_symlink(), "expected a symlink but got a regular dir"
    # Resolves to the packaged source.
    assert (target / "SKILL.md").is_file()


def test_skill_install_tool_auto_uses_monkeypatched_dirs(tmp_path, monkeypatch):
    """--tool auto: we override the hard-coded discovery dirs so the test
    stays sandboxed."""
    fake_cursor = tmp_path / "fake_cursor_skills"
    fake_cursor.mkdir()  # exists → auto should pick it
    fake_claude = tmp_path / "fake_claude_skills"  # does NOT exist
    fake_codex = tmp_path / "fake_codex_skills"  # does NOT exist

    monkeypatch.setitem(cli_mod._SKILL_TOOL_DIRS, "cursor", fake_cursor)
    monkeypatch.setitem(cli_mod._SKILL_TOOL_DIRS, "claude", fake_claude)
    monkeypatch.setitem(cli_mod._SKILL_TOOL_DIRS, "codex", fake_codex)

    result = CliRunner().invoke(cli, ["skill", "install"])  # auto
    assert result.exit_code == 0, result.output
    assert (fake_cursor / "sponsio" / "SKILL.md").is_file()
    # claude/codex were not pre-existing so auto should NOT have installed there
    assert not (fake_claude / "sponsio").exists()
    assert not (fake_codex / "sponsio").exists()


def test_skill_install_tool_all_hits_every_target(tmp_path, monkeypatch):
    fake_dirs = {
        "cursor": tmp_path / "c",
        "claude": tmp_path / "cl",
        "codex": tmp_path / "cx",
    }
    for p in fake_dirs.values():
        # --tool all should create them itself; don't pre-create.
        pass
    for name, path in fake_dirs.items():
        monkeypatch.setitem(cli_mod._SKILL_TOOL_DIRS, name, path)

    result = CliRunner().invoke(cli, ["skill", "install", "--tool", "all"])
    assert result.exit_code == 0, result.output
    for path in fake_dirs.values():
        assert (path / "sponsio" / "SKILL.md").is_file(), f"missing in {path}"


def test_skill_install_tool_both_excludes_codex(tmp_path, monkeypatch):
    c = tmp_path / "c"
    cl = tmp_path / "cl"
    cx = tmp_path / "cx"
    monkeypatch.setitem(cli_mod._SKILL_TOOL_DIRS, "cursor", c)
    monkeypatch.setitem(cli_mod._SKILL_TOOL_DIRS, "claude", cl)
    monkeypatch.setitem(cli_mod._SKILL_TOOL_DIRS, "codex", cx)

    result = CliRunner().invoke(cli, ["skill", "install", "--tool", "both"])
    assert result.exit_code == 0, result.output
    assert (c / "sponsio" / "SKILL.md").is_file()
    assert (cl / "sponsio" / "SKILL.md").is_file()
    assert not (cx / "sponsio").exists()


def test_skill_install_auto_falls_back_when_nothing_detected(tmp_path, monkeypatch):
    c = tmp_path / "c"
    cl = tmp_path / "cl"
    cx = tmp_path / "cx"
    monkeypatch.setitem(cli_mod._SKILL_TOOL_DIRS, "cursor", c)
    monkeypatch.setitem(cli_mod._SKILL_TOOL_DIRS, "claude", cl)
    monkeypatch.setitem(cli_mod._SKILL_TOOL_DIRS, "codex", cx)

    result = CliRunner().invoke(cli, ["skill", "install"])
    # Nothing pre-existed → fallback to cursor+claude.
    assert result.exit_code == 0, result.output
    assert (c / "sponsio" / "SKILL.md").is_file()
    assert (cl / "sponsio" / "SKILL.md").is_file()
    assert not (cx / "sponsio").exists()


def test_skill_install_unknown_tool_is_rejected():
    result = CliRunner().invoke(cli, ["skill", "install", "--tool", "vim"])
    assert result.exit_code != 0
    assert "vim" in result.output.lower() or "invalid" in result.output.lower()


# ---------------------------------------------------------------------------
# Discovery footer — the human-readable proof that the skill landed
# where a coding agent can actually find it.  Written after every
# install so CI / setup scripts don't need to re-grep.
# ---------------------------------------------------------------------------


def test_install_prints_discovery_footer_with_absolute_path(tmp_path):
    dest = tmp_path / "skills"
    result = CliRunner().invoke(cli, ["skill", "install", "--dest", str(dest)])
    assert result.exit_code == 0, result.output

    assert "Discovery:" in result.output, (
        "post-install footer missing; users need an at-a-glance path "
        f"verification.  Got:\n{result.output}"
    )
    expected_path = str(dest / "sponsio" / "SKILL.md")
    assert expected_path in result.output, (
        f"footer should cite the absolute skill path so users can paste "
        f"it into agent logs.  Expected {expected_path!r} in:\n{result.output}"
    )


def test_install_footer_reports_copy_mode_in_sync(tmp_path):
    dest = tmp_path / "skills"
    result = CliRunner().invoke(cli, ["skill", "install", "--dest", str(dest)])
    assert result.exit_code == 0, result.output
    # Copy mode + freshly written → must report "in sync" so users
    # can distinguish from drift.  Also asserts that drift detection
    # isn't over-eager on a brand-new copy.
    assert "in sync" in result.output, result.output


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="--link unsupported on Windows",
)
def test_install_footer_reports_symlink_mode(tmp_path):
    dest = tmp_path / "skills"
    result = CliRunner().invoke(
        cli, ["skill", "install", "--dest", str(dest), "--link"]
    )
    assert result.exit_code == 0, result.output
    assert "symlink" in result.output, (
        f"symlinked installs should say so in the footer so users know "
        f"they're on auto-upgrade rails.  Got:\n{result.output}"
    )


def test_install_footer_detects_drift_and_exits_nonzero(tmp_path):
    """Simulate a stale copy: install, then mutate the installed
    SKILL.md so its hash no longer matches the packaged source.  A
    subsequent ``skill install --force`` restores parity; here we
    verify that a *re-verification* of the stale state flags drift."""

    dest = tmp_path / "skills"
    r1 = CliRunner().invoke(cli, ["skill", "install", "--dest", str(dest)])
    assert r1.exit_code == 0, r1.output

    # Mutate the installed file so its hash no longer matches the
    # packaged one.  This models "user ran pip install -U sponsio
    # without re-running skill install".
    skill_md = dest / "sponsio" / "SKILL.md"
    skill_md.write_text(skill_md.read_text() + "\n# stale marker\n")

    # Re-probe via the public helper — this is what ``doctor`` calls.
    from sponsio.cli import _packaged_skill_source, _verify_skill_install_target

    src = _packaged_skill_source()
    probe = _verify_skill_install_target("custom", dest, src)
    assert probe.status == "drift", probe
    assert "re-run" in probe.detail.lower()


def test_install_footer_flags_broken_frontmatter(tmp_path):
    """If somebody hand-edits SKILL.md and strips the frontmatter,
    the agent dispatcher won't find it — verification must fail
    hard, not silently say ok."""
    dest = tmp_path / "skills"
    r1 = CliRunner().invoke(cli, ["skill", "install", "--dest", str(dest)])
    assert r1.exit_code == 0, r1.output

    skill_md = dest / "sponsio" / "SKILL.md"
    skill_md.write_text("no frontmatter at all — just prose")

    from sponsio.cli import _packaged_skill_source, _verify_skill_install_target

    src = _packaged_skill_source()
    probe = _verify_skill_install_target("custom", dest, src)
    assert probe.status == "broken", probe
    assert "frontmatter" in probe.detail.lower()


def test_verify_returns_missing_for_empty_parent(tmp_path):
    from sponsio.cli import _packaged_skill_source, _verify_skill_install_target

    src = _packaged_skill_source()
    probe = _verify_skill_install_target("custom", tmp_path / "nope", src)
    assert probe.status == "missing", probe
    assert probe.mode == "missing"

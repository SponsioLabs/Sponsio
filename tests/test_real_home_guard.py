"""The suite-wide guard against writing under the real home directory.

Exercised against a stand-in home: the guard's globals are pointed at a
temp dir for the duration of each test, so nothing here touches the
real one either.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import conftest as guard


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "realhome"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text('{"permissions": {"allow": ["x"]}}')
    monkeypatch.setattr(guard, "_REAL_HOME", home)
    monkeypatch.setattr(guard, "_BASELINE", {})
    monkeypatch.setattr(guard, "_BACKUP_DIR", None)
    guard._take_baseline()
    return home


def test_an_untouched_home_passes(fake_home):
    assert [r for r, s in guard._snapshot().items() if s != guard._BASELINE[r]] == []


def test_a_changed_config_file_is_restored_and_kept(fake_home):
    settings = fake_home / ".claude" / "settings.json"
    settings.write_text('{"hooks": {"PreToolUse": []}}')
    note = guard._restore_file(".claude/settings.json", "tests/x.py::test_y")
    assert settings.read_text() == '{"permissions": {"allow": ["x"]}}'
    kept = next((guard._BACKUP_DIR / "changed").rglob("settings.json"))
    assert kept.read_text() == '{"hooks": {"PreToolUse": []}}'
    assert "restored" in note


def test_a_created_config_file_is_moved_aside(fake_home):
    hooks = fake_home / ".cursor" / "hooks.json"
    hooks.parent.mkdir()
    hooks.write_text("{}")
    assert (
        guard._snapshot()[".cursor/hooks.json"] != guard._BASELINE[".cursor/hooks.json"]
    )
    note = guard._restore_file(".cursor/hooks.json", "t")
    assert not hooks.exists() and "did not exist before" in note


def test_a_changed_tree_is_detected(fake_home):
    lib = fake_home / ".sponsio" / "plugins" / "_host_x" / "sponsio.yaml"
    lib.parent.mkdir(parents=True)
    lib.write_text("x")
    changed = [r for r, s in guard._snapshot().items() if s != guard._BASELINE[r]]
    assert changed == [".sponsio/plugins"]


def test_the_guard_watches_the_password_database_home_not_HOME(monkeypatch):
    import os
    import pwd

    monkeypatch.setenv("HOME", "/nonexistent")
    assert guard._REAL_HOME == Path(pwd.getpwuid(os.getuid()).pw_dir)

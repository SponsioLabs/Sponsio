"""Every change to a host config is backed up first and written atomically.

``claude_code_install`` rewrote ``settings.json`` with ``write_text`` and
kept nothing; during development that destroyed a real user-level
``~/.claude/settings.json``. Each installer now copies the file to
``<name>.sponsio-backup-<timestamp>`` before it changes it, on install
and on uninstall, and ``sponsio host uninstall`` offers the most recent
backup back.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from sponsio.integrations import config_backup, hosts

USER_SETTINGS = {
    "permissions": {"allow": ["Bash(ls:*)"]},
    "env": {"EDITOR": "vim"},
    "hooks": {
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "mine"}]}
        ]
    },
}
USER_CURSOR_HOOKS = {
    "version": 1,
    "hooks": {"beforeShellExecution": [{"command": "mine"}]},
}
USER_OPENCLAW = {"gateway": {"port": 18789}, "plugins": {"entries": {"other": {}}}}


@pytest.fixture
def home(monkeypatch):
    # Short, so the daemon socket under it is bindable (the installers
    # refuse to pin a hook to one that is not).
    h = Path(tempfile.mkdtemp(prefix="spb-", dir="/tmp")).resolve()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("SPONSIO_PLUGIN_ROOT", str(h / ".sponsio" / "plugins"))
    monkeypatch.setenv("OPENCLAW_HOME", str(h / ".openclaw"))
    monkeypatch.delenv("SPONSIO_DAEMON_SOCKET", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: h))
    monkeypatch.chdir(h)
    for spec in hosts.available():
        assert spec.config_path_user.is_relative_to(h), spec.config_path_user
        assert spec.host_config_fn("user").is_relative_to(h)
    yield h
    shutil.rmtree(h, ignore_errors=True)


def _seed(path: Path, data: dict) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(data, indent=4) + "\n").encode()
    path.write_bytes(raw)
    return raw


def _no_temp_files(directory: Path) -> None:
    leftovers = [p.name for p in directory.iterdir() if p.name.startswith(".")]
    assert leftovers == [], leftovers


HOSTS = [
    ("claude-code", USER_SETTINGS),
    ("cursor", USER_CURSOR_HOOKS),
    ("openclaw", USER_OPENCLAW),
]


@pytest.mark.parametrize(("name", "user_data"), HOSTS)
def test_install_and_uninstall_back_up_the_file_they_change(home, name, user_data):
    spec = hosts.get(name)
    cfg = spec.host_config_fn("user")
    before_install = _seed(cfg, user_data)

    spec.install_fn(spec)
    saved = config_backup.backups(cfg)
    assert len(saved) == 1
    assert saved[0].read_bytes() == before_install
    assert cfg.read_bytes() != before_install
    _no_temp_files(cfg.parent)

    before_uninstall = cfg.read_bytes()
    spec.uninstall_fn(spec)
    saved = config_backup.backups(cfg)
    assert len(saved) == 2
    assert saved[-1].read_bytes() == before_uninstall
    _no_temp_files(cfg.parent)
    # What the user had is still there after the round trip.
    assert json.loads(cfg.read_text()) == json.loads(before_install)


@pytest.mark.parametrize("name", ["claude-code", "cursor"])
def test_a_forced_install_over_a_file_keeps_a_copy(home, name):
    spec = hosts.get(name)
    cfg = spec.host_config_fn("user")
    original = _seed(cfg, USER_SETTINGS if name == "claude-code" else USER_CURSOR_HOOKS)
    result = spec.install_fn(spec, force=True)
    assert result.backup is not None and result.backup.read_bytes() == original


@pytest.mark.parametrize("name", ["claude-code", "cursor"])
def test_a_file_sponsio_created_leaves_nothing_behind(home, name):
    # Installed into a file that did not exist, reinstalled (which backs
    # up Sponsio's own file), uninstalled: no file and no backups, since
    # every copy holds only Sponsio's entries.
    spec = hosts.get(name)
    cfg = spec.host_config_fn("user")
    spec.install_fn(spec)
    spec.install_fn(spec)
    assert config_backup.backups(cfg)
    result = spec.uninstall_fn(spec)
    assert not cfg.exists()
    assert result.backup is None
    assert config_backup.backups(cfg) == []


@pytest.mark.parametrize(("name", "user_data"), HOSTS[:2])
def test_a_backup_with_the_users_content_is_kept(home, name, user_data):
    spec = hosts.get(name)
    cfg = spec.host_config_fn("user")
    original = _seed(cfg, user_data)
    spec.install_fn(spec)
    spec.uninstall_fn(spec)
    assert cfg.exists()
    assert any(b.read_bytes() == original for b in config_backup.backups(cfg))


@pytest.mark.parametrize(("name", "user_data"), HOSTS[:2])
def test_an_uninstall_with_nothing_to_remove_leaves_the_file_alone(
    home, name, user_data
):
    spec = hosts.get(name)
    cfg = spec.host_config_fn("user")
    original = _seed(cfg, user_data)
    result = spec.uninstall_fn(spec)
    assert result.removed_entries == 0 and result.backup is None
    assert cfg.read_bytes() == original
    assert config_backup.backups(cfg) == []


def test_project_scope_backs_up_the_project_file(home):
    spec = hosts.get("claude-code")
    cfg = spec.host_config_fn("project")
    assert cfg == home / ".claude" / "settings.json"
    project = home / "repo"
    project.mkdir()
    os.chdir(project)
    cfg = spec.host_config_fn("project")
    original = _seed(cfg, USER_SETTINGS)
    spec.install_fn(spec, scope="project")
    assert config_backup.latest_backup(cfg).read_bytes() == original


def test_a_symlinked_config_is_written_through_and_stays_a_link(home):
    spec = hosts.get("claude-code")
    real = home / "dotfiles" / "settings.json"
    original = _seed(real, USER_SETTINGS)
    cfg = spec.host_config_fn("user")
    cfg.parent.mkdir(parents=True)
    cfg.symlink_to(real)
    spec.install_fn(spec)
    assert cfg.is_symlink()
    assert "PreToolUse" in json.loads(real.read_text())["hooks"]
    assert config_backup.latest_backup(cfg).read_bytes() == original


def test_file_mode_survives_the_rewrite(home):
    spec = hosts.get("claude-code")
    cfg = spec.host_config_fn("user")
    _seed(cfg, USER_SETTINGS)
    cfg.chmod(0o600)
    spec.install_fn(spec)
    assert cfg.stat().st_mode & 0o777 == 0o600


def test_backups_in_one_second_sort_in_the_order_taken(tmp_path, monkeypatch):
    monkeypatch.setattr(config_backup, "_stamp", lambda: "20261022120000")
    cfg = tmp_path / "settings.json"
    taken = []
    for i in range(12):
        cfg.write_text(str(i))
        taken.append(config_backup.backup(cfg))
    assert config_backup.backups(cfg) == taken
    assert config_backup.latest_backup(cfg).read_text() == "11"


def _cli(*args: str, **kw):
    from sponsio.cli import cli

    return CliRunner().invoke(cli, list(args), catch_exceptions=False, **kw)


def test_uninstall_restores_the_latest_backup_when_asked(home):
    spec = hosts.get("claude-code")
    cfg = spec.host_config_fn("user")
    original = _seed(cfg, USER_SETTINGS)
    spec.install_fn(spec)
    r = _cli("host", "uninstall", "claude-code", "--keep-skill", "--restore-backup")
    assert r.exit_code == 0, r.output
    assert "restored" in r.output
    assert cfg.read_bytes() == original


def test_uninstall_without_a_terminal_only_points_at_the_backup(home):
    spec = hosts.get("claude-code")
    cfg = spec.host_config_fn("user")
    _seed(cfg, {**USER_SETTINGS, "model": "x"})
    spec.install_fn(spec)
    r = _cli("host", "uninstall", "claude-code", "--keep-skill")
    assert r.exit_code == 0, r.output
    assert "--restore-backup" in r.output
    assert "restored" not in r.output
    # Sponsio's entries are gone; the user's edit since install is kept.
    data = json.loads(cfg.read_text())
    assert data["model"] == "x"
    assert not any(
        hosts.is_sponsio_hook_command(h["command"], "claude-code")
        for e in data["hooks"]["PreToolUse"]
        for h in e["hooks"]
    )


def test_cursor_keeps_a_file_with_the_users_other_keys(home):
    spec = hosts.get("cursor")
    cfg = spec.host_config_fn("user")
    _seed(cfg, {"version": 1, "notes": "mine"})
    spec.install_fn(spec)
    spec.uninstall_fn(spec)
    assert json.loads(cfg.read_text()) == {"version": 1, "notes": "mine"}


def test_uninstall_removes_the_skills_dir_it_emptied(home):
    r = _cli("host", "install", "claude-code", "--scope", "project")
    assert r.exit_code == 0, r.output
    skills = home / ".claude" / "skills"
    assert (skills / "sponsio").exists()
    r = _cli(
        "host", "uninstall", "claude-code", "--scope", "project", "--restore-backup"
    )
    assert r.exit_code == 0, r.output
    assert not skills.exists()
    assert "nothing to restore" in r.output
    assert not (home / ".claude" / "settings.json").exists()
    assert config_backup.backups(home / ".claude" / "settings.json") == []


def test_uninstall_keeps_a_skills_dir_with_other_skills(home):
    other = home / ".claude" / "skills" / "mine"
    other.mkdir(parents=True)
    _cli("host", "install", "claude-code", "--scope", "project")
    _cli(
        "host", "uninstall", "claude-code", "--scope", "project", "--no-restore-backup"
    )
    assert other.exists() and not (other.parent / "sponsio").exists()

"""The service account is hardened on every install run, shared parent
directories are checked and never changed, Sponsio's own tree is root's,
and a Tier 2 shim gives Tier 2 recovery advice (2026-10-25)."""

from __future__ import annotations

import os
import stat
import subprocess
from types import SimpleNamespace

import pytest

from sponsio.system_install import (
    SERVICE_USER,
    Executor,
    InstallOptions,
    Layout,
    install,
    linux,
    macos,
    shared_parent_problems,
)


class Recorder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")


def _ex(tmp_path, platform="darwin"):
    rec = Recorder()
    lay = Layout(platform=platform, root=tmp_path / "root")
    return Executor(lay, chown=False, runner=rec, echo=lambda _l: None), rec


def test_an_existing_macos_account_is_hardened_again(tmp_path, monkeypatch):
    import grp
    import pwd

    ex, rec = _ex(tmp_path, "darwin")
    monkeypatch.setattr(macos, "_exists", lambda _ex: True)
    monkeypatch.setattr(pwd, "getpwnam", lambda _n: SimpleNamespace(pw_gid=497))

    def no_group(_n):
        raise KeyError(_n)

    monkeypatch.setattr(grp, "getgrnam", no_group)
    macos.ensure_user(ex)
    user = f"/Users/{SERVICE_USER}"
    group = f"/Groups/{SERVICE_USER}"
    for want in (
        ["dscl", ".", "-create", group, "PrimaryGroupID", "497"],
        ["dscl", ".", "-create", user, "UserShell", "/usr/bin/false"],
        ["dscl", ".", "-create", user, "Password", "*"],
        ["dscl", ".", "-create", user, "IsHidden", "1"],
        ["dscl", ".", "-create", group, "Password", "*"],
    ):
        assert want in rec.calls, want
    assert not any("UniqueID" in c for c in rec.calls)


@pytest.mark.parametrize("exists", [True, False])
def test_the_linux_account_is_locked_on_every_run(tmp_path, monkeypatch, exists):
    ex, rec = _ex(tmp_path, "linux")
    monkeypatch.setattr(linux, "_exists", lambda _ex: exists)
    linux.ensure_user(ex)
    assert [
        "usermod",
        "--shell",
        "/usr/sbin/nologin",
        "--home",
        "/nonexistent",
        "--lock",
        SERVICE_USER,
    ] in rec.calls
    assert any(c[:1] == ["useradd"] for c in rec.calls) is not exists


def test_a_writable_shared_parent_is_reported_not_changed(tmp_path):
    ex, _rec = _ex(tmp_path, "linux")
    lay = ex.layout
    opt = lay.fs(lay.prefix).parent
    opt.mkdir(parents=True)
    os.chmod(opt, 0o777)
    problems = shared_parent_problems(lay)
    assert problems  # a temp tree is not root's, let alone this one
    install(ex, InstallOptions())
    assert stat.S_IMODE(opt.stat().st_mode) == 0o777  # left as it was


def test_a_dry_run_on_this_machine_names_the_parents_it_would_refuse(
    monkeypatch, capsys
):
    import sponsio.system_install as si

    monkeypatch.setattr(
        si, "shared_parent_problems", lambda _lay: ["/opt is writable by every user"]
    )
    monkeypatch.setattr(
        si, "choose_interpreter", lambda *_a: (si.Path("/usr/bin/python3"), [])
    )
    lines: list[str] = []
    lay = Layout(platform="linux", root=si.Path("/"))
    ex = Executor(lay, dry_run=True, chown=False, runner=Recorder(), echo=lines.append)
    install(ex, InstallOptions())
    warning = next(line for line in lines if line.startswith("warning:"))
    assert "/opt is writable by every user" in warning and "chmod go-w" in warning


def test_everything_under_the_prefix_is_roots_and_not_group_writable(tmp_path):
    ex, _rec = _ex(tmp_path, "linux")
    lay = ex.layout
    prefix = lay.fs(lay.prefix)
    (prefix / "lib").mkdir(parents=True)
    planted = prefix / "lib" / "site.py"
    planted.write_text("")
    os.chmod(planted, 0o666)
    os.chmod(prefix / "lib", 0o777)
    ex.secure_tree(lay.prefix)
    for p in (prefix, prefix / "lib", planted):
        assert stat.S_IMODE(p.stat().st_mode) & 0o022 == 0, p


def test_a_tier_2_shim_gives_tier_2_recovery_on_every_failure():
    from sponsio.shim import sponsio_shim as shim

    system = shim.parse_args(["claude-code", "--socket", "/run/s", "--no-start"])
    user = shim.parse_args(["claude-code", "--socket", "/home/u/s"])
    assert "sudo" in shim._recovery(system, "daemon error: boom")
    assert "daemon run --detach" not in shim._recovery(system, "x")
    assert "daemon run --detach" in shim._recovery(user, "x")

"""`sponsio doctor` reports the enforcement tier from facts.

On a default install (hooks through a same-user daemon) the answer is
Tier 1, and the report lists what Tier 2 needs. Tier 2 is claimed only
when every one of its guarantees holds; a file the agent's user could
write never counts.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from sponsio.tier import assess


@pytest.fixture
def tmp():
    d = Path(tempfile.mkdtemp(prefix="spt-", dir="/tmp"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _assess(tmp: Path, **kw):
    sock = tmp / "sponsio.sock"
    sock.touch()
    (tmp / "plugins").mkdir(exist_ok=True)
    args = dict(
        hooks_through_daemon=True,
        socket_path=sock,
        admin_socket_path=tmp / "sponsio.admin.sock",
        shim_path=tmp / "shim.py",
        engine_dir=tmp,
        plugin_root=tmp / "plugins",
        managed_settings=(tmp / "managed.json",),
        mdm_settings=tmp / "no-mdm.plist",
        cursor_hooks=tmp / "no-cursor.json",
        sandboxed=False,
    )
    args.update(kw)
    return assess(**args)


def test_a_same_user_daemon_with_hooks_is_tier_1(tmp):
    report = _assess(tmp)
    assert report.tier == 1
    names = [g.name for g in report.missing(2)]
    assert "The daemon runs as its own user" in names
    assert "Host hooks come from managed settings" in names
    assert all(g.fix for g in report.missing(2))


def test_no_hooks_is_below_tier_1(tmp):
    assert _assess(tmp, hooks_through_daemon=False).tier == 0


def test_tier_2_needs_every_guarantee(tmp):
    # Pretend the daemon and the store belong to another user: the
    # files are still this user's, so the root-owned checks fail and
    # the tier stays 1.
    other = os.getuid() + 1
    report = _assess(tmp, uid=other)
    held = {g.name for g in report.guarantees if g.tier == 2 and g.ok}
    assert "The daemon runs as its own user" in held
    assert report.tier == 1


SYSTEM_SETTINGS = {
    "allowManagedHooksOnly": True,
    "requiredMinimumVersion": "2.1.295",
    "hooks": {"PreToolUse": [{"hooks": [{"command": "x sponsio_shim.py"}]}]},
    "sandbox": {
        "enabled": True,
        "failIfUnavailable": True,
        "allowUnsandboxedCommands": False,
    },
}


def _g(tmp, **kw):
    return {x.name: x for x in _assess(tmp, **kw).guarantees}


@pytest.fixture
def root_owned(monkeypatch):
    """Stand in for root-owned managed settings: the files here are the
    test user's own, which the real check rightly refuses."""
    monkeypatch.setattr("sponsio.tier._owned_check", lambda p: (True, f"{p} ok"))


def test_managed_settings_must_name_the_shim(tmp, root_owned):
    managed = tmp / "managed.json"
    managed.write_text(json.dumps({"hooks": {"PreToolUse": []}}))
    g = _g(tmp)
    assert not g["Host hooks come from managed settings"].ok
    managed.write_text(json.dumps(SYSTEM_SETTINGS))
    g = _g(tmp)
    assert g["Host hooks come from managed settings"].ok
    assert g["Only managed hooks load"].ok
    assert g["The host sandbox is on and cannot be bypassed"].ok
    assert g["Claude Code older than 2.1.295 will not start"].ok


def test_drop_ins_merge_after_the_main_file(tmp, root_owned):
    main = tmp / "managed.json"
    main.write_text(json.dumps({"permissions": {"deny": []}}))
    dropin = tmp / "50-sponsio.json"
    dropin.write_text(json.dumps(SYSTEM_SETTINGS))
    g = _g(tmp, managed_settings=(main, dropin))
    assert g["Host hooks come from managed settings"].ok


def test_user_hooks_allowed_is_degraded_not_lower(tmp):
    settings = dict(SYSTEM_SETTINGS, allowManagedHooksOnly=False)
    (tmp / "managed.json").write_text(json.dumps(settings))
    report = _assess(tmp)
    only = next(g for g in report.guarantees if g.name == "Only managed hooks load")
    assert not only.ok and not only.required
    # Not a Tier 2 requirement: it does not appear in what Tier 2 needs.
    assert only not in report.missing(2)


def test_a_sandbox_that_lets_commands_out_does_not_count(tmp):
    settings = dict(SYSTEM_SETTINGS)
    settings["sandbox"] = dict(settings["sandbox"], allowUnsandboxedCommands=True)
    (tmp / "managed.json").write_text(json.dumps(settings))
    assert not _g(tmp)["The host sandbox is on and cannot be bypassed"].ok


def test_an_mdm_profile_hides_the_files(tmp, root_owned):
    import plistlib

    (tmp / "managed.json").write_text(json.dumps(SYSTEM_SETTINGS))
    mdm = tmp / "mdm.plist"
    mdm.write_bytes(plistlib.dumps({"permissions": {}}))
    g = _g(tmp, mdm_settings=mdm)
    assert not g["Host hooks come from managed settings"].ok
    assert "ignores them" in g["Host hooks come from managed settings"].detail
    mdm.write_bytes(plistlib.dumps({"managedSourcesBehavior": "merge"}))
    assert _g(tmp, mdm_settings=mdm)["Host hooks come from managed settings"].ok


def test_cursor_system_hooks_are_reported_as_tier_1(tmp):
    cursor = tmp / "cursor.json"
    cursor.write_text(
        json.dumps({"hooks": {"preToolUse": [{"command": "x sponsio_shim.py"}]}})
    )
    report = _assess(tmp, cursor_hooks=cursor)
    assert any("Cursor stays at Tier 1" in n for n in report.notes)
    assert "Block until hooks are ready" in report.notes[0]


def test_doctor_says_tier_1_after_a_default_install(monkeypatch):
    from sponsio.doctor import check_tier
    from sponsio.integrations import hosts
    from sponsio.integrations.claude_code_install import install

    home = Path(tempfile.mkdtemp(prefix="sph-", dir="/tmp")).resolve()
    try:
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.delenv("SPONSIO_DAEMON_SOCKET", raising=False)
        assert check_tier().detail.startswith("below Tier 1")
        install(hosts.get("claude-code"))
        r = check_tier()
        assert r.status == "ok"
        assert r.detail.startswith("Tier 1 (dev); Tier 2 needs: ")
        assert "the daemon runs as its own user" in r.detail
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_tier_3_is_claimed_only_from_inside_the_sandbox(tmp):
    g = _g(tmp, sandboxed=True)
    assert g["The agent's only network route is Sponsio's egress proxy"].ok
    g = _g(tmp)
    assert not g["The agent's tools go through Sponsio's gateway"].ok


def test_the_sandbox_variable_alone_proves_nothing(monkeypatch):
    from sponsio.tier import inside_sandbox

    monkeypatch.delenv("SPONSIO_SANDBOX", raising=False)
    assert inside_sandbox() is False


def test_managed_settings_the_user_can_write_do_not_count(tmp):
    (tmp / "managed.json").write_text(json.dumps(SYSTEM_SETTINGS))
    g = _g(tmp)["Host hooks come from managed settings"]
    assert not g.ok and "owned by uid" in g.detail


def test_disable_all_hooks_turns_managed_hooks_off(tmp, root_owned):
    (tmp / "managed.json").write_text(
        json.dumps(dict(SYSTEM_SETTINGS, disableAllHooks=True))
    )
    g = _g(tmp)["Host hooks come from managed settings"]
    assert not g.ok and "disableAllHooks" in g.detail


def test_the_sandbox_merges_across_files_and_honours_platforms(tmp, root_owned):
    import sys

    main = tmp / "managed.json"
    main.write_text(json.dumps({"sandbox": {"enabledPlatforms": []}}))
    dropin = tmp / "50-sponsio.json"
    dropin.write_text(json.dumps(SYSTEM_SETTINGS))
    name = "The host sandbox is on and cannot be bypassed"
    g = _g(tmp, managed_settings=(main, dropin))[name]
    assert not g.ok and "enabledPlatforms" in g.detail
    here = "macos" if sys.platform == "darwin" else "linux"
    main.write_text(json.dumps({"sandbox": {"enabledPlatforms": [here]}}))
    assert _g(tmp, managed_settings=(main, dropin))[name].ok


def test_a_writable_directory_above_a_root_owned_file_does_not_count(monkeypatch):
    from sponsio import tier

    real_access = os.access
    monkeypatch.setattr(
        tier.os,
        "access",
        lambda p, mode: str(p) == "/usr" or real_access(p, mode),
    )
    ok, detail = tier._root_owned_readonly(Path("/usr/bin/env"))
    assert not ok and "/usr" in detail

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
    )
    args.update(kw)
    return assess(**args)


def test_a_same_user_daemon_with_hooks_is_tier_1(tmp):
    report = _assess(tmp)
    assert report.tier == 1
    names = [g.name for g in report.missing(2)]
    assert "The daemon runs as its own user" in names
    assert "Host hooks come from managed settings only" in names
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


def test_managed_settings_must_name_the_shim_and_lock_hooks(tmp):
    managed = tmp / "managed.json"
    managed.write_text(json.dumps({"hooks": {"PreToolUse": []}}))
    g = {x.name: x for x in _assess(tmp).guarantees}
    assert not g["Host hooks come from managed settings only"].ok
    managed.write_text(
        json.dumps(
            {
                "allowManagedHooksOnly": True,
                "hooks": {
                    "PreToolUse": [{"hooks": [{"command": "x sponsio_shim.py"}]}]
                },
                "sandbox": {"enabled": True},
            }
        )
    )
    g = {x.name: x for x in _assess(tmp).guarantees}
    assert g["Host hooks come from managed settings only"].ok
    assert g["The host sandbox is on"].ok


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

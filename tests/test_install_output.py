"""What `sponsio host install` creates and says.

A fresh install used to bootstrap the legacy shared `_host` buckets and
then warn about `_host` as legacy; it reported the per-host bundle it
had just written as "upgraded ... kept 8 customized", because the
installer wrote the starter unstamped and the refresh then kept every
shipped contract as the user's, beside a fresh copy of each: the
library held every default twice.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

BUCKET = "_host_claude_code"


@pytest.fixture
def home(monkeypatch):
    h = Path(tempfile.mkdtemp(prefix="spi-", dir="/tmp")).resolve()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: h))
    for var in ("SPONSIO_PLUGIN_ROOT", "SPONSIO_DAEMON_SOCKET"):
        monkeypatch.delenv(var, raising=False)
    (h / "proj").mkdir()
    monkeypatch.chdir(h / "proj")
    yield h
    shutil.rmtree(h, ignore_errors=True)


def _install(*extra: str) -> str:
    from sponsio.cli import cli

    r = CliRunner().invoke(
        cli,
        ["host", "install", "claude-code", "--scope", "project", "--no-skill", *extra],
        catch_exceptions=False,
    )
    assert r.exit_code == 0, r.output
    return r.output


def _contracts(home: Path) -> list[dict]:
    doc = yaml.safe_load(
        (home / ".sponsio" / "plugins" / BUCKET / "sponsio.yaml").read_text()
    )
    return doc["agents"][BUCKET]["contracts"]


def _shipped_count() -> int:
    from sponsio.plugin.registry import read_bundled

    return len(yaml.safe_load(read_bundled(BUCKET))["agents"][BUCKET]["contracts"])


def test_a_fresh_install_creates_only_the_hosts_buckets(home):
    out = _install()
    assert (
        f"bundle: created {home / '.sponsio' / 'plugins' / BUCKET / 'sponsio.yaml'}"
        in out
    )
    assert "upgraded" not in out and "legacy" not in out
    plugins = sorted(p.name for p in (home / ".sponsio" / "plugins").iterdir())
    assert plugins == [BUCKET, f"{BUCKET}_subagent"]
    assert len(_contracts(home)) == _shipped_count()


def test_a_reinstall_upgrades_and_keeps_nothing_it_did_not_need_to(home):
    _install()
    out = _install()
    assert "upgraded" in out and "kept 0 customized contract(s)" in out
    assert len(_contracts(home)) == _shipped_count()


def test_a_legacy_bucket_that_was_there_is_still_pointed_out(home):
    legacy = home / ".sponsio" / "plugins" / "_host" / "sponsio.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('version: "1"\nagents:\n  _host:\n    contracts: []\n')
    assert "legacy `_host/sponsio.yaml` detected" in _install()


def test_a_library_installed_twice_over_heals_on_reinstall(home):
    # What the old installer left: the starter unstamped, then a refresh
    # that kept it all as the user's beside the stamped defaults.
    from sponsio.plugin.registry import read_bundled

    target = home / ".sponsio" / "plugins" / BUCKET / "sponsio.yaml"
    target.parent.mkdir(parents=True)
    target.write_text(read_bundled(BUCKET))
    out = _install()
    assert "kept 0 customized contract(s)" in out
    assert len(_contracts(home)) == _shipped_count()


def test_a_users_own_contract_survives(home):
    _install()
    target = home / ".sponsio" / "plugins" / BUCKET / "sponsio.yaml"
    doc = yaml.safe_load(target.read_text())
    doc["agents"][BUCKET]["contracts"].append(
        {"desc": "mine", "G": {"pattern": "rate_limit", "args": ["Bash", 99]}}
    )
    target.write_text(yaml.safe_dump(doc, sort_keys=False))
    assert "kept 1 customized contract(s)" in _install()
    assert len(_contracts(home)) == _shipped_count() + 1

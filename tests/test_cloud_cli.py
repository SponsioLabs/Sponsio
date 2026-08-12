"""``sponsio login`` / ``pull`` / ``push``.

These commands are the only place a user types a credential, so the tests
care most about the failure shapes: a key that does not work must never be
saved, and a command that needs a key must say which key it needs.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from sponsio.cli import cli
from sponsio.cloud.client import CloudError, PulledRulebook

YAML = "version: '1'\nagents:\n  quant:\n    contracts: []\n"


class FakeClient:
    def __init__(self, *, configured=True, identity=None, pulled=None, pushed=None, error=None):
        self.url = "http://test"
        self.configured = configured
        self._identity = identity or {"tenant": {"name": "Acme"}, "projects": ["default"]}
        self._pulled = pulled or PulledRulebook(YAML, versions="quant@v3")
        self._pushed = pushed or {
            "project": "default",
            "agents": {"quant": {"version": 3, "rules": 9, "unchanged": False}},
        }
        self._error = error
        self.calls: list[tuple] = []

    def whoami(self):
        self.calls.append(("whoami",))
        if self._error:
            raise self._error
        return self._identity

    def pull_rulebook(self, project, *, agent=None, version=None):
        self.calls.append(("pull", project, agent, version))
        if self._error:
            raise self._error
        return self._pulled

    def push_rulebook(self, project, text):
        self.calls.append(("push", project, len(text)))
        if self._error:
            raise self._error
        return self._pushed


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def patched(monkeypatch):
    """Install a fake client and capture any credential write."""
    holder: dict = {}

    def install(**kwargs):
        client = FakeClient(**kwargs)
        holder["client"] = client
        monkeypatch.setattr("sponsio.cli.commands.cloud._client", lambda **_: client)
        return client

    written: dict = {}

    def fake_write(key, url=None, path=None):
        written["key"] = key
        return "/tmp/creds"

    monkeypatch.setattr("sponsio.cloud.client.write_api_key", fake_write)
    holder["written"] = written
    holder["install"] = install
    return holder


# -- login -----------------------------------------------------------------


def test_login_saves_a_key_that_verifies(runner, patched):
    patched["install"]()
    result = runner.invoke(cli, ["login", "--key", "sk_sp_v1_good"])

    assert result.exit_code == 0, result.output
    assert "logged in" in result.output and "Acme" in result.output
    assert patched["written"]["key"] == "sk_sp_v1_good"


def test_login_never_saves_a_rejected_key(runner, patched):
    """A saved-but-broken key becomes a confusing failure inside an agent
    run days later. Fail while the user is still looking at the terminal."""
    patched["install"](error=CloudError("key rejected", status=401))
    result = runner.invoke(cli, ["login", "--key", "sk_sp_v1_bad"])

    assert result.exit_code != 0
    assert "not accepted" in result.output
    assert "key" not in patched["written"]


def test_login_reads_a_piped_key(runner, patched):
    patched["install"]()
    result = runner.invoke(cli, ["login"], input="sk_sp_v1_piped\n")

    assert result.exit_code == 0, result.output
    assert patched["written"]["key"] == "sk_sp_v1_piped"


# -- pull ------------------------------------------------------------------


def test_pull_writes_to_stdout(runner, patched):
    patched["install"]()
    result = runner.invoke(cli, ["pull", "alpha"])

    assert result.exit_code == 0, result.output
    assert "agents:" in result.output


def test_pull_writes_to_a_file(runner, patched, tmp_path):
    patched["install"]()
    out = tmp_path / "sponsio.yaml"
    result = runner.invoke(cli, ["pull", "alpha", "-o", str(out)])

    assert result.exit_code == 0, result.output
    assert out.read_text() == YAML
    assert "quant@v3" in result.output


def test_pull_without_a_key_says_which_key(runner, patched):
    patched["install"](configured=False)
    result = runner.invoke(cli, ["pull", "alpha"])

    assert result.exit_code != 0
    assert "SPONSIO_API_KEY" in result.output and "sponsio login" in result.output


def test_pull_version_requires_agent(runner, patched):
    """Versions are per agent; a project-wide version would be a fiction."""
    patched["install"]()
    result = runner.invoke(cli, ["pull", "alpha", "--version", "3"])

    assert result.exit_code != 0
    assert "--version needs --agent" in result.output


def test_pull_passes_agent_and_version_through(runner, patched):
    client = patched["install"]()
    runner.invoke(cli, ["pull", "alpha", "--agent", "quant", "--version", "3"])

    assert client.calls[-1] == ("pull", "alpha", "quant", 3)


# -- push ------------------------------------------------------------------


def test_push_reports_each_agents_version(runner, patched, tmp_path):
    patched["install"]()
    config = tmp_path / "sponsio.yaml"
    config.write_text(YAML)

    result = runner.invoke(cli, ["push", str(config)])

    assert result.exit_code == 0, result.output
    assert "quant: v3" in result.output and "new version" in result.output


def test_push_says_plainly_when_nothing_moved(runner, patched, tmp_path):
    """Agents re-push on every start; without this the book looks like it
    keeps changing."""
    patched["install"](
        pushed={
            "project": "default",
            "agents": {"quant": {"version": 3, "rules": 9, "unchanged": True}},
        }
    )
    config = tmp_path / "sponsio.yaml"
    config.write_text(YAML)

    result = runner.invoke(cli, ["push", str(config)])

    assert "unchanged" in result.output


def test_push_without_a_key_is_refused(runner, patched, tmp_path):
    patched["install"](configured=False)
    config = tmp_path / "sponsio.yaml"
    config.write_text(YAML)

    result = runner.invoke(cli, ["push", str(config)])

    assert result.exit_code != 0
    assert "sponsio login" in result.output

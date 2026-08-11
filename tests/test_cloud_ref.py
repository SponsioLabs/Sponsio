"""Resolution of ``sponsio://`` config references.

The behaviour under test is what a user experiences when things go wrong:
network down, key missing, key rejected, nothing published. Every one of
those has to end with a rulebook or an explicit refusal — never with an
agent quietly running unguarded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sponsio.cloud.client import CloudClient, CloudError, PulledRulebook
from sponsio.cloud.ref import (
    CloudRef,
    CloudRefError,
    is_cloud_ref,
    parse_ref,
    resolve_config_ref,
)

YAML = "version: '1'\nagents:\n  quant:\n    contracts: []\n"


class FakeClient(CloudClient):
    """A CloudClient that answers from memory instead of the network."""

    def __init__(self, *, configured=True, result=None, error=None):
        super().__init__(api_key="k" if configured else "", url="http://test")
        self._result = result
        self._error = error
        self.calls: list[tuple] = []

    @property
    def configured(self):
        return bool(self.api_key)

    def pull_rulebook(self, project, *, agent=None, version=None):
        self.calls.append((project, agent, version))
        if self._error is not None:
            raise self._error
        return self._result


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SPONSIO_CACHE_DIR", str(tmp_path / "cache"))
    yield


# -- parsing ---------------------------------------------------------------


def test_is_cloud_ref():
    assert is_cloud_ref("sponsio://alpha-pod-research")
    assert not is_cloud_ref("sponsio.yaml")
    assert not is_cloud_ref(Path("sponsio.yaml"))


def test_parse_ref_with_and_without_version():
    assert parse_ref("sponsio://alpha") == CloudRef("alpha", None)
    assert parse_ref("sponsio://alpha@v7") == CloudRef("alpha", 7)


@pytest.mark.parametrize(
    "bad",
    ["sponsio://", "sponsio://-leading", "sponsio://a b", "sponsio://a@7", "sponsio://a@vx"],
)
def test_parse_ref_rejects_malformed(bad):
    with pytest.raises(CloudRefError):
        parse_ref(bad)


# -- happy path ------------------------------------------------------------


def test_cloud_hit_caches_and_returns_the_file(tmp_path, capsys):
    client = FakeClient(result=PulledRulebook(YAML, versions="quant@v3"))
    path = resolve_config_ref("sponsio://alpha", client=client, cwd=tmp_path)

    assert path.read_text() == YAML
    assert client.calls == [("alpha", None, None)]
    out = capsys.readouterr().out
    assert "cloud checkout" in out and "quant@v3" in out


def test_ref_does_not_carry_an_agent():
    """A project-wide pull is deliberate: the guard knows its own agent_id."""
    client = FakeClient(result=PulledRulebook(YAML))
    resolve_config_ref("sponsio://alpha", client=client)
    assert client.calls[0][1] is None


def test_pinned_version_is_passed_through():
    client = FakeClient(result=PulledRulebook(YAML))
    resolve_config_ref("sponsio://alpha@v7", client=client)
    assert client.calls[0][2] == 7


# -- degraded paths --------------------------------------------------------


def test_network_failure_falls_back_to_cache(tmp_path, capsys):
    ok = FakeClient(result=PulledRulebook(YAML, versions="quant@v3"))
    cached = resolve_config_ref("sponsio://alpha", client=ok, cwd=tmp_path)

    down = FakeClient(error=CloudError("connection refused"))
    path = resolve_config_ref("sponsio://alpha", client=down, cwd=tmp_path)

    assert path == cached
    out = capsys.readouterr().out
    assert "unreachable" in out and "stale" in out


def test_no_key_uses_local_yaml(tmp_path, capsys):
    local = tmp_path / "sponsio.yaml"
    local.write_text(YAML)

    path = resolve_config_ref("sponsio://alpha", client=FakeClient(configured=False), cwd=tmp_path)

    assert path == local
    out = capsys.readouterr().out
    assert "no API key" in out and "local file" in out


def test_rejected_key_is_loud(tmp_path, capsys):
    (tmp_path / "sponsio.yaml").write_text(YAML)
    client = FakeClient(error=CloudError("invalid credentials", status=401))

    resolve_config_ref("sponsio://alpha", client=client, cwd=tmp_path)

    assert "key rejected" in capsys.readouterr().out


def test_nothing_published_yet_falls_back(tmp_path, capsys):
    (tmp_path / "sponsio.yaml").write_text(YAML)
    client = FakeClient(error=CloudError("no rulebook published", status=404))

    path = resolve_config_ref("sponsio://alpha", client=client, cwd=tmp_path)

    assert path == tmp_path / "sponsio.yaml"
    assert "nothing published yet" in capsys.readouterr().out


def test_no_cloud_no_cache_no_local_refuses(tmp_path):
    """The one outcome worse than failing to start is starting unguarded."""
    client = FakeClient(error=CloudError("connection refused"))
    with pytest.raises(CloudRefError) as exc:
        resolve_config_ref("sponsio://alpha", client=client, cwd=tmp_path)
    assert "Refusing to start an agent with no contracts" in str(exc.value)


def test_quiet_suppresses_diagnostics(tmp_path, capsys):
    client = FakeClient(result=PulledRulebook(YAML))
    resolve_config_ref("sponsio://alpha", client=client, cwd=tmp_path, quiet=True)
    assert capsys.readouterr().out == ""


# -- integration with load_config -----------------------------------------


def test_load_config_accepts_a_cloud_ref(tmp_path, monkeypatch):
    from sponsio.config import load_config

    served = tmp_path / "served.yaml"
    served.write_text(YAML)
    monkeypatch.setattr(
        "sponsio.cloud.ref.resolve_config_ref", lambda *a, **k: served, raising=True
    )

    cfg = load_config("sponsio://alpha")
    assert "quant" in cfg.agents


# -- rulebook stamp --------------------------------------------------------


def test_checkout_records_the_rulebook_stamp(tmp_path, monkeypatch):
    """A recorded run has to name the book it enforced, or it cannot be
    replayed against it."""
    import os

    monkeypatch.delenv("SPONSIO_RULEBOOK_STAMP", raising=False)
    client = FakeClient(result=PulledRulebook(YAML, versions="quant@v3", sha="abc123abc123"))

    resolve_config_ref("sponsio://alpha", client=client, cwd=tmp_path, quiet=True)

    stamp = os.environ["SPONSIO_RULEBOOK_STAMP"]
    assert "alpha" in stamp and "quant@v3" in stamp and "sha:abc123abc123" in stamp


def test_tracer_stamps_the_rulebook_on_exported_traces(monkeypatch):
    from sponsio.models.trace import Trace
    from sponsio.tracer.otel_writer import trace_to_otlp

    monkeypatch.setenv("SPONSIO_RULEBOOK_STAMP", "alpha quant@v3 sha:abc")
    otlp = trace_to_otlp(Trace(events=[]), agent_id="quant")

    attrs = otlp["resourceSpans"][0]["resource"]["attributes"]
    by_key = {a["key"]: a["value"]["stringValue"] for a in attrs}
    assert by_key["sponsio.rulebook"] == "alpha quant@v3 sha:abc"


def test_local_runs_carry_no_rulebook_attribute(monkeypatch):
    from sponsio.models.trace import Trace
    from sponsio.tracer.otel_writer import trace_to_otlp

    monkeypatch.delenv("SPONSIO_RULEBOOK_STAMP", raising=False)
    otlp = trace_to_otlp(Trace(events=[]), agent_id="quant")

    keys = {a["key"] for a in otlp["resourceSpans"][0]["resource"]["attributes"]}
    assert "sponsio.rulebook" not in keys

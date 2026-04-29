"""Tests for ``runtime:`` YAML section + Sponsio() resolution precedence.

The precedence contract these tests pin down:

    SPONSIO_MODE env       > runtime.mode      > default ("observe")
    Sponsio(mode=...)      > runtime.mode      > default ("observe")
        but SPONSIO_MODE   > Sponsio(mode=...) still holds (documented)

    Sponsio(dashboard=...) > SPONSIO_DASHBOARD > runtime.dashboard > None
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from sponsio.config import (
    ConfigError,
    RuntimeSection,
    _parse_runtime_section,
    load_config,
)
from sponsio.core import Sponsio, _coerce_dashboard_env


# ---------------------------------------------------------------------------
# _parse_runtime_section — unit tests
# ---------------------------------------------------------------------------


class TestParseRuntimeSection:
    def test_none_returns_defaults(self) -> None:
        r = _parse_runtime_section(None)
        assert r == RuntimeSection()
        assert r.mode is None
        assert r.dashboard is None

    def test_rejects_non_mapping(self) -> None:
        with pytest.raises(ConfigError, match="runtime.*mapping"):
            _parse_runtime_section(["mode: enforce"])  # type: ignore[list-item]

    def test_mode_valid(self) -> None:
        r = _parse_runtime_section({"mode": "enforce"})
        assert r.mode == "enforce"

        r = _parse_runtime_section({"mode": "observe"})
        assert r.mode == "observe"

    def test_mode_rejects_typos(self) -> None:
        with pytest.raises(ConfigError, match="runtime.mode"):
            _parse_runtime_section({"mode": "enforece"})

    def test_mode_empty_string_normalises_to_none(self) -> None:
        r = _parse_runtime_section({"mode": ""})
        assert r.mode is None

    def test_mode_rejects_non_string(self) -> None:
        with pytest.raises(ConfigError, match="runtime.mode"):
            _parse_runtime_section({"mode": 1})

    def test_dashboard_url_string(self) -> None:
        r = _parse_runtime_section({"dashboard": "http://localhost:8000"})
        assert r.dashboard == "http://localhost:8000"

    @pytest.mark.parametrize("truthy", ["true", "TRUE", "yes", "on", "1"])
    def test_dashboard_truthy_strings(self, truthy: str) -> None:
        r = _parse_runtime_section({"dashboard": truthy})
        assert r.dashboard is True

    @pytest.mark.parametrize("falsy", ["false", "FALSE", "no", "off", "0"])
    def test_dashboard_falsy_strings(self, falsy: str) -> None:
        r = _parse_runtime_section({"dashboard": falsy})
        assert r.dashboard is False

    @pytest.mark.parametrize("empty", ["", "none", "NULL"])
    def test_dashboard_empty_strings(self, empty: str) -> None:
        r = _parse_runtime_section({"dashboard": empty})
        assert r.dashboard is None

    def test_dashboard_bool(self) -> None:
        assert _parse_runtime_section({"dashboard": True}).dashboard is True
        assert _parse_runtime_section({"dashboard": False}).dashboard is False

    def test_dashboard_rejects_int(self) -> None:
        with pytest.raises(ConfigError, match="runtime.dashboard"):
            # 42 is not bool / str / None — must be rejected so config
            # typos surface at load time.
            _parse_runtime_section({"dashboard": 42})


# ---------------------------------------------------------------------------
# load_config integration — runtime: lives alongside existing sections
# ---------------------------------------------------------------------------


class TestLoadConfigRuntime:
    def _write(self, body: str) -> Path:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        tmp.write(body)
        tmp.close()
        return Path(tmp.name)

    def test_runtime_omitted_defaults_ok(self) -> None:
        # Absent runtime: is the common case today; must keep working.
        p = self._write('version: "1"\nagents:\n  bot:\n    contracts: []\n')
        try:
            cfg = load_config(p)
            assert cfg.runtime.mode is None
            assert cfg.runtime.dashboard is None
        finally:
            p.unlink()

    def test_runtime_mode_and_dashboard(self) -> None:
        p = self._write(
            'version: "1"\n'
            "runtime:\n"
            "  mode: enforce\n"
            "  dashboard: http://localhost:8000\n"
            "agents:\n"
            "  bot:\n"
            "    contracts: []\n"
        )
        try:
            cfg = load_config(p)
            assert cfg.runtime.mode == "enforce"
            assert cfg.runtime.dashboard == "http://localhost:8000"
        finally:
            p.unlink()

    def test_env_interpolation_in_runtime(self, monkeypatch) -> None:
        # Users may want to thread env vars into the yaml value
        # explicitly (useful in k8s ConfigMaps).
        monkeypatch.setenv("SP_TEST_DASH", "http://k8s-dash:8000")
        p = self._write(
            'version: "1"\n'
            "runtime:\n"
            "  dashboard: ${SP_TEST_DASH}\n"
            "agents:\n"
            "  bot:\n"
            "    contracts: []\n"
        )
        try:
            cfg = load_config(p)
            assert cfg.runtime.dashboard == "http://k8s-dash:8000"
        finally:
            p.unlink()

    def test_bad_mode_fails_load(self) -> None:
        p = self._write(
            'version: "1"\n'
            "runtime:\n"
            "  mode: ENFORCE_MAYBE\n"
            "agents:\n"
            "  bot:\n"
            "    contracts: []\n"
        )
        try:
            with pytest.raises(ConfigError, match="runtime.mode"):
                load_config(p)
        finally:
            p.unlink()


# ---------------------------------------------------------------------------
# _coerce_dashboard_env — mirrors yaml parsing so env/yaml agree
# ---------------------------------------------------------------------------


class TestCoerceDashboardEnv:
    @pytest.mark.parametrize("v", ["", "none", "NULL"])
    def test_empty_to_none(self, v: str) -> None:
        assert _coerce_dashboard_env(v) is None

    @pytest.mark.parametrize("v", ["true", "1", "YES", "on"])
    def test_truthy(self, v: str) -> None:
        assert _coerce_dashboard_env(v) is True

    @pytest.mark.parametrize("v", ["false", "0", "NO", "off"])
    def test_falsy(self, v: str) -> None:
        assert _coerce_dashboard_env(v) is False

    def test_url(self) -> None:
        assert _coerce_dashboard_env("http://x:8000") == "http://x:8000"


# ---------------------------------------------------------------------------
# Sponsio() factory — precedence resolution
# ---------------------------------------------------------------------------


@pytest.fixture
def yaml_with_runtime(tmp_path: Path) -> Path:
    p = tmp_path / "sponsio.yaml"
    p.write_text(
        'version: "1"\n'
        "runtime:\n"
        "  mode: enforce\n"
        "  dashboard: http://yaml-dash:9999\n"
        "agents:\n"
        "  bot:\n"
        "    contracts: []\n"
    )
    return p


@pytest.fixture
def yaml_without_runtime(tmp_path: Path) -> Path:
    p = tmp_path / "sponsio.yaml"
    p.write_text(
        'version: "1"\nagents:\n  bot:\n    contracts: []\n',
    )
    return p


class TestSponsioFactoryPrecedence:
    def _clean_env(self, monkeypatch) -> None:
        monkeypatch.delenv("SPONSIO_MODE", raising=False)
        monkeypatch.delenv("SPONSIO_DASHBOARD", raising=False)

    def test_yaml_only_applies(self, yaml_with_runtime: Path, monkeypatch) -> None:
        self._clean_env(monkeypatch)
        g = Sponsio(config=str(yaml_with_runtime), agent_id="bot")
        assert g._mode == "enforce"
        assert g._dashboard_url == "http://yaml-dash:9999"

    def test_env_mode_wins_over_yaml(
        self, yaml_with_runtime: Path, monkeypatch
    ) -> None:
        self._clean_env(monkeypatch)
        monkeypatch.setenv("SPONSIO_MODE", "observe")
        g = Sponsio(config=str(yaml_with_runtime), agent_id="bot")
        assert g._mode == "observe"

    def test_env_dashboard_wins_over_yaml(
        self, yaml_with_runtime: Path, monkeypatch
    ) -> None:
        self._clean_env(monkeypatch)
        monkeypatch.setenv("SPONSIO_DASHBOARD", "http://env-dash:7777")
        g = Sponsio(config=str(yaml_with_runtime), agent_id="bot")
        assert g._dashboard_url == "http://env-dash:7777"

    def test_env_dashboard_false_disables(
        self, yaml_with_runtime: Path, monkeypatch
    ) -> None:
        # Useful to silence the yaml-configured dashboard in CI without
        # editing the yaml.
        self._clean_env(monkeypatch)
        monkeypatch.setenv("SPONSIO_DASHBOARD", "false")
        g = Sponsio(config=str(yaml_with_runtime), agent_id="bot")
        assert g._dashboard_url is None

    def test_ctor_dashboard_beats_env_and_yaml(
        self, yaml_with_runtime: Path, monkeypatch
    ) -> None:
        self._clean_env(monkeypatch)
        monkeypatch.setenv("SPONSIO_DASHBOARD", "http://env-dash:7777")
        g = Sponsio(
            config=str(yaml_with_runtime),
            agent_id="bot",
            dashboard="http://ctor:6666",
        )
        assert g._dashboard_url == "http://ctor:6666"

    def test_ctor_mode_beats_yaml_when_env_unset(
        self, yaml_with_runtime: Path, monkeypatch
    ) -> None:
        self._clean_env(monkeypatch)
        g = Sponsio(config=str(yaml_with_runtime), agent_id="bot", mode="observe")
        assert g._mode == "observe"

    def test_env_mode_still_beats_ctor_mode(
        self, yaml_with_runtime: Path, monkeypatch
    ) -> None:
        # Documented behaviour: SPONSIO_MODE env wins over explicit
        # ctor arg so ops can flip production without a code change.
        self._clean_env(monkeypatch)
        monkeypatch.setenv("SPONSIO_MODE", "observe")
        g = Sponsio(config=str(yaml_with_runtime), agent_id="bot", mode="enforce")
        assert g._mode == "observe"

    def test_no_yaml_runtime_falls_to_defaults(
        self, yaml_without_runtime: Path, monkeypatch
    ) -> None:
        self._clean_env(monkeypatch)
        g = Sponsio(config=str(yaml_without_runtime), agent_id="bot")
        assert g._mode == "observe"  # BaseGuard default
        assert g._dashboard_url is None

    def test_inline_mode_unaffected_by_runtime(self, monkeypatch) -> None:
        # With no config=, there's no yaml runtime section in scope.
        # Pure inline construction must still behave like before.
        self._clean_env(monkeypatch)
        g = Sponsio(agent_id="bot", contracts=[])
        assert g._mode == "observe"

    def test_defaults_mode_yaml_drives_when_no_runtime_section(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # ``sponsio onboard`` / ``sponsio init`` write ``defaults.mode``
        # (not ``runtime.mode``).  Sponsio() must honour that — without
        # this fallback, flipping ``defaults.mode: observe → enforce``
        # in an onboard-generated yaml is silently ignored, and users
        # have no way to make the yaml authoritative without learning
        # the undocumented ``runtime:`` alternative.
        self._clean_env(monkeypatch)
        p = tmp_path / "sponsio.yaml"
        p.write_text(
            'version: "1"\n'
            "defaults:\n"
            "  mode: enforce\n"
            "agents:\n"
            "  bot:\n"
            "    contracts: []\n"
        )
        g = Sponsio(config=str(p), agent_id="bot")
        assert g._mode == "enforce"

    def test_runtime_mode_beats_defaults_mode(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # When both sections are present (transitional configs / power
        # users), the typed ``runtime:`` block is the canonical source.
        self._clean_env(monkeypatch)
        p = tmp_path / "sponsio.yaml"
        p.write_text(
            'version: "1"\n'
            "runtime:\n"
            "  mode: observe\n"
            "defaults:\n"
            "  mode: enforce\n"
            "agents:\n"
            "  bot:\n"
            "    contracts: []\n"
        )
        g = Sponsio(config=str(p), agent_id="bot")
        assert g._mode == "observe"
        assert g._dashboard_url is None

    def test_inline_guard_honors_env_dashboard(self, monkeypatch) -> None:
        # Behavioural expansion (CHANGELOG): SPONSIO_DASHBOARD now
        # applies even without config=.  Pin it down so a future
        # refactor can't silently revert.
        self._clean_env(monkeypatch)
        monkeypatch.setenv("SPONSIO_DASHBOARD", "http://env-only:8000")
        g = Sponsio(agent_id="bot", contracts=[])
        assert g._dashboard_url == "http://env-only:8000"

    def test_inline_guard_env_dashboard_false_disables(self, monkeypatch) -> None:
        # Symmetric guarantee: opting *out* via env on an inline
        # guard must not blow up (regression — earlier resolver
        # only ran on the config= branch).
        self._clean_env(monkeypatch)
        monkeypatch.setenv("SPONSIO_DASHBOARD", "false")
        g = Sponsio(agent_id="bot", contracts=[])
        assert g._dashboard_url is None


# ---------------------------------------------------------------------------
# Ops ergonomics — the main point of the feature
# ---------------------------------------------------------------------------


def test_integration_simplifies_run_script(
    yaml_with_runtime: Path, monkeypatch
) -> None:
    """The motivating use case: a user integration like
    earnings-forecast-hub's ``run_with_sponsio.py`` used to do:

        guard = Sponsio(
            agent_id="agent",
            config="sponsio.yaml",
            mode=os.getenv("SPONSIO_MODE", "observe"),
            dashboard=os.getenv("SPONSIO_DASHBOARD") or None,
        )

    With runtime: in the yaml they can drop the env-juggling lines
    without losing any of the override points.
    """
    monkeypatch.delenv("SPONSIO_MODE", raising=False)
    monkeypatch.delenv("SPONSIO_DASHBOARD", raising=False)

    # Clean minimal call — the yaml does all the work.
    guard = Sponsio(config=str(yaml_with_runtime), agent_id="bot")
    assert guard._mode == "enforce"
    assert guard._dashboard_url == "http://yaml-dash:9999"

    # Same call, but ops overrides via env → must switch without code
    # changes.
    monkeypatch.setenv("SPONSIO_MODE", "observe")
    monkeypatch.setenv("SPONSIO_DASHBOARD", "false")
    guard = Sponsio(config=str(yaml_with_runtime), agent_id="bot")
    assert guard._mode == "observe"
    assert guard._dashboard_url is None

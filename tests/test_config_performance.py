"""Parser-level tests for the ``performance:`` YAML section.

The full integration with the guard is covered in
``test_perf_runtime.py``; this file tests the YAML → dataclass
mapping in isolation so config-only regressions get caught at the
right layer.
"""

from __future__ import annotations

import pytest

from sponsio.config import (
    ConfigError,
    PerformanceSection,
    _parse_performance_section,
    load_config,
)


# ---------------------------------------------------------------------------
# Dataclass defaults
# ---------------------------------------------------------------------------


def test_defaults_match_docstring():
    """Default values documented in PerformanceSection's docstring
    must match what the dataclass actually produces — a drift here
    silently mis-documents the feature."""
    section = PerformanceSection()
    assert section.report == "auto"
    assert section.export_path is None
    assert section.warn_slow_dfa_us == 500.0
    assert section.histogram_size == 10_000


# ---------------------------------------------------------------------------
# _parse_performance_section
# ---------------------------------------------------------------------------


def test_parse_none_returns_defaults():
    """A missing ``performance:`` block must produce a PerformanceSection
    with default values — any other behaviour would break back-compat
    for existing configs."""
    section = _parse_performance_section(None)
    assert section == PerformanceSection()


def test_parse_full_section():
    raw = {
        "report": "always",
        "export_path": ".sponsio/perf.json",
        "warn_slow_dfa_us": 50.0,
        "histogram_size": 5_000,
    }
    section = _parse_performance_section(raw)
    assert section.report == "always"
    assert section.export_path == ".sponsio/perf.json"
    assert section.warn_slow_dfa_us == 50.0
    assert section.histogram_size == 5_000


def test_parse_empty_export_path_becomes_none():
    """Allow ``export_path: ""`` in YAML (e.g. after env
    interpolation of an unset ``${...}``) to mean "no export" rather
    than "write to the empty filename"."""
    section = _parse_performance_section({"export_path": ""})
    assert section.export_path is None


def test_parse_rejects_non_mapping():
    with pytest.raises(ConfigError, match="must be a mapping"):
        _parse_performance_section("nope")


def test_parse_rejects_invalid_report_mode():
    with pytest.raises(ConfigError, match="report must be one of"):
        _parse_performance_section({"report": "sometimes"})


def test_parse_rejects_non_integer_histogram():
    with pytest.raises(ConfigError, match="histogram_size"):
        _parse_performance_section({"histogram_size": "many"})


def test_parse_rejects_zero_histogram():
    with pytest.raises(ConfigError, match="histogram_size"):
        _parse_performance_section({"histogram_size": 0})


def test_parse_rejects_non_numeric_warn_threshold():
    with pytest.raises(ConfigError, match="warn_slow_dfa_us"):
        _parse_performance_section({"warn_slow_dfa_us": "slow"})


def test_parse_zero_warn_means_disable():
    """``warn_slow_dfa_us: 0`` turns off the slow-DFA stderr warning."""
    section = _parse_performance_section({"warn_slow_dfa_us": 0.0})
    assert section.warn_slow_dfa_us == 0.0


# ---------------------------------------------------------------------------
# End-to-end: load_config wires it through
# ---------------------------------------------------------------------------


def test_load_config_threads_performance_through(tmp_path):
    p = tmp_path / "sponsio.yaml"
    p.write_text(
        """
version: "1"
performance:
  report: always
  histogram_size: 42
agents:
  a:
    contracts: []
"""
    )
    config = load_config(p)
    assert config.performance.report == "always"
    assert config.performance.histogram_size == 42


def test_load_config_env_interpolation_in_export_path(tmp_path, monkeypatch):
    """``${VAR}`` must expand in performance.export_path too —
    users routinely parametrise output paths by run ID / env."""
    monkeypatch.setenv("SPONSIO_PERF_DIR", "/var/log/sponsio")
    p = tmp_path / "sponsio.yaml"
    p.write_text(
        """
version: "1"
performance:
  export_path: "${SPONSIO_PERF_DIR}/perf.json"
agents:
  a:
    contracts: []
"""
    )
    config = load_config(p)
    assert config.performance.export_path == "/var/log/sponsio/perf.json"

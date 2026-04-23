"""Regression tests for path-traversal hardening.

Covers:

* :mod:`sponsio._paths` — ``safe_resolve`` / ``safe_join_segment``.
* :mod:`sponsio.runtime.session_log` — malicious ``agent_id`` cannot
  escape the sessions tree.
* :mod:`sponsio.config` — bare-path ``include:`` rejects ``../``
  escapes from the host yaml's directory.
* :mod:`sponsio.discovery.loaders` — ``safe_root`` opt-in confines
  ``load_document`` / ``load_trace`` for untrusted callers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sponsio._paths import PathEscapeError, safe_join_segment, safe_resolve
from sponsio.config import ConfigError, load_config
from sponsio.discovery.loaders import load_document, load_trace
from sponsio.runtime.session_log import (
    _sanitize_agent_id,
    default_session_dir,
)


# ---------------------------------------------------------------------------
# safe_resolve / safe_join_segment
# ---------------------------------------------------------------------------


class TestSafeResolve:
    def test_relative_under_base_dir_ok(self, tmp_path: Path) -> None:
        f = tmp_path / "a" / "b.txt"
        f.parent.mkdir()
        f.write_text("hi")
        out = safe_resolve("a/b.txt", base_dir=tmp_path, safe_root=tmp_path)
        assert out == f.resolve()

    def test_dotdot_escape_blocked(self, tmp_path: Path) -> None:
        with pytest.raises(PathEscapeError):
            safe_resolve("../outside.txt", base_dir=tmp_path, safe_root=tmp_path)

    def test_absolute_outside_blocked(self, tmp_path: Path) -> None:
        with pytest.raises(PathEscapeError):
            safe_resolve("/etc/passwd", base_dir=tmp_path, safe_root=tmp_path)

    def test_no_safe_root_means_no_check(self, tmp_path: Path) -> None:
        # Without safe_root, ``..`` is allowed (CLI compat).
        out = safe_resolve("../foo", base_dir=tmp_path)
        assert isinstance(out, Path)

    def test_allow_absolute_false_rejects(self, tmp_path: Path) -> None:
        with pytest.raises(PathEscapeError):
            safe_resolve("/tmp/x", base_dir=tmp_path, allow_absolute=False)


class TestSafeJoinSegment:
    def test_plain_segment_ok(self, tmp_path: Path) -> None:
        out = safe_join_segment(tmp_path, "agent.v2")
        assert out == (tmp_path / "agent.v2").resolve()

    @pytest.mark.parametrize(
        "bad",
        ["..", ".", "", "a/b", "a\\b", "../a"],
    )
    def test_unsafe_segments_rejected(self, tmp_path: Path, bad: str) -> None:
        with pytest.raises(PathEscapeError):
            safe_join_segment(tmp_path, bad)


# ---------------------------------------------------------------------------
# session_log.default_session_dir
# ---------------------------------------------------------------------------


class TestSessionDirAgentIdSanitization:
    @pytest.mark.parametrize(
        "raw,expected_prefix",
        [
            ("bot", "bot"),
            ("team:bot", "team:bot"),
            ("agent.v2", "agent.v2"),
            ("../../etc", "etc"),  # dots stripped, slashes substituted
            ("a/b/c", "a_b_c"),
            ("../../../passwd", "passwd"),
            ("", "_unknown"),
            ("..", "_unknown"),
        ],
    )
    def test_sanitize(self, raw: str, expected_prefix: str) -> None:
        assert _sanitize_agent_id(raw) == expected_prefix

    def test_malicious_agent_id_stays_under_base(self, tmp_path: Path) -> None:
        d = default_session_dir("../../../escaped", base_dir=tmp_path)
        # Resolved path must stay strictly under tmp_path
        assert (
            tmp_path.resolve() in d.resolve().parents
            or d.resolve() == tmp_path.resolve()
        ), f"escaped to {d.resolve()} (base={tmp_path.resolve()})"

    def test_long_agent_id_capped(self, tmp_path: Path) -> None:
        long_id = "x" * 500
        d = default_session_dir(long_id, base_dir=tmp_path)
        assert len(d.name) <= 128


# ---------------------------------------------------------------------------
# config.py include: containment
# ---------------------------------------------------------------------------


class TestIncludeContainment:
    def test_relative_include_outside_base_dir_rejected(self, tmp_path: Path) -> None:
        # Layout:
        #   tmp/host/sponsio.yaml      <- includes "../escape.yaml"
        #   tmp/escape.yaml            <- attacker-controlled file
        host_dir = tmp_path / "host"
        host_dir.mkdir()
        (tmp_path / "escape.yaml").write_text("agents:\n  '*':\n    contracts: []\n")
        host_yaml = host_dir / "sponsio.yaml"
        host_yaml.write_text(
            "agents:\n  bot:\n    include:\n      - ../escape.yaml\n    contracts: []\n"
        )
        with pytest.raises(ConfigError) as exc:
            load_config(host_yaml)
        assert "outside" in str(exc.value).lower()

    def test_absolute_include_still_allowed(self, tmp_path: Path) -> None:
        # Operator-typed absolute path should still work.
        pack = tmp_path / "pack.yaml"
        pack.write_text("agents:\n  '*':\n    contracts: []\n")
        host_yaml = tmp_path / "host.yaml"
        host_yaml.write_text(
            f"agents:\n  bot:\n    include:\n      - {pack}\n    contracts: []\n"
        )
        # Should not raise.
        cfg = load_config(host_yaml)
        assert cfg is not None


# ---------------------------------------------------------------------------
# discovery loaders: opt-in safe_root
# ---------------------------------------------------------------------------


class TestLoaderSafeRoot:
    def test_load_document_no_safe_root_keeps_legacy_behavior(
        self, tmp_path: Path
    ) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("hello")
        # No safe_root: classic CLI behavior, anything readable works.
        assert load_document(f) == "hello"

    def test_load_document_safe_root_blocks_escape(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        with pytest.raises(PathEscapeError):
            load_document(outside, safe_root=sandbox)

    def test_load_document_safe_root_allows_inside(self, tmp_path: Path) -> None:
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        f = sandbox / "ok.md"
        f.write_text("inside")
        assert load_document(f, safe_root=sandbox) == "inside"

    def test_load_trace_safe_root_blocks_escape(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.json"
        outside.write_text(json.dumps({"events": []}))
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        with pytest.raises(PathEscapeError):
            load_trace(outside, safe_root=sandbox)


# ---------------------------------------------------------------------------
# Dashboard URL hardening (SSRF / metadata exfil)
# ---------------------------------------------------------------------------


class TestDashboardUrlValidation:
    def _validate(self, url):
        from sponsio.integrations.base import BaseGuard

        return BaseGuard._validate_dashboard_url(url)

    def test_none_passes(self) -> None:
        assert self._validate(None) is None

    def test_https_public_host_ok(self) -> None:
        assert (
            self._validate("https://dash.example.com/x") == "https://dash.example.com/x"
        )

    @pytest.mark.parametrize(
        "url",
        [
            "ftp://example.com",
            "javascript:alert(1)",
            "file:///etc/passwd",
        ],
    )
    def test_bad_scheme_rejected(self, url: str) -> None:
        with pytest.raises(ValueError, match="scheme"):
            self._validate(url)

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",
            "http://metadata.google.internal/",
            "http://metadata/computeMetadata/v1/",
        ],
    )
    def test_cloud_metadata_hard_blocked(self, url: str) -> None:
        with pytest.raises(ValueError, match="metadata"):
            self._validate(url)

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:9999",
            "http://127.0.0.1:8080",
            "http://10.0.0.5/",
            "http://192.168.1.10/",
            "http://172.16.0.1/",
        ],
    )
    def test_local_addresses_warn_but_allowed(self, url: str) -> None:
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert self._validate(url) == url
        assert any("local-network" in str(w.message) for w in caught), (
            f"expected a local-network warning for {url}"
        )

    def test_strict_mode_rejects_local(self, monkeypatch) -> None:
        monkeypatch.setenv("SPONSIO_STRICT_DASHBOARD_URL", "1")
        with pytest.raises(ValueError, match="local-network"):
            self._validate("http://10.0.0.5/")

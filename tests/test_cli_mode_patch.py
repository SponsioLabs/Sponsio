"""Tests for ``sponsio mode <observe|enforce>`` yaml patching.

Locks in the contract that the CLI:

* Prefers updating ``runtime.mode`` (the only thing the TS loader
  reads).
* Falls back to ``defaults.mode`` (still works for the Python loader,
  matches what older ``sponsio onboard`` outputs wrote).
* Appends a fresh ``runtime:`` block when neither exists, so the file
  stays loadable instead of failing silently.
* Preserves comments / line endings on the patched line.
* Does NOT accidentally pick the wrong ``mode:`` line when both a
  ``runtime.mode`` AND a ``defaults.mode`` exist (the regression
  reported in the project_sponsio_mode_cli_yaml_shape memo).
"""

from __future__ import annotations

import pytest

from sponsio.cli import _patch_mode_in_yaml


class TestPatchModeInYaml:
    def test_patches_runtime_mode(self) -> None:
        text = "runtime:\n  mode: observe\n"
        new, action = _patch_mode_in_yaml(text, "enforce")
        assert action == "runtime"
        assert new == "runtime:\n  mode: enforce\n"

    def test_patches_defaults_mode(self) -> None:
        text = "defaults:\n  mode: observe\n"
        new, action = _patch_mode_in_yaml(text, "enforce")
        assert action == "defaults"
        assert new == "defaults:\n  mode: enforce\n"

    def test_prefers_runtime_over_defaults(self) -> None:
        """Both blocks present, defaults listed first. The CLI must
        still pick the runtime line — that's the only place the TS
        loader looks, so picking defaults silently leaves TS stale.
        This is the regression the memory note flagged."""
        text = "defaults:\n  mode: observe\nruntime:\n  mode: observe\n"
        new, action = _patch_mode_in_yaml(text, "enforce")
        assert action == "runtime"
        # The runtime line flipped; defaults stayed put.
        assert "runtime:\n  mode: enforce\n" in new
        assert "defaults:\n  mode: observe\n" in new

    def test_appends_runtime_block_when_neither_present(self) -> None:
        text = "agents:\n  bot:\n    contracts: []\n"
        new, action = _patch_mode_in_yaml(text, "enforce")
        assert action == "appended"
        assert new.endswith("\nruntime:\n  mode: enforce\n")
        # Original content preserved verbatim.
        assert new.startswith(text)

    def test_unchanged_when_already_target(self) -> None:
        text = "runtime:\n  mode: enforce\n"
        new, action = _patch_mode_in_yaml(text, "enforce")
        assert action == "unchanged"
        assert new == text

    def test_preserves_inline_comment(self) -> None:
        text = "runtime:\n  mode: observe  # safe default\n"
        new, action = _patch_mode_in_yaml(text, "enforce")
        assert action == "runtime"
        assert new == "runtime:\n  mode: enforce  # safe default\n"

    def test_ignores_mode_in_unrelated_block(self) -> None:
        """A ``mode:`` line nested under some other key (e.g.
        ``judge.fallback_mode``) must not be confused for the runtime
        mode. The walker only looks under ``runtime:`` / ``defaults:``."""
        text = "judge:\n  fallback_mode: allow\nagents:\n  bot:\n    contracts: []\n"
        new, action = _patch_mode_in_yaml(text, "enforce")
        assert action == "appended"
        # fallback_mode left alone.
        assert "fallback_mode: allow" in new

    def test_skips_comment_only_lines(self) -> None:
        """Comments at the top level must not be treated as a new
        ``current_parent`` key — otherwise a stray ``# runtime:`` in a
        comment would mislead the walker."""
        text = "# runtime: this is a comment\ndefaults:\n  mode: observe\n"
        new, action = _patch_mode_in_yaml(text, "enforce")
        assert action == "defaults"
        assert new == "# runtime: this is a comment\ndefaults:\n  mode: enforce\n"

    @pytest.mark.parametrize(
        "ending,expected_ending",
        [("\n", "\n"), ("", "")],
    )
    def test_preserves_line_ending_of_patched_line(
        self, ending: str, expected_ending: str
    ) -> None:
        text = f"runtime:\n  mode: observe{ending}"
        new, action = _patch_mode_in_yaml(text, "enforce")
        assert action == "runtime"
        assert new == f"runtime:\n  mode: enforce{expected_ending}"

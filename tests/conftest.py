"""Shared pytest configuration.

Sets ``SPONSIO_MODE=enforce`` as the test-suite default.  The
production default is ``observe`` (shadow mode), but most tests in
this repo were written before that flip and exercise the *blocking*
semantics directly — adding ``mode="enforce"`` to every guard
construction site would be 30+ noisy diffs that say nothing.

Tests that specifically exercise shadow-mode behavior (or the
mode-resolution logic itself) opt out by deleting the env var, e.g.::

    @pytest.fixture(autouse=True)
    def _clean_env(monkeypatch):
        monkeypatch.delenv("SPONSIO_MODE", raising=False)

This is the same pattern ``tests/test_doctor.py`` and
``tests/test_shadow_mode.py`` already use.
"""

from __future__ import annotations

import os


def pytest_configure(config):  # noqa: ARG001 — pytest hook signature
    # Set BEFORE any test imports `sponsio.integrations.base`, so the
    # module-level ``_VALID_MODES`` constant and any cached env reads
    # see the right value from the start.  Using ``setdefault`` so a
    # caller-supplied env var (e.g. ``SPONSIO_MODE=observe pytest``)
    # still wins — useful for running the suite against the new
    # production default to find what the next round of fixes is.
    os.environ.setdefault("SPONSIO_MODE", "enforce")

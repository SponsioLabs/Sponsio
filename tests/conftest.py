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

import pytest


def pytest_configure(config):  # noqa: ARG001 — pytest hook signature
    # Set BEFORE any test imports `sponsio.integrations.base`, so the
    # module-level ``_VALID_MODES`` constant and any cached env reads
    # see the right value from the start.  Using ``setdefault`` so a
    # caller-supplied env var (e.g. ``SPONSIO_MODE=observe pytest``)
    # still wins — useful for running the suite against the new
    # production default to find what the next round of fixes is.
    os.environ.setdefault("SPONSIO_MODE", "enforce")


@pytest.fixture(autouse=True)
def _reset_rich_style_cache():
    """Invalidate Rich's ``Style`` ANSI caches between tests.

    Rich interns ``Style`` instances via ``@lru_cache`` and each
    instance memoises its first-rendered ANSI string in ``_ansi``.
    When one test renders a style under a non-truecolor Console
    (typical under ``CliRunner``, where stderr isn't a TTY and Rich
    auto-downgrades to STANDARD 16-color), the cached ``_ansi`` is
    16-color — and a *later* test that constructs an explicit
    ``Console(color_system="truecolor")`` and prints text using the
    same style gets the cached 16-color back instead of truecolor,
    because ``Style.render`` uses ``self._ansi or
    self._make_ansi_codes(color_system)`` — the cached value wins
    over the requested color system.

    This silently broke 7 assertions in test_render_session_view /
    test_render_monitor / test_render_rich_report / test_render_explain
    whenever the suite ran in any order that put a CliRunner-style
    test before a truecolor-asserting one.

    Two-step clear: (1) drop the ``Style.parse`` / ``Style.normalize``
    / ``Color.parse`` lru_cache so subsequent ``Style.parse`` calls
    return fresh instances, (2) walk live ``Style`` instances via
    ``gc.get_objects`` and reset their ``_ansi`` so any object still
    referenced by a Text span (or by module-level constants) renders
    fresh against the next test's color system.

    The bug has nothing to do with the failing tests themselves;
    clearing both layers at every test boundary makes the ANSI render
    path hermetic again.
    """
    import gc

    from rich.color import Color
    from rich.style import Style

    Style.parse.cache_clear()
    Style.normalize.cache_clear()
    if hasattr(Color, "parse") and hasattr(Color.parse, "cache_clear"):
        Color.parse.cache_clear()
    for obj in gc.get_objects():
        # ``isinstance(obj, Style)`` reads ``obj.__class__`` (CPython
        # consults the instance's ``__class__`` attribute, not just
        # ``type(obj)``). Some live objects are lazy-import proxies
        # whose ``__class__`` getter has side effects — e.g. once an
        # openai-touching test has run, the openai SDK leaves proxies
        # for its optional submodules in memory, and probing their
        # ``__class__`` tries to import ``sounddevice`` (voice helpers)
        # or raises ``OpenAIError``. That used to escape this autouse
        # fixture and error the *setup* of every subsequent test. Treat
        # any object that objects to introspection as "not a Style".
        try:
            is_style = isinstance(obj, Style)
        except Exception:
            continue
        if is_style:
            try:
                obj._ansi = None
            except Exception:
                pass
    yield

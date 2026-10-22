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
import pwd
import shutil
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# The real home directory is off limits
# ---------------------------------------------------------------------------
#
# Host config files are global and live: Claude Code re-reads
# ``~/.claude/settings.json`` on every tool call of every session on the
# machine. A test that wrote a fail-closed hook there (it happened: the
# host registry fixed its paths at import, before the test moved HOME)
# denied every tool call of every Claude Code session until the file was
# fixed by hand, and destroyed the settings it overwrote.
#
# So the suite snapshots what Sponsio could write under the real HOME
# (resolved from the password database, which no test patches), and
# checks it after every test. A config file that changed is put back from
# the session-start copy at once (the changed version is kept next to
# the backup) and the run stops. Anything else that appeared or changed
# is reported, not touched, and the run stops.

_REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)

_GUARDED_FILES = (
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".cursor/hooks.json",
    ".openclaw/openclaw.json",
    ".sponsio/credentials",
)
"""Restored on change: each is read live by a host or by Sponsio."""

_GUARDED_TREES = (
    ".claude/skills/sponsio",
    ".cursor/skills/sponsio",
    ".openclaw/skills/sponsio",
    ".openclaw/extensions/sponsio-openclaw",
    ".sponsio/plugins",
    ".sponsio/state",
    ".sponsio/sponsio.sock",
    ".sponsio/admin.sock",
)
"""Reported on change, never modified by the guard."""


def _signature(path: Path):
    try:
        st = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as e:  # unreadable: compare the error, not a stat
        return ("error", type(e).__name__)
    if path.is_dir() and not path.is_symlink():
        entries = []
        for sub in sorted(path.rglob("*")):
            try:
                sst = sub.lstat()
            except OSError:
                continue
            entries.append((str(sub.relative_to(path)), sst.st_mtime_ns, sst.st_size))
        return ("dir", tuple(entries))
    return ("file", st.st_mtime_ns, st.st_size, st.st_ino)


def _snapshot() -> dict[str, object]:
    return {
        rel: _signature(_REAL_HOME / rel) for rel in _GUARDED_FILES + _GUARDED_TREES
    }


_BASELINE: dict[str, object] = {}
_BACKUP_DIR: Path | None = None


def _take_baseline() -> None:
    global _BACKUP_DIR
    _BASELINE.update(_snapshot())
    _BACKUP_DIR = Path(tempfile.mkdtemp(prefix="sponsio-real-home-backup-"))
    for rel in _GUARDED_FILES:
        src = _REAL_HOME / rel
        if src.is_file():
            dst = _BACKUP_DIR / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def _restore_file(rel: str, test_id: str) -> str:
    assert _BACKUP_DIR is not None
    target = _REAL_HOME / rel
    safe_id = "".join(c if c.isalnum() or c in "._-" else "_" for c in test_id)[-120:]
    kept = _BACKUP_DIR / "changed" / safe_id / rel
    kept.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.copy2(target, kept)
    original = _BACKUP_DIR / rel
    if original.is_file():
        shutil.copy2(original, target)
        return f"restored {target} (changed version kept at {kept})"
    if target.exists():
        target.unlink()
        return f"removed {target}, which did not exist before (kept at {kept})"
    return f"{target} changed and is gone again"


@pytest.fixture(autouse=True)
def _real_home_is_off_limits(request):
    yield
    now = _snapshot()
    changed = [rel for rel, sig in now.items() if sig != _BASELINE.get(rel)]
    if not changed:
        return
    notes = []
    for rel in changed:
        if rel in _GUARDED_FILES:
            notes.append(_restore_file(rel, request.node.nodeid))
            _BASELINE[rel] = _signature(_REAL_HOME / rel)
        else:
            notes.append(f"changed: {_REAL_HOME / rel} (left as is)")
    pytest.exit(
        f"{request.node.nodeid} wrote under the real home directory "
        f"{_REAL_HOME}. Tests must write only under their own temp dir.\n  "
        + "\n  ".join(notes)
        + f"\n  session-start backup: {_BACKUP_DIR}",
        returncode=3,
    )


def pytest_configure(config):  # noqa: ARG001 — pytest hook signature
    # Set BEFORE any test imports `sponsio.integrations.base`, so the
    # module-level ``_VALID_MODES`` constant and any cached env reads
    # see the right value from the start.  Using ``setdefault`` so a
    # caller-supplied env var (e.g. ``SPONSIO_MODE=observe pytest``)
    # still wins — useful for running the suite against the new
    # production default to find what the next round of fixes is.
    os.environ.setdefault("SPONSIO_MODE", "enforce")
    _take_baseline()


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

"""Bundled scaffolding installed alongside the package.

Used by ``sponsio init --with-example`` to drop a runnable corpus
into a fresh project.  Files mirror ``examples/eval/`` in the repo
— the dev-time edits happen there, ``scripts/sync_init_examples.py``
copies them here, and ``tests/test_init_examples_sync.py`` enforces
they stay identical.

Why ship a duplicate?  Because ``pip install sponsio`` only carries
files inside the ``sponsio/`` package tree; ``examples/`` lives at
repo root and would otherwise be missing in installed wheels.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path


def example_root(name: str = "eval") -> Path:
    """Filesystem path to a bundled example directory.

    ``importlib.resources.files`` is the modern, package-loader-aware
    way to do this — works for editable installs, wheels, and zipped
    packages alike.  We materialise to a real ``Path`` because the
    caller (the wizard) shells out to ``shutil.copytree`` which needs
    one; for a pure-stream API use ``files()`` directly.
    """
    root = resources.files(__name__) / name
    # ``files()`` may return a ``MultiplexedPath`` or a ``Traversable``;
    # turning it into ``Path`` via ``str`` is the documented escape
    # hatch and works for the common (non-zipped) install layouts.
    return Path(str(root))

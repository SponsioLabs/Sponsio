"""Lookup for bundled plugin default libraries.

Single source of truth for what's shipped under
``sponsio/plugin/defaults/<name>.yaml`` — used by both
``sponsio plugin init`` (which copies ``_host`` automatically) and
``sponsio plugin install <name>`` (which copies any library by name).

The registry is filesystem-driven: any ``*.yaml`` dropped into
``sponsio/plugin/defaults/`` is automatically discoverable. New
starter libraries don't need a code change beyond adding the file.
"""

from __future__ import annotations

from importlib import resources


def list_bundled() -> list[str]:
    """Return the sorted names of bundled default libraries.

    A "name" is the yaml stem under ``sponsio/plugin/defaults/`` —
    e.g. ``"_host"``, ``"github"``, ``"filesystem"``, ``"playwright"``.
    """
    root = resources.files("sponsio.plugin").joinpath("defaults")
    names: list[str] = []
    for entry in root.iterdir():
        name = entry.name
        if name.endswith(".yaml"):
            names.append(name[: -len(".yaml")])
    return sorted(names)


def read_bundled(name: str) -> str:
    """Return the full text of a bundled library, or raise FileNotFoundError."""
    src = resources.files("sponsio.plugin").joinpath(f"defaults/{name}.yaml")
    return src.read_text(encoding="utf-8")

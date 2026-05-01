"""Stub for the sto atom registry.

The real registry + atom catalog is a Sponsio Cloud feature. The OSS
engine ships only the deterministic pipeline; sto atoms (``no_pii``,
``tone_polite``, ``injection_free`` …) require ``sponsio[cloud]``.

This stub keeps lazy imports intact so the OSS package loads cleanly;
calls into the registry return empty results so callers that ask
"which sto atoms are registered?" simply see "none."
"""

from __future__ import annotations

from typing import Any

# Empty registry — no sto atoms are available in the OSS build.
_REGISTRY: dict[str, Any] = {}


def list_sto_atoms() -> list[str]:
    """Return registered sto atom names. Empty in OSS — see module doc."""
    return []


def list_sto_atom_infos() -> list[dict]:
    """Return registered sto atom metadata. Empty in OSS — see module doc."""
    return []


def get_sto_atom_info(name: str) -> dict | None:
    """Return metadata for an atom, or ``None``. Always ``None`` in OSS."""
    return None


def register_sto_atom(*args, **kwargs):
    """Decorator stub — accepts and returns the wrapped function unchanged.

    Lets cloud-only modules import this in OSS without crashing; the
    registration just becomes a no-op since the registry is read-only
    in OSS builds.
    """

    def _identity(fn):
        return fn

    if args and callable(args[0]) and not kwargs:
        return _identity(args[0])
    return _identity

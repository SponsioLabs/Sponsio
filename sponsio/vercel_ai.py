"""Vercel AI SDK-friendly Sponsio entry points.

Usage::

    from sponsio.vercel_ai import Sponsio
    from sponsio import contract

    guard = Sponsio(
        agent_id="vercel_bot",
        contracts=[
            contract("tool budget").enforce("tool `search` at most 5 times"),
        ],
    )
    config = guard.wrap(stream_text_config)
"""

from __future__ import annotations

from typing import Any

from sponsio.core import Sponsio as _Sponsio
from sponsio.integrations.vercel_ai import VercelAIGuard


def Sponsio(**kwargs: Any) -> VercelAIGuard:  # noqa: N802 — branded factory
    """Create a Vercel AI guard without passing ``framework="vercel_ai"``."""
    return _Sponsio(framework="vercel_ai", **kwargs)


__all__ = ["Sponsio", "VercelAIGuard"]

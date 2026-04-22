"""OpenAI SDK-friendly Sponsio entry points.

Usage::

    from sponsio.openai import Sponsio
    from sponsio import contract
    import openai

    guard = Sponsio(
        agent_id="chatbot",
        contracts=[
            contract("no PII in responses")
                .enforce("response must not contain PII"),
        ],
    )
    client = guard.wrap(openai.OpenAI())
"""

from __future__ import annotations

from typing import Any

from sponsio.core import Sponsio as _Sponsio
from sponsio.integrations.openai import OpenAIGuard, patch_openai, unpatch_openai


def Sponsio(**kwargs: Any) -> OpenAIGuard:  # noqa: N802 — branded factory
    """Create an OpenAI guard without passing ``framework="openai"``."""
    return _Sponsio(framework="openai", **kwargs)


__all__ = ["OpenAIGuard", "Sponsio", "patch_openai", "unpatch_openai"]

"""Claude Agent SDK-friendly Sponsio entry points.

Usage::

    from sponsio.claude_agent import Sponsio
    from sponsio import contract
    from claude_agent_sdk import ClaudeSDKClient

    guard = Sponsio(
        agent_id="research_bot",
        contracts=[
            contract("redact PII before send")
                .assume("response contains PII")
                .enforce("response must redact PII"),
        ],
    )
    client = ClaudeSDKClient(options=guard.wrap(options))
"""

from __future__ import annotations

from typing import Any

from sponsio.core import Sponsio as _Sponsio
from sponsio.integrations.claude_agent import ClaudeAgentGuard


def Sponsio(**kwargs: Any) -> ClaudeAgentGuard:  # noqa: N802 — branded factory
    """Create a Claude Agent guard without passing ``framework="claude_agent"``."""
    return _Sponsio(framework="claude_agent", **kwargs)


__all__ = ["ClaudeAgentGuard", "Sponsio"]

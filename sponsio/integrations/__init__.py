"""Framework integrations for Sponsio.

Each guard class inherits from BaseGuard and adapts contract enforcement
to a specific agent framework:

- LangGraphGuard  — LangGraph (tool wrapping + ToolNode)
- MCPContractProxy — Model Context Protocol (tool proxy)
- OpenAIGuard     — OpenAI Chat Completions (patch/unpatch)
- CrewAIGuard     — CrewAI (before/after hooks)
- AgentsSDKGuard  — OpenAI Agents SDK (tool wrapping)
"""

from sponsio.integrations.base import BaseGuard, CheckResult

__all__ = ["BaseGuard", "CheckResult"]

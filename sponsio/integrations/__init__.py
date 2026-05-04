"""Framework integrations for Sponsio.

Each guard class inherits from BaseGuard and adapts contract enforcement
to a specific agent framework:

- LangGraphGuard    -- LangGraph (tool wrapping + ToolNode)
- ClaudeAgentGuard  -- Claude Agent SDK (hooks, no tool wrapping)
- OpenAIGuard       -- OpenAI Chat Completions (patch/unpatch)
- AgentsSDKGuard    -- OpenAI Agents SDK (tool wrapping)
- VercelAIGuard     -- Vercel AI SDK (middleware)
- CrewAIGuard       -- CrewAI (before/after hooks)
- GoogleADKGuard    -- Google ADK (function tool wrapping)
- MCPContractProxy  -- Model Context Protocol (tool proxy)

Plus the host-plugin bridge (Mode A):

- CursorHostGuard       -- Cursor IDE host adapter
- claude_code_install   -- installer for Claude Code plugin
- openclaw_install      -- installer for OpenClaw plugin
"""

from sponsio.integrations.base import BaseGuard, CheckResult

__all__ = ["BaseGuard", "CheckResult"]

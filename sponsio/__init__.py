"""Sponsio — Runtime contract enforcement for LLM agent systems.

Quick start (LangGraph)::

    from sponsio.langgraph import Sponsio
    from langgraph.prebuilt import create_react_agent

    guard = Sponsio(
        agent_id="bot",
        contracts=["tool `issue_refund` at most 1 times"],
    )
    agent = create_react_agent(model, guard.wrap(tools))

Recommended — fluent contract builder::

    from sponsio import contract
    from sponsio.langgraph import Sponsio

    guard = Sponsio(
        agent_id="bot",
        contracts=[
            contract("refund needs policy check")
                .assume("called `issue_refund`")
                .guarantees("must call `check_policy` before `issue_refund`"),
        ],
    )

Config-driven::

    from sponsio.langgraph import Sponsio

    guard = Sponsio(
        config="sponsio.yaml",
        agent_id="customer_bot",
    )

Direct guard import (advanced)::

    from sponsio import LangGraphGuard
    guard = LangGraphGuard(contracts=[...])
"""

__version__ = "0.2.0a3"

# --- Main entry point ---
from sponsio.core import Sponsio

# --- Config + contract builder ---
from sponsio.config import load_config
from sponsio.contract import ContractBuilder, contract

# --- Core models (users occasionally need these) ---
from sponsio.models.agent import Agent
from sponsio.models.contract import Contract
from sponsio.models.system import System
from sponsio.models.trace import Event, Trace


# --- Framework guards (lazy imports to avoid pulling optional deps) ---


def __getattr__(name: str):
    """Lazy-load framework guards to avoid importing optional dependencies."""
    _guard_map = {
        "LangGraphGuard": "sponsio.integrations.langgraph",
        "OpenAIGuard": "sponsio.integrations.openai",
        "CrewAIGuard": "sponsio.integrations.crewai",
        "AgentsSDKGuard": "sponsio.integrations.agents",
        "MCPContractProxy": "sponsio.integrations.mcp",
        "VercelAIGuard": "sponsio.integrations.vercel_ai",
        "ClaudeAgentGuard": "sponsio.integrations.claude_agent",
        "GoogleADKGuard": "sponsio.integrations.google_adk",
        # Backward compat aliases
        "ContractGuard": "sponsio.integrations.langgraph",
        "AgentsGuard": "sponsio.integrations.agents",
    }

    if name in _guard_map:
        import importlib

        module = importlib.import_module(_guard_map[name])
        attr = getattr(module, name)
        globals()[name] = attr
        return attr

    if name == "patch_openai":
        from sponsio.integrations.openai import patch_openai

        globals()["patch_openai"] = patch_openai
        return patch_openai

    if name == "unpatch_openai":
        from sponsio.integrations.openai import unpatch_openai

        globals()["unpatch_openai"] = unpatch_openai
        return unpatch_openai

    raise AttributeError(f"module 'sponsio' has no attribute {name!r}")


__all__ = [
    # Main entry point
    "Sponsio",
    "__version__",
    "load_config",
    # Contract builder (recommended for (A, G) pairs)
    "contract",
    "ContractBuilder",
    # Core models (for power users building custom integrations)
    "Agent",
    "Contract",
    "System",
    "Trace",
    "Event",
    # OpenAI monkey-patch (no Sponsio() equivalent for unpatch)
    "patch_openai",
    "unpatch_openai",
]

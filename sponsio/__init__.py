"""Sponsio — Runtime contract enforcement for LLM agent systems.

Quick start::

    import sponsio

    guard = sponsio.init(
        framework="langgraph",
        agent_id="bot",
        contracts=["tool `issue_refund` at most 1 times"],
    )
    agent = create_react_agent(model, guard.wrap(tools))

Config-driven::

    guard = sponsio.init(
        framework="langgraph",
        config="sponsio.yaml",
        agent_id="customer_bot",
    )

Direct guard import (advanced)::

    from sponsio import LangGraphGuard
    guard = LangGraphGuard(contracts=[...])
"""

__version__ = "0.1.0a0"

# --- Main entry point ---
from sponsio.core import init

# --- Config ---
from sponsio.config import load_config

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
    "init",
    "__version__",
    "load_config",
    # Core models (for power users building custom integrations)
    "Agent",
    "Contract",
    "System",
    "Trace",
    "Event",
    # OpenAI monkey-patch (no init() equivalent for unpatch)
    "patch_openai",
    "unpatch_openai",
]

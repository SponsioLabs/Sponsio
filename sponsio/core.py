"""Sponsio — the contract layer for LLM agent systems.

This is the main entry point for Sponsio. Usage:

**Quick start — list of unconditional rules (bare strings):**

    import sponsio

    guard = sponsio.init(
        framework="langgraph",
        agent_id="bot",
        contracts=[
            "tool `issue_refund` at most 1 times",
            "tool `check_policy` must precede `issue_refund`",
        ],
        dashboard=True,
    )
    agent = create_react_agent(model, guard.wrap(tools))

**Mixed: bare strings + per-contract (A, E) pairs:**

    guard = sponsio.init(
        agent_id="bot",
        contracts=[
            "tool `sed` arg contains `-i` is banned",   # unconditional
            {
                "assumption": "called `cancel_order`",
                "enforcement": "must call `get_order_details` before `cancel_order`",
            },
            {
                "assumption": ["A1", "A2"],             # list = AND
                "enforcement": ["E1", "E2"],
            },
        ],
    )

**Config-driven:**

    guard = sponsio.init(
        framework="langgraph",
        config="sponsio.yaml",
        agent_id="bot",
    )
"""

from __future__ import annotations

import sys
import threading
from typing import Any

from sponsio.integrations.base import BaseGuard

# ---------------------------------------------------------------------------
# Framework registry — maps framework name to (module_path, class_name)
# ---------------------------------------------------------------------------

_FRAMEWORK_REGISTRY: dict[str, tuple[str, str]] = {
    "langgraph": ("sponsio.integrations.langgraph", "LangGraphGuard"),
    "mcp": ("sponsio.integrations.mcp", "MCPContractProxy"),
    "openai": ("sponsio.integrations.openai", "OpenAIGuard"),
    "crewai": ("sponsio.integrations.crewai", "CrewAIGuard"),
    "agents_sdk": ("sponsio.integrations.agents", "AgentsSDKGuard"),
    "vercel_ai": ("sponsio.integrations.vercel_ai", "VercelAIGuard"),
    "claude_agent": ("sponsio.integrations.claude_agent", "ClaudeAgentGuard"),
}


def _resolve_guard_class(framework: str | None) -> type:
    """Resolve a framework name to a Guard class."""
    if framework is None:
        return BaseGuard

    key = framework.lower().replace("-", "_").replace(" ", "_")

    if key not in _FRAMEWORK_REGISTRY:
        available = ", ".join(sorted(_FRAMEWORK_REGISTRY.keys()))
        raise ValueError(f"Unknown framework {framework!r}. Available: {available}")

    module_path, class_name = _FRAMEWORK_REGISTRY[key]

    import importlib

    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise ImportError(
            f"Framework {framework!r} requires additional dependencies. "
            f"Install with: pip install 'sponsio[{key}]'"
        ) from e

    return getattr(module, class_name)


# ---------------------------------------------------------------------------
# Dashboard auto-start
# ---------------------------------------------------------------------------

_dashboard_thread: threading.Thread | None = None
_dashboard_port: int | None = None


def _start_dashboard(host: str = "127.0.0.1", port: int = 8000) -> str:
    """Start the Sponsio dashboard server in a background daemon thread."""
    global _dashboard_thread, _dashboard_port

    if _dashboard_thread is not None and _dashboard_thread.is_alive():
        return f"http://{host}:{_dashboard_port}"

    try:
        import uvicorn  # noqa: F401
    except ImportError:
        raise ImportError(
            "Dashboard requires 'uvicorn' and 'fastapi'. "
            "Install with: pip install 'sponsio[web]'"
        )

    def _run() -> None:
        import uvicorn as _uvicorn

        _uvicorn.run("api.main:app", host=host, port=port, log_level="warning")

    _dashboard_port = port
    _dashboard_thread = threading.Thread(
        target=_run, daemon=True, name="sponsio-dashboard"
    )
    _dashboard_thread.start()

    url = f"http://{host}:{port}"
    print(f"\033[36mSponsio dashboard:\033[0m {url}", file=sys.stderr)
    return url


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def init(
    framework: str | None = None,
    agent_id: str = "agent",
    config: str | None = None,
    contracts: list[dict | str] | None = None,
    dashboard: bool | str | None = None,
    verbose: bool = True,
    verbosity: int = 1,
    otel_exporter: Any | None = None,
    mode: str | None = None,
    **kwargs: Any,
) -> BaseGuard:
    """Initialize Sponsio contract enforcement for an agent.

    Args:
        framework: One of "langgraph", "mcp", "openai", "crewai",
            "agents_sdk", "vercel_ai". If None, returns BaseGuard.
        agent_id: Logical name for the agent.
        config: Path to a sponsio.yaml config file.
        contracts: List of contract entries. Each entry is either a bare
            NL string (unconditional shortcut) or a dict with
            ``assumption`` (optional, scalar or list) and ``enforcement``
            (required, scalar or list). Each entry becomes one
            independent ``Contract``; assumptions never cross contracts.
        dashboard: True (auto-start), str (URL), or None/False.
        verbose: Enable terminal output (default True).
        verbosity: Detail level (0=violations, 1=all, 2=spans).
        otel_exporter: Optional OTEL exporter for span export.
        mode: Enforcement mode. ``"enforce"`` (default) blocks on det
            violations and retries on sto. ``"observe"`` (shadow mode)
            logs every violation to
            ``~/.sponsio/sessions/<agent_id>/*.jsonl`` without blocking
            — the recommended first-run setting when adopting Sponsio on
            a live agent. The ``SPONSIO_MODE`` environment variable
            overrides this argument.

    Returns:
        A configured Guard instance.
    """
    guard_cls = _resolve_guard_class(framework)

    # Dashboard
    dashboard_url: str | None = None
    if dashboard is True:
        dashboard_url = _start_dashboard()
    elif isinstance(dashboard, str):
        dashboard_url = dashboard

    # Config mode
    if config is not None:
        if contracts is not None:
            raise ValueError(
                "Cannot combine 'config' with 'contracts'. "
                "Use either a config file or inline contracts, not both."
            )

        from sponsio.config import config_to_guard_kwargs, load_config

        parsed = load_config(config)

        # Auto-infer agent_id when user didn't specify and config has one agent
        if agent_id == "agent" and agent_id not in parsed.agents:
            if len(parsed.agents) == 1:
                agent_id = next(iter(parsed.agents))
            elif len(parsed.agents) > 1:
                available = list(parsed.agents.keys())
                raise ValueError(
                    f"Config has multiple agents {available}. "
                    f"Please specify agent_id=... explicitly."
                )

        cfg_kwargs = config_to_guard_kwargs(parsed, agent_id)
        cfg_kwargs["verbose"] = verbose
        cfg_kwargs["verbosity"] = verbosity
        if dashboard_url is not None:
            cfg_kwargs["dashboard_url"] = dashboard_url
        if otel_exporter is not None:
            cfg_kwargs["otel_exporter"] = otel_exporter
        if mode is not None:
            cfg_kwargs["mode"] = mode
        cfg_kwargs.update(kwargs)

        return guard_cls(**cfg_kwargs)

    # Inline mode
    return guard_cls(
        agent_id=agent_id,
        contracts=contracts,
        verbose=verbose,
        verbosity=verbosity,
        dashboard_url=dashboard_url,
        otel_exporter=otel_exporter,
        mode=mode,
        **kwargs,
    )

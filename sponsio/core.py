"""Sponsio — the contract layer for LLM agent systems.

The main entry point is the framework-specific factory function
``Sponsio()``. Pick the import that matches your stack:

**Quick start (LangGraph) — fluent contract builder:**

    from langgraph.prebuilt import create_react_agent

    from sponsio import contract
    from sponsio.langgraph import Sponsio

    guard = Sponsio(
        agent_id="bot",
        contracts=[
            # Conditional (A, E) pair — assumption triggers the enforcement
            contract("policy gate before refund")
                .assume("called `issue_refund`")
                .enforce("must call `check_policy` before `issue_refund`"),
            # Unconditional rule — no .assume(), only .enforce()
            contract("refund rate limit")
                .enforce("tool `issue_refund` at most 1 times"),
        ],
        dashboard=True,
    )
    agent = create_react_agent(model, guard.wrap(tools))

Every contract is built with ``contract(desc).enforce(...)``, with an
optional ``.assume(...)`` in front for rules that have a trigger. Repeated
``.assume(...)`` / ``.enforce(...)`` calls AND the arguments together::

    contract("multi-condition policy")
        .assume("A1")
        .assume("A2")                           # A1 AND A2
        .enforce("E1")
        .enforce("E2")                          # E1 AND E2

Bare NL strings are still accepted at the parser level as a shortcut,
but the fluent builder is the documented pattern because it always
attaches a human-facing description for reports.

**Config-driven:**

    from sponsio.langgraph import Sponsio

    guard = Sponsio(
        config="sponsio.yaml",
        agent_id="bot",
    )

The framework-agnostic factory ``sponsio.core.Sponsio`` is also available;
the framework shims (``sponsio.langgraph``, ``sponsio.openai``,
``sponsio.crewai``, ``sponsio.claude_agent``, ``sponsio.agents``,
``sponsio.vercel_ai``) are thin wrappers that pre-fill ``framework=...``.
"""

from __future__ import annotations

import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from sponsio.constants import (
    DASHBOARD_DEFAULT_HOST,
    DASHBOARD_DEFAULT_PORT,
    default_dashboard_url,
)
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

_FRAMEWORK_EXTRAS: dict[str, str] = {
    "agents_sdk": "agents-sdk",
    "claude_agent": "claude-agent",
    "vercel_ai": "vercel-ai",
}


def _resolve_guard_class(framework: str | None) -> type:
    """Resolve a framework name to a Guard class."""
    if framework is None:
        return BaseGuard

    key = framework.lower().replace("-", "_").replace(" ", "_")
    if key not in _FRAMEWORK_REGISTRY:
        available = ", ".join(sorted(_FRAMEWORK_REGISTRY))
        raise ValueError(f"Unknown framework {framework!r}. Available: {available}")

    module_path, class_name = _FRAMEWORK_REGISTRY[key]

    import importlib

    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        extra = _FRAMEWORK_EXTRAS.get(key, key)
        raise ImportError(
            f"Framework {framework!r} requires additional dependencies. "
            f"Install with: pip install 'sponsio[{extra}]'"
        ) from e

    return getattr(module, class_name)


# ---------------------------------------------------------------------------
# Dashboard helper
# ---------------------------------------------------------------------------

_dashboard_lock = threading.Lock()
_dashboard_started: bool = False
_dashboard_url: str | None = None


def _dashboard_api_reachable(
    host: str = DASHBOARD_DEFAULT_HOST,
    port: int = DASHBOARD_DEFAULT_PORT,
    *,
    timeout: float = 0.4,
) -> bool:
    """True if something responds to ``GET /api/health`` (e.g. ``sponsio serve``)."""
    url = f"http://{host}:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _start_dashboard() -> str:
    """Ensure the dashboard API is reachable, starting it when possible (idempotent).

    Uses the same default host/port as ``sponsio serve`` and the Vite dev proxy
    (:data:`DASHBOARD_DEFAULT_PORT`, currently **8000**). If an API is already
    running (e.g. the user started ``sponsio serve`` in another terminal), we
    reuse it and do **not** bind a second server.

    When the full ``api/`` app is not on disk (typical ``pip install`` from
    PyPI), we only print a hint and still return the canonical URL so
    ``dashboard_url`` is consistent; start the app from a git checkout with
    ``sponsio serve`` for the full UI.
    """
    global _dashboard_started, _dashboard_url

    base = default_dashboard_url()
    host, port = DASHBOARD_DEFAULT_HOST, DASHBOARD_DEFAULT_PORT

    with _dashboard_lock:
        if _dashboard_started and _dashboard_url is not None:
            return _dashboard_url

        # Another process (or an earlier `sponsio serve`) already owns 8000.
        if _dashboard_api_reachable(host, port):
            _dashboard_started = True
            _dashboard_url = base
            print(
                f"[sponsio] dashboard API already up at {base} — reusing (no second server).",
                file=sys.stderr,
            )
            return base

        # Developer checkout: `sponsio/` lives under repo root next to `api/`.
        repo_root = Path(__file__).resolve().parent.parent
        api_main = repo_root / "api" / "main.py"
        if not api_main.is_file():
            _dashboard_started = True
            _dashboard_url = base
            print(
                "[sponsio] full dashboard API not bundled; for the UI + /api, clone the "
                "Sponsio repo and run from its root: `sponsio serve` "
                f"(listens on {base}). Pushes will target that URL if you start it.\n"
                f"[sponsio] (optional) `pip install 'sponsio[web]'` for uvicorn; "
                "the API app ships with the repository, not the PyPI wheel alone.",
                file=sys.stderr,
            )
            return base

        try:
            import uvicorn
        except ImportError as e:
            raise ImportError(
                "Dashboard auto-start needs uvicorn. "
                "Install with: pip install 'sponsio[web]'"
            ) from e

        root_str = str(repo_root)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)
        from api.main import app  # type: ignore[import-not-found,import-untyped]

        def _run() -> None:
            uvicorn.run(app, host=host, port=port, log_level="warning")

        thread = threading.Thread(target=_run, name="sponsio-dashboard", daemon=True)
        thread.start()

        for _ in range(60):
            if _dashboard_api_reachable(host, port, timeout=0.25):
                break
            time.sleep(0.1)
        else:
            print(
                f"[sponsio] warning: started dashboard thread on {base} but "
                f"/api/health did not become ready; check for port conflict.",
                file=sys.stderr,
            )

        _dashboard_started = True
        _dashboard_url = base
        print(f"[sponsio] dashboard → {base}", file=sys.stderr)
        return base


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def Sponsio(  # noqa: N802 — branded factory function
    framework: str | None = None,
    agent_id: str = "agent",
    config: str | None = None,
    contracts: list[Any] | None = None,
    dashboard: bool | str | None = None,
    verbose: bool = True,
    verbosity: int = 1,
    otel_exporter: Any | None = None,
    mode: str | None = None,
    sto_judge: Any | None = None,
    **kwargs: Any,
) -> BaseGuard:
    """Create a Sponsio guard for a given framework.

    Most users should import the framework-specific factory instead, e.g.::

        from sponsio.langgraph import Sponsio
        from sponsio.openai import Sponsio
        from sponsio.crewai import Sponsio

    which pre-fills ``framework=...`` and returns the right Guard class.

    Args:
        framework: One of "langgraph", "mcp", "openai", "crewai",
            "agents_sdk", "vercel_ai", "claude_agent". If None, returns
            BaseGuard (framework-agnostic).
        agent_id: Logical name for the agent.
        config: Path to a sponsio.yaml config file.
        contracts: List of contract entries. Each entry is one of:

            - a bare NL string (unconditional shortcut), e.g.
              ``"tool `issue_refund` at most 1 times"``
            - a :class:`~sponsio.contract.ContractBuilder` from
              :func:`sponsio.contract` — recommended for any (A, E) pair
            - a dict with ``assumption`` (optional) + ``enforcement``
              (legacy form, still supported)

            Each entry becomes one independent ``Contract``; assumptions
            never cross contracts.
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
        sto_judge: A :class:`BooleanJudge` (or compatible) used by sto
            atom evaluators in this guard. Recommended over the legacy
            module-level ``set_default_judge()``. Example::

                from sponsio.runtime.judge import BooleanJudge
                from sponsio.runtime.llm_client import OpenAILogprobClient
                import openai

                from sponsio.langgraph import Sponsio

                guard = Sponsio(
                    contracts=[...],
                    sto_judge=BooleanJudge(
                        OpenAILogprobClient(openai.OpenAI(), "gpt-4o-mini")
                    ),
                )

            If omitted, falls back to the global judge set by
            :func:`sponsio.patterns.sto_catalog.set_default_judge` (or
            raises ``RuntimeError`` when a sto contract evaluates and
            neither is configured).

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
        if sto_judge is not None:
            cfg_kwargs["sto_judge"] = sto_judge
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
        sto_judge=sto_judge,
        **kwargs,
    )

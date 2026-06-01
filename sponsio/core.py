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
            # Conditional (A, G) pair — assumption triggers the enforcement
            contract("policy gate before refund")
                .assume("called `issue_refund`")
                .guarantees("must call `check_policy` before `issue_refund`"),
            # Unconditional rule — no .assume(), only .guarantees()
            contract("refund rate limit")
                .guarantees("tool `issue_refund` at most 1 times"),
        ],
        dashboard=True,
    )
    agent = create_react_agent(model, guard.wrap(tools))

Every contract is built with ``contract(desc).guarantees(...)``, with an
optional ``.assume(...)`` in front for rules that have a trigger. Repeated
``.assume(...)`` / ``.guarantees(...)`` calls AND the arguments together::

    contract("multi-condition policy")
        .assume("A1")
        .assume("A2")                           # A1 AND A2
        .guarantees("E1")
        .guarantees("E2")                          # E1 AND E2

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
``sponsio.vercel_ai``, ``sponsio.google_adk``) are thin wrappers that
pre-fill ``framework=...``.
"""

from __future__ import annotations

import os
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


def _coerce_dashboard_env(value: str) -> str | bool | None:
    """Parse the ``SPONSIO_DASHBOARD`` env var into a dashboard argument.

    Mirrors :func:`sponsio.config._parse_runtime_section` so a YAML
    setting and an env-var setting produce the same resolved value.
    """
    stripped = value.strip()
    lowered = stripped.lower()
    if lowered in ("", "none", "null"):
        return None
    if lowered in ("true", "yes", "on", "1"):
        return True
    if lowered in ("false", "no", "off", "0"):
        return False
    return stripped  # URL


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
    "google_adk": ("sponsio.integrations.google_adk", "GoogleADKGuard"),
}

_FRAMEWORK_EXTRAS: dict[str, str] = {
    "agents_sdk": "agents-sdk",
    "claude_agent": "claude-agent",
    "google_adk": "google-adk",
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

        # Add the repo root to ``sys.path`` only for the duration of the
        # ``api.main`` import. Once that import completes, the FastAPI app
        # plus every router/submodule are cached in ``sys.modules`` and
        # neither uvicorn nor any later guard import needs the path on the
        # search list. Leaving it permanently inserted lets a process that
        # later instantiates a guard from a different repo accumulate
        # path entries — and worst-case shadow stdlib modules if the repo
        # happens to contain a same-named directory.
        root_str = str(repo_root)
        path_added = root_str not in sys.path
        if path_added:
            sys.path.insert(0, root_str)
        try:
            from api.main import app  # type: ignore[import-not-found,import-untyped]
        finally:
            if path_added:
                try:
                    sys.path.remove(root_str)
                except ValueError:
                    pass  # Someone else already removed it; fine.

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
    tool_policy: dict[str, Any] | Any | None = None,
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
            "agents_sdk", "vercel_ai", "claude_agent", "google_adk".
            If None, returns BaseGuard (framework-agnostic).
        agent_id: Logical name for the agent.
        config: Path to a sponsio.yaml config file.
        contracts: List of contract entries. Each entry is one of:

            - a bare NL string (unconditional shortcut), e.g.
              ``"tool `issue_refund` at most 1 times"``
            - a :class:`~sponsio.contract.ContractBuilder` from
              :func:`sponsio.contract` — recommended for any (A, G) pair
            - a dict with ``assumption`` (optional) + ``enforcement``
              (legacy form, still supported)

            Each entry becomes one independent ``Contract``; assumptions
            never cross contracts.
        dashboard: True (auto-start), str (URL), or None/False.
            Falls back to ``SPONSIO_DASHBOARD`` env var, then to the
            ``runtime.dashboard`` field in ``sponsio.yaml`` when
            ``config=`` is given. Precedence:
            ctor arg > env > yaml > None (disabled).
            The env var also applies to inline guards (no ``config=``)
            so leaving ``SPONSIO_DASHBOARD`` set system-wide will
            attach a dashboard to every Sponsio instance in the process.
        verbose: Enable terminal output (default True).
        verbosity: Detail level (0=violations, 1=all, 2=spans).
        otel_exporter: Optional OTEL exporter for span export.
        mode: Enforcement mode. ``"enforce"`` blocks on det violations;
            ``"observe"`` (default) logs every violation to
            ``~/.sponsio/sessions/<agent_id>/*.jsonl`` without blocking,
            the recommended first-run setting when adopting Sponsio on a
            live agent. Falls back to ``SPONSIO_MODE`` env var, then to
            ``runtime.mode`` in ``sponsio.yaml`` when ``config=`` is
            given. Precedence: env > ctor arg > yaml > ``"observe"``.
        sto_judge: A judge object consumed by sto atom evaluators. The
            sto pipeline is an extension point: this build ships no
            evaluator implementation and rejects sto contracts at config
            load, so the judge path is never reached. If omitted, falls
            back to the global judge set by the catalog's
            ``set_default_judge`` hook (or raises ``RuntimeError`` when
            a sto contract evaluates and neither is configured).
        tool_policy: Inline equivalent of the YAML ``tool_policy:``
            section. Accepts the same dict shape::

                tool_policy={
                    "default": "deny",          # allow | deny
                    "approved": ["search"],     # tools permitted under deny
                    "enforcement": "reactive",  # reactive | proactive
                }

            When set with ``default: deny``, a ``tool_allowlist`` det
            contract is synthesized and prepended to ``contracts``.
            Mutually exclusive with ``config=`` (YAML wins; pass via
            the YAML's ``tool_policy:`` block instead). A
            ``ToolPolicySection`` instance is also accepted for callers
            that have already parsed one.

    Returns:
        A configured Guard instance.
    """
    guard_cls = _resolve_guard_class(framework)

    # --- Load YAML early so its ``runtime:`` section can feed
    # mode/dashboard resolution below.  We do NOT build the guard from
    # the parsed config yet — that still happens in the ``config
    # is not None`` branch further down.
    parsed = None
    if config is not None:
        if contracts is not None:
            raise ValueError(
                "Cannot combine 'config' with 'contracts'. "
                "Use either a config file or inline contracts, not both."
            )
        if tool_policy is not None:
            raise ValueError(
                "Cannot combine 'config' with 'tool_policy'. "
                "Set 'tool_policy:' inside the YAML file instead."
            )
        from sponsio.config import load_config

        parsed = load_config(config)

    # --- Resolve inline tool_policy → synthesized contract.
    # Accept a dict (yaml-shape) or a pre-parsed ToolPolicySection.
    # The synthesized contract is prepended to ``contracts`` in the
    # inline branch below; this resolution happens here so a misconfig
    # (e.g. unknown ``default:`` value) raises before the guard exists.
    inline_policy_contract = None
    if tool_policy is not None:
        from sponsio.config import (
            ToolPolicySection,
            _parse_tool_policy_section,
            _synthesize_tool_policy_contract,
        )

        if isinstance(tool_policy, ToolPolicySection):
            policy = tool_policy
        elif isinstance(tool_policy, dict):
            policy = _parse_tool_policy_section(tool_policy)
        else:
            raise TypeError(
                f"tool_policy must be a dict or ToolPolicySection, "
                f"got {type(tool_policy).__name__}"
            )
        inline_policy_contract = _synthesize_tool_policy_contract(policy)

    # --- Resolve mode (ctor arg > SPONSIO_MODE env > yaml > default).
    # ``_resolve_mode`` inside BaseGuard still re-checks the env var so
    # the env keeps winning even if we substitute a yaml value here;
    # what changes is the *fallback* when neither arg nor env is set.
    #
    # Yaml lookup order: ``runtime.mode`` first (typed section, parsed
    # by :func:`_parse_runtime_section`), then ``defaults.mode`` —
    # which is what ``sponsio onboard`` / ``sponsio init`` actually
    # write today.  Without the second branch, flipping ``defaults.mode:
    # observe → enforce`` was a silent no-op (the canonical onboard
    # workflow), and users had to learn the undocumented ``runtime:``
    # alternative to make the yaml authoritative.
    if mode is None and parsed is not None and "SPONSIO_MODE" not in os.environ:
        yaml_mode = parsed.runtime.mode or parsed.defaults.get("mode")
        if yaml_mode:
            mode = yaml_mode

    # --- Resolve dashboard (ctor arg > SPONSIO_DASHBOARD env > yaml > default).
    # Unlike mode, dashboard has no env-read inside BaseGuard, so we
    # apply the precedence here in full.
    if dashboard is None:
        env_dash = os.environ.get("SPONSIO_DASHBOARD")
        if env_dash is not None:
            dashboard = _coerce_dashboard_env(env_dash)
        elif parsed is not None and parsed.runtime.dashboard is not None:
            dashboard = parsed.runtime.dashboard

    # Dashboard
    dashboard_url: str | None = None
    if dashboard is True:
        dashboard_url = _start_dashboard()
    elif isinstance(dashboard, str):
        dashboard_url = dashboard

    # Config mode (build guard from parsed YAML)
    if parsed is not None:
        # When the requested agent_id isn't in the config, but the
        # config only has one agent block, fall back to that single
        # agent — it's unambiguous and saves the user from having to
        # keep `--agent <name>` on every onboard / Sponsio() call line
        # in sync with the demo's hardcoded id.  Multi-agent configs
        # still require an explicit pick (no good default).
        if agent_id not in parsed.agents:
            if len(parsed.agents) == 1:
                only_agent = next(iter(parsed.agents))
                # Surface the fallback when the user actively asked
                # for a non-default name (likely a typo or a stale
                # agent_id) so they notice if the wrong agent's rules
                # got applied.  The default "agent" sentinel doesn't
                # warn — that's the auto-infer path that's been
                # silent forever.
                if agent_id != "agent":
                    import warnings

                    warnings.warn(
                        f"agent_id={agent_id!r} not found in config; "
                        f"using the only agent defined: {only_agent!r}.",
                        UserWarning,
                        stacklevel=3,
                    )
                agent_id = only_agent
            elif len(parsed.agents) > 1:
                available = list(parsed.agents.keys())
                raise ValueError(
                    f"agent_id={agent_id!r} not found in config and "
                    f"the config defines multiple agents {available}. "
                    f"Please specify a valid agent_id explicitly."
                )

        from sponsio.config import config_to_guard_kwargs

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
        # Forward the yaml's ``tool_policy:`` block so adapters'
        # ``wrap()`` see the ``enforcement:`` knob. An explicit
        # ``tool_policy=`` kwarg still wins via the ``kwargs.update``
        # below — same precedence as ``mode`` (kwarg > yaml > default).
        cfg_kwargs["tool_policy"] = parsed.tool_policy
        cfg_kwargs.update(kwargs)

        return guard_cls(**cfg_kwargs)

    # Inline mode
    if inline_policy_contract is not None:
        contracts = [inline_policy_contract, *(contracts or [])]
    # Forward the parsed/typed policy so adapters' wrap() see the
    # ``enforcement`` knob, not just the synthesized det contract.
    if tool_policy is not None and "tool_policy" not in kwargs:
        kwargs["tool_policy"] = tool_policy
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

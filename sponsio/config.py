"""YAML contract configuration loader.

Loads a ``sponsio.yaml`` file and returns structured data for BaseGuard.

The canonical shape is a list of **contracts** under each agent — each
contract is an ``(assumption, enforcement)`` pair and is evaluated
independently. Assumptions never cross contracts.

Each contract entry accepts either the **short keys** ``A`` /
``E`` (terse, preferred for hand-edited YAML) or the **full keys**
``assumption`` / ``enforcement`` (self-describing, matches the Python
API). Mixing is fine *across* entries, but using both a short and
long form for the same field in the *same* entry raises
``ConfigError`` — pick one.

Either field may be a scalar or a list; a list is interpreted as the
logical AND of its elements.

Example::

    version: 1
    tools:
      - name: cancel_order
      - name: get_order_details
    agents:
      customer_bot:
        contracts:
          # short keys — recommended for terse hand-edited YAML
          - A: "called `cancel_order`"
            E: "must call `get_order_details` before `cancel_order`"
          - E: "tool `sed` arg contains `-i` is banned"
          # long keys — accepted when users prefer them (e.g. copied
          # from Python code)
          - assumption: ["called `modify_order`", "verified_identity"]
            enforcement:
              - "U(Not(called(modify_order)), called(get_order_details))"
              - "tool `modify_order` at most 3 times"

Within a contract entry, each NL string / scalar can also be a structured
pattern dict::

    contracts:
      - A: {pattern: called, args: [cancel_order]}
        E: {pattern: must_precede, args: [get_order_details, cancel_order]}

Usage::

    from sponsio.config import load_config, config_to_guard_kwargs

    config = load_config("sponsio.yaml")
    kwargs = config_to_guard_kwargs(config, agent_id="customer_bot")
    guard = LangGraphGuard(**kwargs)

    # Or load all agents into a System:
    system = config_to_system(config)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ToolEntry:
    """A tool definition from the ``tools`` section."""

    name: str
    description: str = ""
    params: str = ""


@dataclass
class ConstraintEntry:
    """A single constraint — one of three shapes:

    1. **NL** (``nl="every send_email needs confirmation"``) — passed to
       the structured-IR / LLM extractor at compile time.
    2. **Pattern** (``pattern="rate_limit"``, ``args=[exec, 50]``) —
       resolved against the registered pattern library.
    3. **LTL** (``ltl="G(called(exec) -> count(confirm) >= count(exec))"``)
       — raw infix LTL parsed by :func:`sponsio.formulas.parser.parse_repr`.
       This is the escape hatch for properties that mix predicate-on-arg
       conditionals with count dominance, which the structured patterns
       can't express directly (e.g. "sudo exec needs confirmation but
       plain exec doesn't").
    """

    nl: str | None = None
    pattern: str | None = None
    args: list[Any] = field(default_factory=list)
    ltl: str | None = None
    source: str | None = None

    @property
    def is_structured(self) -> bool:
        return self.pattern is not None

    @property
    def is_ltl(self) -> bool:
        return self.ltl is not None


@dataclass
class ContractEntry:
    """One (assumption, enforcement) pair from the YAML.

    Each field may hold None, one ``ConstraintEntry``, or a list of
    them (= logical AND). ``assumption`` is optional; ``enforcement`` is
    required.

    ``alpha`` and ``beta`` are resolved at parse time from one of three
    mutually-exclusive YAML specs: explicit ``alpha``/``beta``,
    ``risk_profile``, or ``costs``. Defaults (1.0, 1.0) preserve existing
    det semantics.
    """

    enforcement: ConstraintEntry | list[ConstraintEntry] = None  # type: ignore[assignment]
    assumption: ConstraintEntry | list[ConstraintEntry] | None = None
    desc: str | None = None
    alpha: float = 1.0
    beta: float = 1.0


@dataclass
class AgentConfig:
    """Parsed contract config for a single agent."""

    agent_id: str
    contracts: list[ContractEntry] = field(default_factory=list)


@dataclass
class ExtractorSection:
    """Parse-time LLM config (used by ``sponsio scan`` /
    ``UnifiedExtractor``).

    Parse-time work is offline and one-shot: latency is irrelevant,
    accuracy matters.  Most users want their best model here (e.g.
    ``gpt-4o``, ``claude-3-5-sonnet``).  Separate from ``judge``
    because the judge is on the agent's hot path and has very
    different requirements.
    """

    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None


@dataclass
class JudgeSection:
    """Runtime sto-judge config (used by ``StoEvaluator``).

    Runtime judging happens on every guarded turn — latency, cost,
    and resilience matter.  Most users want a *cheaper, faster* model
    here (e.g. ``gpt-4o-mini``, ``gemini-2.0-flash``) and care about
    the fault-tolerance knobs.

    Defaults match :class:`sponsio.runtime.evaluators.StoEvaluator`'s
    own defaults so an empty section behaves exactly like the
    programmatic default.
    """

    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    fallback_mode: str = "allow"  # allow|deny|skip
    circuit_breaker: bool = True
    failure_threshold: int = 5
    cooldown_seconds: float = 10.0


@dataclass
class PerformanceSection:
    """Runtime performance reporting config.

    Mirrors competitor-style caching config blocks in YAML position
    and naming (``performance:``) but reports a structurally
    different story: we don't need a judge cache — most checks are
    DFA and never touch an LLM in the first place.  This section
    controls *how that story gets surfaced*, not whether the speedup
    happens.

    Fields:
      * ``report``: when to print the human-readable performance
        table.  ``auto`` (default) prints at process exit only when
        the guard is ``verbose=True`` and stderr is a TTY — same
        rules as the existing session summary, so we don't clutter
        CI logs.  ``always`` forces a print even in non-TTY contexts
        (useful when redirecting to a file).  ``never`` suppresses.
      * ``export_path``: optional JSON dump path.  When set, the
        guard writes ``perf.json`` (shaped like
        :meth:`BaseGuard.performance_stats`) at process exit.
        Great for pipelines that diff perf run-over-run.
      * ``warn_slow_dfa_us``: if the pure-DFA p99 exceeds this
        threshold in μs, print a stderr warning.  Default **500μs**
        leaves headroom for GC, load, and p99 noise on healthy runs
        (typical p99s are often single-digit μs) while still firing
        well before accidental sto/LLM paths (usually ms+).  Use
        ``0`` (or any value ≤0) to disable this warning entirely.
      * ``histogram_size``: per-contract ring buffer size.  Larger
        = more accurate tail percentiles (p99 over 10k samples has
        ~3% noise; over 100k it's ~1%), at linear memory cost.
    """

    report: str = "auto"  # auto | always | never
    export_path: str | None = None
    warn_slow_dfa_us: float = 500.0
    histogram_size: int = 10_000


@dataclass
class RuntimeSection:
    """Runtime-behaviour knobs (enforcement mode, dashboard).

    Sponsio historically spread these settings across env vars
    (``SPONSIO_MODE``, ``SPONSIO_DASHBOARD``) and constructor kwargs.
    This section gives ops one place to pin them in YAML without
    losing the env-var overrides needed for per-deploy flipping.

    Precedence when :func:`sponsio.core.Sponsio` resolves each field
    (note the asymmetry — ``mode`` lets env override an explicit ctor
    arg, since ops need to flip enforcement in production without a
    code change; ``dashboard`` does not, since it's typically a
    deploy-time concern set in code)::

        mode:       SPONSIO_MODE env  >  ctor arg  >  yaml  >  "observe"
        dashboard:  ctor arg  >  SPONSIO_DASHBOARD env  >  yaml  >  None

    The env vars also apply to inline guards (``Sponsio(contracts=[...])``
    without ``config=``); only the yaml fallback requires a config.

    Fields:
      * ``mode``: ``"enforce"`` (block on det violations) or
        ``"observe"`` (shadow-mode, log only). Unset falls through to
        the BaseGuard default (``"observe"``).
      * ``dashboard``: ``true`` (auto-start local dashboard), ``false``
        (explicitly off), or a URL string. Unset behaves like no
        ``dashboard=`` kwarg (no dashboard).
    """

    mode: str | None = None
    dashboard: str | bool | None = None


@dataclass
class SponsoConfig:
    """Top-level parsed config."""

    version: str = "1"
    defaults: dict[str, Any] = field(default_factory=dict)
    tools: list[ToolEntry] = field(default_factory=list)
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    extractor: ExtractorSection = field(default_factory=ExtractorSection)
    judge: JudgeSection = field(default_factory=JudgeSection)
    performance: PerformanceSection = field(default_factory=PerformanceSection)
    runtime: RuntimeSection = field(default_factory=RuntimeSection)


class ConfigError(Exception):
    """Raised when a config file is invalid."""


# ---------------------------------------------------------------------------
# ${ENV_VAR} interpolation
# ---------------------------------------------------------------------------

# Bash-style: ``${VAR}`` or ``${VAR:-default}``.  We deliberately do
# NOT support the bare ``$VAR`` shorthand because YAML strings
# routinely contain naked dollar signs (template vars, regex,
# currency) and we don't want to accidentally munch those.
_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _interpolate_env(value: Any) -> Any:
    """Recursively expand ``${VAR}`` / ``${VAR:-default}`` in strings.

    Walks through dicts and lists in place — anything non-string /
    non-container is returned unchanged.  Missing env vars without a
    default expand to the empty string (matching shell semantics) so
    a missing key simply becomes ``api_key: ""`` rather than blowing
    up the loader; the constructor that consumes the value gets to
    decide whether empty is fatal.
    """
    if isinstance(value, str):

        def _sub(m: re.Match) -> str:
            name, default = m.group(1), m.group(2)
            return os.environ.get(name, default if default is not None else "")

        return _ENV_VAR_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(v) for v in value]
    return value


def _parse_extractor_section(raw: Any) -> ExtractorSection:
    if raw is None:
        return ExtractorSection()
    if not isinstance(raw, dict):
        raise ConfigError(f"'extractor' must be a mapping, got {type(raw).__name__}")
    return ExtractorSection(
        provider=raw.get("provider"),
        model=raw.get("model"),
        api_key=raw.get("api_key") or None,  # empty string → None
        base_url=raw.get("base_url") or None,
    )


def _parse_performance_section(raw: Any) -> PerformanceSection:
    if raw is None:
        return PerformanceSection()
    if not isinstance(raw, dict):
        raise ConfigError(f"'performance' must be a mapping, got {type(raw).__name__}")
    report = raw.get("report", "auto")
    if report not in ("auto", "always", "never"):
        raise ConfigError(
            f"performance.report must be one of auto|always|never, got {report!r}"
        )
    try:
        hs = int(raw.get("histogram_size", 10_000))
    except (TypeError, ValueError):
        raise ConfigError("performance.histogram_size must be an integer")
    if hs < 1:
        raise ConfigError("performance.histogram_size must be >= 1")
    try:
        warn = float(raw.get("warn_slow_dfa_us", 500.0))
    except (TypeError, ValueError):
        raise ConfigError("performance.warn_slow_dfa_us must be a number")
    return PerformanceSection(
        report=report,
        export_path=raw.get("export_path") or None,
        warn_slow_dfa_us=warn,
        histogram_size=hs,
    )


_VALID_RUNTIME_MODES = frozenset({"enforce", "observe"})


def _parse_runtime_section(raw: Any) -> RuntimeSection:
    """Parse the optional ``runtime:`` block.

    Validates ``mode`` against the same set :func:`_resolve_mode` uses so
    a typo (e.g. ``mode: enforece``) fails fast at load-time, not on the
    first guarded turn. ``dashboard`` coerces common string forms
    (``"true"``/``"false"``/``"none"``) into the corresponding Python
    values so ``${SPONSIO_DASHBOARD}`` interpolations from env vars
    degrade gracefully — a URL, a bool, or nothing.
    """
    if raw is None:
        return RuntimeSection()
    if not isinstance(raw, dict):
        raise ConfigError(f"'runtime' must be a mapping, got {type(raw).__name__}")

    mode = raw.get("mode")
    if mode is not None:
        if not isinstance(mode, str):
            raise ConfigError(
                f"runtime.mode must be a string, got {type(mode).__name__}"
            )
        mode = mode.strip() or None
        if mode is not None and mode not in _VALID_RUNTIME_MODES:
            raise ConfigError(
                f"runtime.mode must be one of "
                f"{sorted(_VALID_RUNTIME_MODES)}, got {mode!r}"
            )

    dashboard: str | bool | None = raw.get("dashboard")
    if isinstance(dashboard, str):
        stripped = dashboard.strip()
        lowered = stripped.lower()
        if lowered in ("", "none", "null"):
            dashboard = None
        elif lowered in ("true", "yes", "on", "1"):
            dashboard = True
        elif lowered in ("false", "no", "off", "0"):
            dashboard = False
        else:
            dashboard = stripped  # treat as URL
    elif dashboard is not None and not isinstance(dashboard, bool):
        raise ConfigError(
            f"runtime.dashboard must be bool, string URL, or null, "
            f"got {type(dashboard).__name__}"
        )

    return RuntimeSection(mode=mode, dashboard=dashboard)


def _parse_judge_section(raw: Any) -> JudgeSection:
    if raw is None:
        return JudgeSection()
    if not isinstance(raw, dict):
        raise ConfigError(f"'judge' must be a mapping, got {type(raw).__name__}")
    fb = raw.get("fallback_mode", "allow")
    if fb not in ("allow", "deny", "skip"):
        raise ConfigError(
            f"judge.fallback_mode must be one of allow|deny|skip, got {fb!r}"
        )
    return JudgeSection(
        provider=raw.get("provider"),
        model=raw.get("model"),
        api_key=raw.get("api_key") or None,
        base_url=raw.get("base_url") or None,
        fallback_mode=fb,
        circuit_breaker=bool(raw.get("circuit_breaker", True)),
        failure_threshold=int(raw.get("failure_threshold", 5)),
        cooldown_seconds=float(raw.get("cooldown_seconds", 10.0)),
    )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_constraint_entry(item: Any) -> ConstraintEntry:
    """Parse a single constraint entry (string or dict).

    Recognised dict shapes:

    * ``{pattern: ..., args: [...]}``        — structured pattern
    * ``{ltl: "G(...)"}``                    — raw LTL escape hatch
    * ``{nl: "..."}``                        — natural-language description
      (also accepted as a bare string item)

    Either ``pattern`` or ``ltl`` is required; specifying both is a config
    error since they take separate compile paths and silently picking one
    would mask user intent.
    """
    if isinstance(item, str):
        return ConstraintEntry(nl=item)
    elif isinstance(item, dict):
        has_pattern = "pattern" in item
        has_ltl = "ltl" in item
        has_nl = "nl" in item
        if has_pattern and has_ltl:
            raise ConfigError(
                "Constraint dict has both 'pattern' and 'ltl' keys — pick "
                "one.  ``pattern`` resolves against the registered pattern "
                "library; ``ltl`` parses a raw infix formula via "
                "sponsio.formulas.parser.parse_repr."
            )
        if has_pattern:
            args = item.get("args", [])
            if not isinstance(args, list):
                args = [args]
            return ConstraintEntry(
                pattern=item["pattern"],
                args=args,
                source=item.get("source"),
            )
        if has_ltl:
            ltl_text = item["ltl"]
            if not isinstance(ltl_text, str) or not ltl_text.strip():
                raise ConfigError(
                    f"Constraint 'ltl' must be a non-empty string, got: {ltl_text!r}"
                )
            return ConstraintEntry(
                ltl=ltl_text,
                source=item.get("source"),
            )
        if has_nl:
            return ConstraintEntry(nl=item["nl"], source=item.get("source"))
        raise ConfigError(
            "Constraint dict must have 'pattern', 'ltl', or 'nl' key, "
            f"got: {list(item.keys())}"
        )
    else:
        raise ConfigError(f"Constraint must be a string or dict, got: {type(item)}")


def _parse_constraint_field(
    value: Any,
) -> ConstraintEntry | list[ConstraintEntry] | None:
    """Parse the assumption or enforcement field of a contract entry.

    Scalars return a single ``ConstraintEntry``; lists return a list
    (= AND). ``None`` stays ``None``.
    """
    if value is None:
        return None
    if isinstance(value, list):
        return [_parse_constraint_entry(item) for item in value]
    return _parse_constraint_entry(value)


def _parse_contract_entry(item: Any, agent_id: str) -> ContractEntry:
    """Parse a single entry in the ``contracts:`` list.

    Accepts both short keys (``A`` / ``E``) and long keys
    (``assumption`` / ``enforcement``). Using both forms of the same
    field in a single entry (e.g. both ``A`` and ``assumption``) is
    ambiguous and raises ``ConfigError``.
    """
    if not isinstance(item, dict):
        raise ConfigError(
            f"Agent '{agent_id}': each 'contracts' entry must be a mapping "
            f"with 'A'/'assumption' (optional) and 'E'/'enforcement' "
            f"(required); got {type(item).__name__}"
        )

    has_short_a = "A" in item
    has_long_a = "assumption" in item
    has_short_e = "E" in item
    has_long_e = "enforcement" in item

    if has_short_a and has_long_a:
        raise ConfigError(
            f"Agent '{agent_id}': contract entry has both 'A' and "
            f"'assumption' — pick one. Got: {item!r}"
        )
    if has_short_e and has_long_e:
        raise ConfigError(
            f"Agent '{agent_id}': contract entry has both 'E' and "
            f"'enforcement' — pick one. Got: {item!r}"
        )

    e_raw = item.get("E") if has_short_e else item.get("enforcement")
    if e_raw is None:
        raise ConfigError(
            f"Agent '{agent_id}': contract entry missing 'E' / 'enforcement': {item!r}"
        )
    a_raw = item.get("A") if has_short_a else item.get("assumption")
    desc = item.get("desc")

    alpha, beta = _parse_thresholds(item, agent_id)

    return ContractEntry(
        enforcement=_parse_constraint_field(e_raw),  # type: ignore[arg-type]
        assumption=_parse_constraint_field(a_raw),
        desc=desc,
        alpha=alpha,
        beta=beta,
    )


def _parse_thresholds(item: dict, agent_id: str) -> tuple[float, float]:
    """Resolve ``(alpha, beta)`` from the three mutually-exclusive YAML specs.

    Forms accepted:

    * explicit ``alpha`` / ``beta`` (either may be set; unset defaults to 1.0)
    * ``risk_profile: <name>``
    * ``costs: {fp: N, fn: M}`` (α falls back to per-category default)
    """
    from sponsio.models.thresholds import resolve_thresholds

    alpha = item.get("alpha")
    beta = item.get("beta")
    risk_profile = item.get("risk_profile")
    costs = item.get("costs")

    try:
        return resolve_thresholds(
            alpha=alpha,
            beta=beta,
            risk_profile=risk_profile,
            costs=costs,
            atom_category=None,
        )
    except ValueError as e:
        raise ConfigError(f"Agent '{agent_id}': {e}") from e


def load_config(path: str | Path) -> SponsoConfig:
    """Load and validate a sponsio.yaml config file.

    Args:
        path: Path to the YAML file.

    Returns:
        Parsed SponsoConfig.

    Raises:
        ConfigError: If the file is invalid or malformed.
        FileNotFoundError: If the file doesn't exist.
    """
    try:
        import yaml
    except ImportError:
        raise ImportError(
            "PyYAML is required for config files. "
            "Install with: pip install 'sponsio[config]'"
        )

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    try:
        with open(path) as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML: {e}")

    if not isinstance(raw, dict):
        raise ConfigError("Config must be a YAML mapping (dict)")

    # ``${ENV_VAR}`` interpolation runs *after* YAML parse so users
    # can put secrets in env vars instead of committing them.  We
    # walk the whole tree once — keeps the rest of the loader naive.
    raw = _interpolate_env(raw)

    config = SponsoConfig(
        version=str(raw.get("version", "1")),
        defaults=raw.get("defaults", {}),
        extractor=_parse_extractor_section(raw.get("extractor")),
        judge=_parse_judge_section(raw.get("judge")),
        performance=_parse_performance_section(raw.get("performance")),
        runtime=_parse_runtime_section(raw.get("runtime")),
    )

    # Parse tools section
    tools_raw = raw.get("tools", [])
    if isinstance(tools_raw, list):
        for t in tools_raw:
            if isinstance(t, dict):
                config.tools.append(
                    ToolEntry(
                        name=t.get("name", ""),
                        description=t.get("description", ""),
                        params=t.get("params", ""),
                    )
                )
            elif isinstance(t, str):
                config.tools.append(ToolEntry(name=t))

    # Parse agents section
    agents_raw = raw.get("agents", {})
    if not isinstance(agents_raw, dict):
        raise ConfigError("'agents' must be a mapping of agent_id -> config")

    for agent_id, agent_data in agents_raw.items():
        if isinstance(agent_data, list):
            # Bare list — treat each entry as an unconditional contract
            ac = AgentConfig(agent_id=agent_id)
            for item in agent_data:
                ac.contracts.append(
                    ContractEntry(
                        enforcement=_parse_constraint_entry(item),
                    )
                )
            config.agents[agent_id] = ac
        elif isinstance(agent_data, dict):
            if "assumptions" in agent_data or "guarantees" in agent_data:
                raise ConfigError(
                    f"Agent '{agent_id}': the 'assumptions'/'guarantees' YAML "
                    f"schema is no longer supported. Use 'contracts:' with "
                    f"per-entry 'assumption'/'enforcement' (or 'A'/'E')."
                )
            contracts_raw = agent_data.get("contracts", [])
            if not isinstance(contracts_raw, list):
                raise ConfigError(f"Agent '{agent_id}': 'contracts' must be a list")
            ac = AgentConfig(agent_id=agent_id)
            for item in contracts_raw:
                ac.contracts.append(_parse_contract_entry(item, agent_id))
            config.agents[agent_id] = ac
        else:
            raise ConfigError(f"Agent '{agent_id}': value must be a mapping or list")

    return config


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


def _compile_structured(entry: ConstraintEntry) -> Any:
    """Compile a structured constraint entry to a formula object."""
    from sponsio.generation.nl_to_contract import get_available_patterns

    registry = get_available_patterns()
    if entry.pattern not in registry:
        raise ConfigError(
            f"Unknown pattern '{entry.pattern}'. Available: {sorted(registry.keys())}"
        )
    fn = registry[entry.pattern]
    coerced_args = []
    for a in entry.args:
        if isinstance(a, str) and a.isdigit():
            coerced_args.append(int(a))
        else:
            coerced_args.append(a)
    return fn(*coerced_args)


def _compile_ltl(entry: ConstraintEntry) -> Any:
    """Compile a raw-LTL constraint entry to a :class:`DetFormula`.

    Wraps the parsed formula in a ``DetFormula`` so the runtime treats
    it identically to any pattern-library output.  ``pattern_name`` is
    set to ``"ltl"`` so attribution / metrics can distinguish raw-LTL
    contracts from registered patterns; ``desc`` falls back to the LTL
    text itself when the YAML didn't supply one.

    Raised errors:
        ConfigError: When the LTL string fails to parse.  We re-raise
            with the original LTL text included so the user can locate
            the offending entry in their YAML; the parse error alone
            (e.g. "Expected ')' at position 12") is unactionable
            without the source line.
    """
    from sponsio.formulas.parser import ParseError, parse_repr
    from sponsio.patterns.library import DetFormula

    try:
        formula = parse_repr(entry.ltl)
    except ParseError as e:
        raise ConfigError(
            f"Failed to parse ltl formula {entry.ltl!r}: {e}"
        ) from e
    return DetFormula(
        formula=formula,
        desc=entry.ltl,
        pattern_name="ltl",
    )


def _compile_field(
    field_value: ConstraintEntry | list[ConstraintEntry] | None,
    llm_extractor: Any = None,
    tool_inventory: list[dict] | None = None,
) -> Any:
    """Compile an assumption/enforcement field to a constraint object.

    Scalars return a single object; lists return a list (so the monitor
    can AND them at check time).
    """
    if field_value is None:
        return None

    if isinstance(field_value, list):
        return [
            _compile_single(item, llm_extractor, tool_inventory) for item in field_value
        ]

    return _compile_single(field_value, llm_extractor, tool_inventory)


def _compile_single(
    entry: ConstraintEntry,
    llm_extractor: Any = None,
    tool_inventory: list[dict] | None = None,
) -> Any:
    from sponsio.generation.nl_to_contract import (
        ContractSyntaxError,
        parse_nl_unified,
    )

    if entry.is_structured:
        return _compile_structured(entry)

    if entry.is_ltl:
        return _compile_ltl(entry)

    try:
        result = parse_nl_unified(
            entry.nl,
            llm_extractor=llm_extractor,
            tool_inventory=tool_inventory,
        )
    except ContractSyntaxError:
        # Config-driven path — a malformed DSL entry in a yaml file is
        # an op-level problem. Surface as a compile failure (None)
        # rather than crashing the loader; validators decide whether
        # to treat None as fatal.
        return None
    if result.is_det:
        return result.hard
    if result.is_sto:
        return result.sto
    return None


def config_to_guard_kwargs(config: SponsoConfig, agent_id: str) -> dict[str, Any]:
    """Extract BaseGuard constructor kwargs for a specific agent.

    Returns a dict with a ``contracts`` kwarg shaped like the Python
    API: each entry is a dict with ``assumption``/``enforcement``
    populated with compiled constraint objects (or kept as NL strings
    for later parsing inside the guard).

    Args:
        config: Parsed SponsoConfig.
        agent_id: Which agent's contracts to extract.

    Returns:
        Dict with keys: agent_id, contracts, plus defaults.

    Raises:
        ConfigError: If the agent_id is not found in config.
    """
    if agent_id not in config.agents:
        raise ConfigError(
            f"Agent '{agent_id}' not found in config. "
            f"Available: {list(config.agents.keys())}"
        )

    ac = config.agents[agent_id]
    tool_inventory = (
        [
            {"name": t.name, "description": t.description, "params": t.params}
            for t in config.tools
        ]
        if config.tools
        else None
    )

    contract_dicts: list[dict] = []
    for ce in ac.contracts:
        entry: dict[str, Any] = {
            "enforcement": _compile_field(
                ce.enforcement, tool_inventory=tool_inventory
            ),
        }
        if ce.assumption is not None:
            entry["assumption"] = _compile_field(
                ce.assumption, tool_inventory=tool_inventory
            )
        if ce.desc:
            entry["desc"] = ce.desc
        # Pass alpha/beta through only if non-default (avoids noise for
        # pure-det contracts; Contract constructor defaults are 1.0/1.0).
        if ce.alpha != 1.0:
            entry["alpha"] = ce.alpha
        if ce.beta != 1.0:
            entry["beta"] = ce.beta
        contract_dicts.append(entry)

    kwargs: dict[str, Any] = {
        "agent_id": agent_id,
        "contracts": contract_dicts if contract_dicts else None,
    }

    if config.defaults.get("verbose") is not None:
        kwargs["verbose"] = config.defaults["verbose"]
    if config.defaults.get("verbosity") is not None:
        kwargs["verbosity"] = config.defaults["verbosity"]

    return kwargs


def build_extractor(section: ExtractorSection) -> Any:
    """Construct a :class:`UnifiedExtractor` from an ``extractor:`` section.

    Returns ``None`` if no provider is configured — callers fall
    back to rule-based parsing in that case.  Imported lazily so
    ``import sponsio.config`` doesn't pull in optional LLM SDKs.
    """
    if section.provider is None and section.model is None and section.api_key is None:
        return None
    from sponsio.generation.llm_extraction import UnifiedExtractor

    return UnifiedExtractor(
        provider=section.provider,
        model=section.model,
        api_key=section.api_key,
        base_url=section.base_url,
    )


def build_sto_evaluator(section: JudgeSection) -> Any:
    """Construct a :class:`StoEvaluator` from a ``judge:`` section.

    Wires the fault-tolerance knobs (fallback / breaker) from YAML
    straight through; the LLM ``provider``/``model``/``api_key``
    fields are *advisory* — individual sto atoms read them through
    their own client construction (we don't centralise judge-client
    instantiation here because different atoms may want different
    models, e.g. a fast model for ``tone`` and a thinking model for
    ``injection_free``).
    """
    from sponsio.runtime.evaluators import StoEvaluator

    return StoEvaluator(
        fallback_mode=section.fallback_mode,  # type: ignore[arg-type]
        circuit_breaker=section.circuit_breaker,
        failure_threshold=section.failure_threshold,
        cooldown_seconds=section.cooldown_seconds,
    )


def config_to_system(
    config: SponsoConfig,
    llm_extractor: Any = None,
    tool_inventory: list[dict] | None = None,
) -> Any:
    """Build a System from all agents in the config.

    Each contract entry in the YAML becomes one :class:`Contract`. The
    monitor evaluates them independently.

    Args:
        config: Parsed ``SponsoConfig`` from ``load_config()``.
        llm_extractor: Optional ``UnifiedExtractor`` instance.
        tool_inventory: Optional list of tool dicts for LLM context.
            If None and config has a ``tools`` section, uses that.

    Returns:
        A System with one Contract per clause.
    """
    from sponsio.models.agent import Agent
    from sponsio.models.contract import Contract
    from sponsio.models.system import System

    if tool_inventory is None and config.tools:
        tool_inventory = [
            {"name": t.name, "description": t.description, "params": t.params}
            for t in config.tools
        ]

    contracts: list[Contract] = []
    for agent_id, ac in config.agents.items():
        agent_obj = Agent(id=agent_id)
        for ce in ac.contracts:
            e = _compile_field(ce.enforcement, llm_extractor, tool_inventory)
            a = _compile_field(ce.assumption, llm_extractor, tool_inventory)
            if e is None:
                continue
            contracts.append(
                Contract(
                    agent=agent_obj,
                    enforcement=e,
                    assumption=a,
                    desc=ce.desc,
                    alpha=ce.alpha,
                    beta=ce.beta,
                )
            )

    system = System(name="config")
    system._contracts = contracts
    return system

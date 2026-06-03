"""YAML contract configuration loader.

Loads a ``sponsio.yaml`` file and returns structured data for BaseGuard.

The canonical shape is a list of **contracts** under each agent. each
contract is an ``(assumption, enforcement)`` pair and is evaluated
independently. Assumptions never cross contracts.

Each contract entry accepts either the **short keys** ``A`` /
``E`` (terse, preferred for hand-edited YAML) or the **full keys**
``assumption`` / ``enforcement`` (self-describing, matches the Python
API). Mixing is fine *across* entries, but using both a short and
long form for the same field in the *same* entry raises
``ConfigError``. pick one.

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
          # short keys. recommended for terse hand-edited YAML
          - A: "called `cancel_order`"
            G: "must call `get_order_details` before `cancel_order`"
          - G: "tool `sed` arg contains `-i` is banned"
          # long keys. accepted when users prefer them (e.g. copied
          # from Python code)
          - assumption: ["called `modify_order`", "verified_identity"]
            guarantee:
              - "U(Not(called(modify_order)), called(get_order_details))"
              - "tool `modify_order` at most 3 times"

Within a contract entry, each NL string / scalar can also be a structured
pattern dict::

    contracts:
      - A: {pattern: called, args: [cancel_order]}
        G: {pattern: must_precede, args: [get_order_details, cancel_order]}

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
    """A single constraint. one of three shapes:

    1. **NL** (``nl="every send_email needs confirmation"``). passed to
       the structured-IR / LLM extractor at compile time.
    2. **Pattern** (``pattern="rate_limit"``, ``args=[exec, 50]``).
       resolved against the registered pattern library.  ``pattern`` may
       name either a deterministic pattern (functions in
       :mod:`sponsio.patterns.library`) or a stochastic atom registered
       via :func:`sponsio.patterns.sto_registry.register_sto_atom`.
       compilation auto-routes via the appropriate registry.
    3. **LTL** (``ltl="G(called(exec) -> count(confirm) >= count(exec))"``)
      . raw infix LTL parsed by :func:`sponsio.formulas.parser.parse_repr`.
       This is the escape hatch for properties that mix predicate-on-arg
       conditionals with count dominance, which the structured patterns
       can't express directly (e.g. "sudo exec needs confirmation but
       plain exec doesn't").

    Sto-only knobs (``context_scope`` / ``output_type`` / ``prompt_override``
    / ``threshold``) are forwarded verbatim into the
    :class:`~sponsio.formulas.formula.Atom` and :class:`StoFormula` when
    ``pattern`` resolves to a stochastic atom; det patterns ignore them.
    """

    nl: str | None = None
    pattern: str | None = None
    args: list[Any] = field(default_factory=list)
    ltl: str | None = None
    source: str | None = None
    context_scope: str | None = None
    output_type: str | None = None
    prompt_override: str | None = None
    threshold: float | None = None
    desc: str | None = None
    """Per-clause human-readable label.

    Without this, the loader is stuck using ``entry.ltl`` (the raw
    formula text) as the DetFormula's desc. the inline trace then
    prints ``⚙ assume "F(arg_field_has(...))"`` instead of a sentence.
    Optional: structured patterns already get a decent desc from
    their factory, but this lets a YAML override it for consistency
    across A/G clauses inside one contract.
    """

    @property
    def is_structured(self) -> bool:
        return self.pattern is not None

    @property
    def is_ltl(self) -> bool:
        return self.ltl is not None


@dataclass
class ContractEntry:
    """One Assume-Guarantee (A/G) pair from the YAML.

    Each field may hold None, one ``ConstraintEntry``, or a list of
    them (= logical AND). ``assumption`` is optional; ``guarantee`` is
    required.

    ``alpha`` and ``beta`` are resolved at parse time from one of three
    mutually-exclusive YAML specs: explicit ``alpha``/``beta``,
    ``risk_profile``, or ``costs``. Defaults (1.0, 1.0) preserve existing
    det semantics.
    """

    guarantee: ConstraintEntry | list[ConstraintEntry] = None  # type: ignore[assignment]
    assumption: ConstraintEntry | list[ConstraintEntry] | None = None
    desc: str | None = None
    alpha: float = 1.0
    beta: float = 1.0
    activate_at: str | None = None
    """Trigger-then-enforce semantic switch.  See ``Contract.activate_at``
    docstring.  Default ``None`` = global semantics; ``"first_match"`` =
    reactive semantics (E checked from the first position where A
    activates, not from position 0)."""
    pack_source: str | None = None
    """Origin of this entry. ``None`` for hand-written contracts,
    or the include spec (e.g. ``"sponsio:core/universal"``) for
    contracts pulled in via ``include:``.  Used by ``customized:`` to
    target entries by their pack and by ``sponsio validate`` to surface
    where a contract came from."""


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
    """Runtime sto-judge config (consumed by sto evaluators).

    Runtime judging happens on every guarded turn: latency, cost,
    and resilience matter.  Most users want a *cheaper, faster* model
    here (e.g. ``gpt-4o-mini``, ``gemini-2.5-flash``) and care about
    the fault-tolerance knobs.

    The sto pipeline is an extension point; this build rejects sto
    contracts at config load and never reaches a judge. Defaults match
    the reference sto evaluator so an empty section behaves like the
    programmatic default once an implementation is plugged in.
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
    different story: we don't need a judge cache. most checks are
    DFA and never touch an LLM in the first place.  This section
    controls *how that story gets surfaced*, not whether the speedup
    happens.

    Fields:
      * ``report``: when to print the human-readable performance
        table.  ``auto`` (default) prints at process exit only when
        the guard is ``verbose=True`` and stderr is a TTY. same
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
    (note the asymmetry. ``mode`` lets env override an explicit ctor
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
class ToolPolicySection:
    """Top-level tool-access policy.

    Sponsio's existing ``tool_allowlist`` pattern checks at call time:
    the agent picks a tool, ``guard_before`` blocks it. This section
    lifts the *posture* to a one-place declaration ("default deny") and
    pairs it with the static approved set, so adding a new tool to the
    underlying framework cannot accidentally hand it to the agent.

    Fields:
      * ``default``: ``"deny"`` (no tool may be called unless listed in
        ``approved``) or ``"allow"`` (anything goes; ``approved`` is
        ignored). Defaults to ``"allow"`` so existing yaml files keep
        working byte-for-byte; users opt into deny.
      * ``approved``: explicit list of tool names that are permitted
        under ``default: deny``. Empty + deny blocks every tool, which
        is a useful "lock down completely" posture but loud, so the
        loader logs it.
      * ``enforcement``: ``"reactive"`` (existing behaviour: the agent
        sees every approved tool, violations get blocked at the call
        boundary) or ``"proactive"`` (the framework's tool listing is
        filtered before the model turn; the agent never sees a denied
        tool). ``proactive`` is wired by integration adapters in a
        follow-up. Parsed now so the schema stays stable across the
        rollout.
    """

    default: str = "allow"
    approved: list[str] = field(default_factory=list)
    enforcement: str = "reactive"


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
    tool_policy: ToolPolicySection = field(default_factory=ToolPolicySection)


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

    Walks through dicts and lists in place. anything non-string /
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
    degrade gracefully. a URL, a bool, or nothing.
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


_VALID_TOOL_POLICY_DEFAULTS = frozenset({"allow", "deny"})
_VALID_TOOL_POLICY_ENFORCEMENT = frozenset({"reactive", "proactive"})


def _parse_tool_policy_section(raw: Any) -> ToolPolicySection:
    """Parse the optional ``tool_policy:`` block.

    Fails fast on typos so an operator who meant ``default: deny`` but
    typed ``deni`` does not silently fall back to allow and ship an
    insecure runtime. ``approved`` accepts both a YAML list and the
    nested form ``approved: {tools: [...]}`` so future per-host /
    per-agent scoping can layer on without breaking the flat form.
    """
    if raw is None:
        return ToolPolicySection()
    if not isinstance(raw, dict):
        raise ConfigError(f"'tool_policy' must be a mapping, got {type(raw).__name__}")

    default = raw.get("default", "allow")
    if not isinstance(default, str) or default not in _VALID_TOOL_POLICY_DEFAULTS:
        raise ConfigError(
            f"tool_policy.default must be one of "
            f"{sorted(_VALID_TOOL_POLICY_DEFAULTS)}, got {default!r}"
        )

    approved_raw = raw.get("approved", [])
    if isinstance(approved_raw, dict):
        approved_raw = approved_raw.get("tools", [])
    if not isinstance(approved_raw, list):
        raise ConfigError(
            f"tool_policy.approved must be a list of tool names, "
            f"got {type(approved_raw).__name__}"
        )
    approved: list[str] = []
    for i, name in enumerate(approved_raw):
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(
                f"tool_policy.approved[{i}] must be a non-empty string, got {name!r}"
            )
        approved.append(name.strip())

    enforcement = raw.get("enforcement", "reactive")
    if (
        not isinstance(enforcement, str)
        or enforcement not in _VALID_TOOL_POLICY_ENFORCEMENT
    ):
        raise ConfigError(
            f"tool_policy.enforcement must be one of "
            f"{sorted(_VALID_TOOL_POLICY_ENFORCEMENT)}, got {enforcement!r}"
        )

    return ToolPolicySection(
        default=default, approved=approved, enforcement=enforcement
    )


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

    * ``{pattern: ..., args: [...]}``       . structured pattern
    * ``{ltl: "G(...)"}``                   . raw LTL escape hatch
    * ``{nl: "..."}``                       . natural-language description
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
                "Constraint dict has both 'pattern' and 'ltl' keys. pick "
                "one.  ``pattern`` resolves against the registered pattern "
                "library; ``ltl`` parses a raw infix formula via "
                "sponsio.formulas.parser.parse_repr."
            )
        if has_pattern:
            args = item.get("args", [])
            if not isinstance(args, list):
                args = [args]
            threshold = item.get("threshold")
            if threshold is not None:
                try:
                    threshold = float(threshold)
                except (TypeError, ValueError) as e:
                    raise ConfigError(
                        f"Constraint 'threshold' must be a number in [0,1], "
                        f"got: {threshold!r}"
                    ) from e
                if not 0.0 <= threshold <= 1.0:
                    raise ConfigError(
                        f"Constraint 'threshold' must be in [0,1], got: {threshold}"
                    )
            return ConstraintEntry(
                pattern=item["pattern"],
                args=args,
                source=item.get("source"),
                context_scope=item.get("context_scope"),
                output_type=item.get("output_type"),
                prompt_override=item.get("prompt_override"),
                threshold=threshold,
                desc=item.get("desc"),
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
                desc=item.get("desc"),
            )
        if has_nl:
            return ConstraintEntry(
                nl=item["nl"],
                source=item.get("source"),
                desc=item.get("desc"),
            )
        raise ConfigError(
            "Constraint dict must have 'pattern', 'ltl', or 'nl' key, "
            f"got: {list(item.keys())}"
        )
    else:
        raise ConfigError(f"Constraint must be a string or dict, got: {type(item)}")


def _parse_constraint_field(
    value: Any,
) -> ConstraintEntry | list[ConstraintEntry] | None:
    """Parse the assumption or guarantee field of a contract entry.

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

    Accepts both short keys (``A`` / ``G``) and long keys
    (``assumption`` / ``guarantee``). Using both forms of the same
    field in a single entry (e.g. both ``A`` and ``assumption``) is
    ambiguous and raises ``ConfigError``.
    """
    if not isinstance(item, dict):
        raise ConfigError(
            f"Agent '{agent_id}': each 'contracts' entry must be a mapping "
            f"with 'A'/'assumption' (optional) and 'G'/'guarantee' "
            f"(required); got {type(item).__name__}"
        )

    has_short_a = "A" in item
    has_long_a = "assumption" in item
    has_short_g = "G" in item
    has_long_g = "guarantee" in item

    if has_short_a and has_long_a:
        raise ConfigError(
            f"Agent '{agent_id}': contract entry has both 'A' and "
            f"'assumption'. pick one. Got: {item!r}"
        )
    if has_short_g and has_long_g:
        raise ConfigError(
            f"Agent '{agent_id}': contract entry has both 'G' and "
            f"'guarantee'. pick one. Got: {item!r}"
        )

    e_raw = item.get("G") if has_short_g else item.get("guarantee")
    if e_raw is None:
        raise ConfigError(
            f"Agent '{agent_id}': contract entry missing 'G' / 'guarantee': {item!r}"
        )
    a_raw = item.get("A") if has_short_a else item.get("assumption")
    desc = item.get("desc")

    alpha, beta = _parse_thresholds(item, agent_id)

    activate_at = item.get("activate_at")
    if activate_at is not None and activate_at not in ("first_match",):
        raise ConfigError(
            f"Agent '{agent_id}': contract entry has unknown activate_at "
            f"value {activate_at!r}; supported values are: 'first_match'."
        )

    return ContractEntry(
        guarantee=_parse_constraint_field(e_raw),  # type: ignore[arg-type]
        assumption=_parse_constraint_field(a_raw),
        desc=desc,
        alpha=alpha,
        beta=beta,
        activate_at=activate_at,
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


# ---------------------------------------------------------------------------
# include: resolution. pull contracts from packs into the host config
# ---------------------------------------------------------------------------


def _resolve_include_spec(spec: str, base_dir: Path) -> Path:
    """Map an ``include:`` entry to an absolute YAML path.

    Recognised forms:

    * ``sponsio:<category>/<name>``. bundled pack shipped with the
      package, resolved against ``sponsio/contracts/``.  The trailing
      ``.yaml`` is optional.  Examples:
        - ``sponsio:core/universal``
        - ``sponsio:capability/shell``
        - ``sponsio:incident/openclaw``
    * Bare path. relative paths resolve against ``base_dir`` (the
      directory holding the *including* yaml).  Absolute paths are
      used as-is.  Useful when teams keep their own pack repo and
      want to share rules across projects without publishing to PyPI.

    Raises:
        ConfigError: When the spec is malformed, the bundled pack
            doesn't exist, or the path doesn't resolve.
    """
    import sponsio

    if not isinstance(spec, str) or not spec.strip():
        raise ConfigError(f"include: entry must be a non-empty string, got {spec!r}")

    if spec.startswith("sponsio:"):
        rel = spec[len("sponsio:") :].strip()
        if not rel:
            raise ConfigError(
                f"include: bundled spec is empty: {spec!r} "
                "- expected e.g. 'sponsio:core/universal'"
            )
        if not rel.endswith(".yaml"):
            rel = rel + ".yaml"
        pkg_root = Path(sponsio.__file__).parent
        candidate = (pkg_root / "contracts" / rel).resolve()
        # Defence in depth. confine resolution to the bundled tree so
        # a stray ``sponsio:../../etc/passwd`` can't escape.
        contracts_root = (pkg_root / "contracts").resolve()
        try:
            candidate.relative_to(contracts_root)
        except ValueError as e:
            raise ConfigError(
                f"include: spec {spec!r} resolves outside the bundled contracts tree"
            ) from e
        if not candidate.exists():
            available = sorted(
                str(p.relative_to(contracts_root).with_suffix(""))
                for p in contracts_root.rglob("*.yaml")
            )
            raise ConfigError(
                f"include: bundled pack not found: {spec!r}. "
                f"Available: {[f'sponsio:{p}' for p in available]}"
            )
        return candidate

    # Bare filesystem include. Two cases:
    #   - relative path: resolved under ``base_dir`` and **must stay
    #     under it** so a malicious upstream pack can't pull in
    #     ``../../etc/passwd`` (path traversal. sym to the
    #     ``sponsio:`` confinement above).
    #   - absolute path: allowed unconditionally because the operator
    #     who wrote the host yaml already chose it explicitly. (The
    #     usual config loader only reads YAML the operator pointed at,
    #     so an absolute path here means the operator typed it.)
    from sponsio._paths import PathEscapeError, safe_resolve

    raw = Path(spec).expanduser()
    try:
        if raw.is_absolute():
            p = raw.resolve()
        else:
            p = safe_resolve(spec, base_dir=base_dir, safe_root=base_dir)
    except PathEscapeError as e:
        raise ConfigError(
            f"include: spec {spec!r} resolves outside the including "
            f"file's directory ({base_dir}). Use an absolute path or "
            "the bundled ``sponsio:...`` form for cross-tree includes."
        ) from e
    if not p.exists():
        raise ConfigError(f"include: file not found: {p}")
    return p


def _load_pack_contracts(
    spec: str, base_dir: Path, agent_id: str, _seen: set[str]
) -> list[ContractEntry]:
    """Resolve one ``include:`` entry to a list of ``ContractEntry``.

    Each shipped pack defines its rules under the placeholder agent id
    ``"*"`` (see ``sponsio/contracts/core/universal.yaml`` for the
    canonical shape).  We pull that ``"*"`` block out and stamp every
    contract it contains with ``pack_source = spec`` so downstream
    tooling (``customized:``, ``sponsio validate`` source attribution)
    can address them.

    Args:
        spec: The include spec, used both for resolution and as the
            stamped ``pack_source`` value.
        base_dir: Directory of the *including* yaml. relative paths
            in ``spec`` resolve against this.
        agent_id: The host agent id we're injecting into.  Used only
            for error messages today; kept in the signature so future
            ``tool_rename`` work can scope renames per-agent without a
            second round-trip.
        _seen: Cycle-detection set holding every spec currently being
            resolved on the call stack.  Mutated in place during
            recursion; never grows beyond the depth of the include
            chain.

    Raises:
        ConfigError: If the pack isn't found, has no ``"*"`` agent,
            defines multiple agents, or participates in an include
            cycle.
    """
    import yaml

    if spec in _seen:
        chain = " -> ".join(list(_seen) + [spec])
        raise ConfigError(f"include: cycle detected: {chain}")

    pack_path = _resolve_include_spec(spec, base_dir)

    try:
        with open(pack_path) as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"include {spec!r}: invalid YAML in {pack_path}: {e}")

    if not isinstance(raw, dict):
        raise ConfigError(
            f"include {spec!r}: pack root must be a mapping, got {type(raw).__name__}"
        )
    raw = _interpolate_env(raw)

    pack_agents = raw.get("agents")
    if not isinstance(pack_agents, dict) or not pack_agents:
        raise ConfigError(
            f"include {spec!r}: pack must define an 'agents:' mapping with "
            f"a single '*' agent (the template)"
        )
    if list(pack_agents.keys()) != ["*"]:
        raise ConfigError(
            f"include {spec!r}: pack must define exactly one agent named '*' "
            f"(the template), got {list(pack_agents.keys())}.  Multi-agent "
            f"packs aren't supported. split the file."
        )

    template = pack_agents["*"]
    if not isinstance(template, dict):
        raise ConfigError(
            f"include {spec!r}: '*' agent value must be a mapping, "
            f"got {type(template).__name__}"
        )

    contracts_raw = template.get("contracts", [])
    if not isinstance(contracts_raw, list):
        raise ConfigError(
            f"include {spec!r}: '*' agent's 'contracts' must be a list, "
            f"got {type(contracts_raw).__name__}"
        )

    nested_includes = template.get("include", [])
    pulled: list[ContractEntry] = []
    if nested_includes:
        if not isinstance(nested_includes, list):
            raise ConfigError(
                f"include {spec!r}: nested 'include' must be a list of strings"
            )
        _seen.add(spec)
        try:
            for nested in nested_includes:
                pulled.extend(
                    _load_pack_contracts(nested, pack_path.parent, agent_id, _seen)
                )
        finally:
            _seen.discard(spec)

    for item in contracts_raw:
        ce = _parse_contract_entry(item, agent_id)
        ce.pack_source = spec
        pulled.append(ce)

    return pulled


# ---------------------------------------------------------------------------
# tool_rename: + workspace:. rewrite pulled-in pack contents into
# the host's vocabulary
# ---------------------------------------------------------------------------


_WORKSPACE_PLACEHOLDER = "<workspace>/"
_AGENT_PLACEHOLDER = "<agent>"


def _rewrite_string(
    text: str,
    workspace: str | None,
    tool_rename: dict[str, str],
    agent_id: str | None = None,
) -> str:
    """Apply workspace + agent + tool-rename rewrites to a single string.

    * ``<workspace>/``. literal substring replacement.  Skipped when
      ``workspace`` is None; the caller decides whether unresolved
      placeholders should error.
    * ``<agent>``. replaced with the host ``agent_id`` so packs can
      reference the running agent in LTL atoms like
      ``flow(<agent>, external)`` portably.  Skipped when ``agent_id``
      is None.  Word-boundary substitution to avoid clobbering
      ``<agentless>`` and similar.
    * Tool renames. whole-word identifier substitution (``\\b{name}\\b``)
      so a rename of ``exec → bash`` doesn't accidentally hit
      ``executor`` or ``rexec``.
    """
    out = text
    if workspace is not None and _WORKSPACE_PLACEHOLDER in out:
        ws = workspace.rstrip("/") + "/"
        out = out.replace(_WORKSPACE_PLACEHOLDER, ws)
    if agent_id is not None and _AGENT_PLACEHOLDER in out:
        out = out.replace(_AGENT_PLACEHOLDER, agent_id)
    if tool_rename:
        for old, new in tool_rename.items():
            out = re.sub(rf"\b{re.escape(old)}\b", new, out)
    return out


def _rewrite_arg(
    arg: Any,
    workspace: str | None,
    tool_rename: dict[str, str],
    agent_id: str | None = None,
) -> Any:
    """Rewrite one ``args`` element.

    Strings get string-level rewrites.  Lists recurse so
    ``args: [scope_limit, [<workspace>/, /tmp/]]`` works.  Tool-rename
    on a *whole* string arg additionally honours exact-match aliasing.
    ``args: [exec, 50]`` with ``tool_rename = {exec: bash}`` becomes
    ``[bash, 50]`` even when ``exec`` is the entire arg (no word
    boundary on a single token).  Non-string scalars pass through.
    """
    if isinstance(arg, str):
        rewritten = _rewrite_string(arg, workspace, tool_rename, agent_id)
        if tool_rename and rewritten in tool_rename:
            rewritten = tool_rename[rewritten]
        return rewritten
    if isinstance(arg, list):
        return [_rewrite_arg(a, workspace, tool_rename, agent_id) for a in arg]
    return arg


def _rewrite_constraint_entry(
    ce: ConstraintEntry,
    workspace: str | None,
    tool_rename: dict[str, str],
    agent_id: str,
    enforce_placeholder_check: bool,
) -> None:
    """Mutate ``ce`` in place: apply rewrites to ``args`` and ``ltl``.

    ``nl`` / ``pattern`` / ``desc`` / ``source`` are intentionally
    left alone. NL is fluid prose (rewrites would corrupt grammar),
    pattern names are stable identifiers, and ``source`` is metadata
    only.

    Args:
        enforce_placeholder_check: When True, raise if any
            ``<workspace>/`` placeholder remains after substitution.
            Only the include-resolution path turns this on. direct
            loads of a pack file (``load_config("sponsio/contracts/
            capability/filesystem.yaml")``) need to succeed for
            ``sponsio validate`` and CI to inspect packs without a
            host config.

    Raises:
        ConfigError: When ``enforce_placeholder_check`` is True and a
            placeholder leaked through.  Naming the offending entry
            beats discovering it at first-event evaluation time.
    """
    if ce.args:
        ce.args = [_rewrite_arg(a, workspace, tool_rename, agent_id) for a in ce.args]
    if ce.ltl:
        ce.ltl = _rewrite_string(ce.ltl, workspace, tool_rename, agent_id)

    if enforce_placeholder_check and workspace is None:
        leftover = []
        for a in ce.args or []:
            if isinstance(a, str) and _WORKSPACE_PLACEHOLDER in a:
                leftover.append(a)
            elif isinstance(a, list):
                leftover.extend(
                    s for s in a if isinstance(s, str) and _WORKSPACE_PLACEHOLDER in s
                )
        if ce.ltl and _WORKSPACE_PLACEHOLDER in ce.ltl:
            leftover.append(ce.ltl)
        if leftover:
            raise ConfigError(
                f"Agent '{agent_id}': pattern '{ce.pattern or 'ltl'}' uses "
                f"the '<workspace>/' placeholder but the agent has no "
                f"'workspace:' set. Add `workspace: \"/path/to/your/repo\"` "
                f"to this agent.  Offending values: {leftover!r}"
            )


def _rewrite_contract_entry(
    contract: ContractEntry,
    workspace: str | None,
    tool_rename: dict[str, str],
    agent_id: str,
) -> None:
    """Walk both ``enforcement`` and ``assumption`` (each may be a
    single ConstraintEntry or a list) and apply rewrites in place.

    The ``<workspace>/`` leftover check fires only on contracts that
    came in via ``include:`` (``contract.pack_source is not None``).
    Locally-authored or direct-loaded pack contracts are considered
    the user's responsibility. they may be inspecting a pack file
    via ``sponsio validate`` and need it to load even without a host
    workspace set.
    """
    enforce_check = contract.pack_source is not None

    def walk(field):
        if field is None:
            return
        if isinstance(field, list):
            for ce in field:
                _rewrite_constraint_entry(
                    ce, workspace, tool_rename, agent_id, enforce_check
                )
        else:
            _rewrite_constraint_entry(
                field, workspace, tool_rename, agent_id, enforce_check
            )

    walk(contract.guarantee)
    walk(contract.assumption)


# ---------------------------------------------------------------------------
# overrides:. disable / tune individual contracts after include
# ---------------------------------------------------------------------------


@dataclass
class OverrideRule:
    """One ``customized:`` entry. a match clause + an effect.

    Match fields are AND'd; a contract qualifies only when every key
    in ``match`` equals the corresponding contract field.  An empty
    ``match`` is rejected at parse time (it would silently apply to
    everything, which is almost certainly a mistake).

    Supported match keys:
      * ``desc``. the contract's human description.  This is the
        most common path because pack YAMLs put the rule's intent
        right in ``desc:``.
      * ``pack_source``. origin pack spec (e.g.
        ``"sponsio:capability/shell"``).  Useful for "disable
        everything from this pack" without listing rules one by one.
      * ``source``. the ``ConstraintEntry.source`` library tag (e.g.
        ``"library:tier1.shell"``).  Finer-grained than pack_source
        when one pack ships rules from several library tiers.
      * ``pattern``. the structured pattern name on the enforcement
        constraint (``rate_limit``, ``injection_free``, …).

    Effects:
      * ``disabled: true``. drop the matched contract entirely.
      * ``threshold`` / ``prompt_override`` / ``context_scope``.
        forwarded onto the enforcement ConstraintEntry(s).  Only
        meaningful for stochastic patterns; det patterns ignore them
        at compile time so a no-op override is harmless but suspect
       . we don't currently warn.

    The match/effect split is borrowed from ``kustomize`` / GitOps
    overlays: it keeps the override readable (intent at top, effect
    at bottom) and makes "match nothing" detectable as an error.

    ``matched_count`` is bookkeeping for the unmatched-rule diagnostic
   . see ``_apply_overrides``.
    """

    match: dict[str, str]
    disabled: bool = False
    threshold: float | None = None
    prompt_override: str | None = None
    context_scope: str | None = None
    matched_count: int = 0


_OVERRIDE_MATCH_KEYS = {"desc", "pack_source", "source", "pattern"}
_OVERRIDE_EFFECT_KEYS = {"disabled", "threshold", "prompt_override", "context_scope"}


def _parse_override_rule(raw: Any, agent_id: str, idx: int) -> OverrideRule:
    """Validate a single ``customized:`` entry.

    Errors name the entry index so users can locate the offender in a
    long list quickly.  We also reject unknown keys outright. silently
    ignoring an `enabled: true` typo would defeat the purpose of having
    overrides.
    """
    if not isinstance(raw, dict):
        raise ConfigError(
            f"Agent '{agent_id}': overrides[{idx}] must be a mapping, "
            f"got {type(raw).__name__}"
        )

    match_raw = raw.get("match")
    if not isinstance(match_raw, dict) or not match_raw:
        raise ConfigError(
            f"Agent '{agent_id}': overrides[{idx}] requires a non-empty "
            f"'match:' mapping (e.g. `match: {{desc: \"…\"}}`).  An empty "
            f"match would apply to every contract."
        )
    bad_keys = set(match_raw) - _OVERRIDE_MATCH_KEYS
    if bad_keys:
        raise ConfigError(
            f"Agent '{agent_id}': overrides[{idx}] has unknown match keys "
            f"{sorted(bad_keys)}; supported keys are "
            f"{sorted(_OVERRIDE_MATCH_KEYS)}"
        )
    match: dict[str, str] = {}
    for k, v in match_raw.items():
        if not isinstance(v, str) or not v.strip():
            raise ConfigError(
                f"Agent '{agent_id}': overrides[{idx}].match.{k} must be a "
                f"non-empty string, got {v!r}"
            )
        match[k] = v

    rule = OverrideRule(match=match)

    effect_keys = set(raw) - {"match"}
    bad_effects = effect_keys - _OVERRIDE_EFFECT_KEYS
    if bad_effects:
        raise ConfigError(
            f"Agent '{agent_id}': overrides[{idx}] has unknown effect keys "
            f"{sorted(bad_effects)}; supported keys are "
            f"{sorted(_OVERRIDE_EFFECT_KEYS)}"
        )
    if not effect_keys:
        raise ConfigError(
            f"Agent '{agent_id}': overrides[{idx}] has no effect. add at "
            f"least one of {sorted(_OVERRIDE_EFFECT_KEYS)} (e.g. "
            f"`disabled: true`)"
        )

    if "disabled" in raw:
        if not isinstance(raw["disabled"], bool):
            raise ConfigError(
                f"Agent '{agent_id}': overrides[{idx}].disabled must be a "
                f"boolean, got {raw['disabled']!r}"
            )
        rule.disabled = raw["disabled"]
    if "threshold" in raw:
        t = raw["threshold"]
        if not isinstance(t, (int, float)) or not (0.0 <= float(t) <= 1.0):
            raise ConfigError(
                f"Agent '{agent_id}': overrides[{idx}].threshold must be a "
                f"number in [0.0, 1.0], got {t!r}"
            )
        rule.threshold = float(t)
    if "prompt_override" in raw:
        p = raw["prompt_override"]
        if not isinstance(p, str) or not p.strip():
            raise ConfigError(
                f"Agent '{agent_id}': overrides[{idx}].prompt_override must be "
                f"a non-empty string, got {p!r}"
            )
        rule.prompt_override = p
    if "context_scope" in raw:
        c = raw["context_scope"]
        if not isinstance(c, str) or not c.strip():
            raise ConfigError(
                f"Agent '{agent_id}': overrides[{idx}].context_scope must be "
                f"a non-empty string, got {c!r}"
            )
        rule.context_scope = c

    # Combining ``disabled: true`` with field-edits is suspicious. the
    # contract is gone, so the edits are dead code.  Catch the typo.
    if rule.disabled and (
        rule.threshold is not None
        or rule.prompt_override is not None
        or rule.context_scope is not None
    ):
        raise ConfigError(
            f"Agent '{agent_id}': overrides[{idx}] sets `disabled: true` "
            f"alongside field-edits; the edits would never apply.  Split "
            f"into two override entries or drop one of the effects."
        )
    return rule


def _contract_constraints(contract: ContractEntry) -> list[ConstraintEntry]:
    """Flatten the enforcement field. used by both override matching
    (against ``source`` / ``pattern``) and override application
    (writing back ``threshold`` etc.).  The list shape (``E`` may be
    a list) is opaque to overrides. they apply to every constraint
    in the AND group.  In practice pack rules are single-constraint
    so this distinction rarely matters."""
    e = contract.guarantee
    if e is None:
        return []
    if isinstance(e, list):
        return list(e)
    return [e]


def _matches_override(rule: OverrideRule, contract: ContractEntry) -> bool:
    """All match-keys must agree.  ``desc`` and ``pack_source`` are
    contract-level; ``source`` and ``pattern`` are constraint-level
    and match if *any* enforcement constraint carries the requested
    value (covering pack rules whose ``E:`` is a list of two
    constraints with different patterns/sources)."""
    for key, expected in rule.match.items():
        if key == "desc":
            if contract.desc != expected:
                return False
        elif key == "pack_source":
            if contract.pack_source != expected:
                return False
        elif key == "source":
            if not any(c.source == expected for c in _contract_constraints(contract)):
                return False
        elif key == "pattern":
            if not any(c.pattern == expected for c in _contract_constraints(contract)):
                return False
    return True


def _apply_override_effects(rule: OverrideRule, contract: ContractEntry) -> None:
    """Apply non-``disabled`` effects.  Caller has already filtered
    out the disabled case (those contracts get dropped entirely).

    Field edits write to *every* enforcement constraint. typically
    one, occasionally a list-AND.  Det constraints carry the fields
    too (the dataclass shape is uniform); they just ignore them at
    compile time.  This mirrors ``ConstraintEntry`` semantics where
    sto-only fields are tolerated on det entries."""
    for ce in _contract_constraints(contract):
        if rule.threshold is not None:
            ce.threshold = rule.threshold
        if rule.prompt_override is not None:
            ce.prompt_override = rule.prompt_override
        if rule.context_scope is not None:
            ce.context_scope = rule.context_scope


def _apply_overrides(
    contracts: list[ContractEntry],
    overrides: list[OverrideRule],
    agent_id: str,
) -> list[ContractEntry]:
    """Apply every override against every contract; return the kept
    contracts.

    Unmatched override rules raise ``ConfigError`` listing every
    rule that fired zero times.  This is the config-drift catch:
    when a pack version bump renames a rule's ``desc:``, the user's
    override silently stops applying and the rule re-enables itself.
    Failing fast with a list of stale match clauses is far better
    than a quiet behavior change.
    """
    kept: list[ContractEntry] = []
    for contract in contracts:
        drop = False
        for rule in overrides:
            if _matches_override(rule, contract):
                rule.matched_count += 1
                if rule.disabled:
                    drop = True
                else:
                    _apply_override_effects(rule, contract)
        if not drop:
            kept.append(contract)

    unmatched = [r for r in overrides if r.matched_count == 0]
    if unmatched:
        details = "; ".join(f"match={r.match!r}" for r in unmatched)
        raise ConfigError(
            f"Agent '{agent_id}': {len(unmatched)} override rule(s) matched "
            f"no contract. pack contents may have changed.  Update or "
            f"remove these match clauses: {details}"
        )
    return kept


def _parse_tool_rename(raw: Any, agent_id: str) -> dict[str, str]:
    """Validate and normalize the ``tool_rename:`` mapping.

    Schema: ``{old_name: new_name, ...}``. both string, both
    non-empty.  The mapping must not be cyclic; cycles would make
    rewrite order observable, which is a bad surface to expose.

    Returns an empty dict when the section is absent, so callers can
    treat "rename" uniformly.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(
            f"Agent '{agent_id}': 'tool_rename' must be a mapping of "
            f"old_name -> new_name, got {type(raw).__name__}"
        )
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not k.strip():
            raise ConfigError(
                f"Agent '{agent_id}': 'tool_rename' keys must be non-empty "
                f"strings; got {k!r}"
            )
        if not isinstance(v, str) or not v.strip():
            raise ConfigError(
                f"Agent '{agent_id}': 'tool_rename' values must be non-empty "
                f"strings; got {v!r} for key {k!r}"
            )
        out[k] = v
    # Reject self-mappings before the cycle check. ``exec: exec``
    # technically satisfies ``out[v] == k`` but the dedicated message
    # is more actionable than "cycle between exec and exec".
    for k, v in out.items():
        if k == v:
            raise ConfigError(
                f"Agent '{agent_id}': 'tool_rename' has a no-op self-mapping "
                f"{k!r} -> {v!r}; remove the entry"
            )
    # Cycle check. rewrite order would be observable otherwise (a→b
    # then b→a flips back).
    for k, v in out.items():
        if v in out and out[v] == k:
            raise ConfigError(
                f"Agent '{agent_id}': 'tool_rename' contains a cycle "
                f"between {k!r} and {v!r}"
            )
    return out


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
    # walk the whole tree once. keeps the rest of the loader naive.
    raw = _interpolate_env(raw)

    config = SponsoConfig(
        version=str(raw.get("version", "1")),
        defaults=raw.get("defaults", {}),
        extractor=_parse_extractor_section(raw.get("extractor")),
        judge=_parse_judge_section(raw.get("judge")),
        performance=_parse_performance_section(raw.get("performance")),
        runtime=_parse_runtime_section(raw.get("runtime")),
        tool_policy=_parse_tool_policy_section(raw.get("tool_policy")),
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

    base_dir = path.parent.resolve()

    for agent_id, agent_data in agents_raw.items():
        if isinstance(agent_data, list):
            # Bare list. treat each entry as an unconditional contract
            ac = AgentConfig(agent_id=agent_id)
            for item in agent_data:
                ac.contracts.append(
                    ContractEntry(
                        guarantee=_parse_constraint_entry(item),
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
            ac = AgentConfig(agent_id=agent_id)

            workspace_raw = agent_data.get("workspace")
            if workspace_raw is not None and (
                not isinstance(workspace_raw, str) or not workspace_raw.strip()
            ):
                raise ConfigError(
                    f"Agent '{agent_id}': 'workspace' must be a non-empty "
                    f"string path, got {workspace_raw!r}"
                )
            tool_rename = _parse_tool_rename(agent_data.get("tool_rename"), agent_id)

            includes = agent_data.get("include", [])
            if includes:
                if not isinstance(includes, list):
                    raise ConfigError(
                        f"Agent '{agent_id}': 'include' must be a list of "
                        f"pack specs (e.g. ['sponsio:core/universal']), "
                        f"got {type(includes).__name__}"
                    )
                for spec in includes:
                    ac.contracts.extend(
                        _load_pack_contracts(spec, base_dir, agent_id, set())
                    )

            contracts_raw = agent_data.get("contracts", [])
            if not isinstance(contracts_raw, list):
                raise ConfigError(f"Agent '{agent_id}': 'contracts' must be a list")
            # Every entry under ``contracts:`` is a real contract.
            # an ``E:`` (enforcement formula) plus an optional ``A:``
            # (assumption). No other entry shapes live in this list.
            for item in contracts_raw:
                ac.contracts.append(_parse_contract_entry(item, agent_id))

            for contract in ac.contracts:
                _rewrite_contract_entry(contract, workspace_raw, tool_rename, agent_id)

            # ``customized:``. agent-level block of ``match:`` + effect
            # entries that adjust contracts loaded above (disable,
            # retune args, narrow assumption, change threshold).
            # Customized entries are NOT contracts and don't share the
            # contract shape, so they stay in their own block.
            #
            # The legacy aliases ``tweaks:`` and ``overrides:`` are no
            # longer accepted. files using them must be migrated to
            # ``customized:``.  Mention them in the error so older
            # config files get an actionable diagnostic instead of a
            # silent no-op.
            for legacy in ("tweaks", "overrides"):
                if agent_data.get(legacy) is not None:
                    raise ConfigError(
                        f"Agent '{agent_id}': '{legacy}:' is no longer "
                        f"accepted. rename it to 'customized:'."
                    )
            block_raw = agent_data.get("customized")
            if block_raw is not None:
                if not isinstance(block_raw, list):
                    raise ConfigError(
                        f"Agent '{agent_id}': 'customized:' must be a list "
                        f"of customized entries, got {type(block_raw).__name__}"
                    )
                customized = [
                    _parse_override_rule(r, agent_id, i)
                    for i, r in enumerate(block_raw)
                ]
                ac.contracts = _apply_overrides(ac.contracts, customized, agent_id)

            config.agents[agent_id] = ac
        else:
            raise ConfigError(f"Agent '{agent_id}': value must be a mapping or list")

    return config


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


def _compile_structured(entry: ConstraintEntry) -> Any:
    """Compile a structured constraint entry to a deterministic formula.

    Resolves ``entry.pattern`` against the deterministic pattern library
    (:func:`sponsio.generation.dsl_to_contract.get_available_patterns`).
    Stochastic / LLM-judged contracts are an extension point that this
    build does not ship, so an unknown pattern here is a hard parse
    error rather than a silent fall-through.
    """
    from sponsio.generation.dsl_to_contract import get_available_patterns

    det_registry = get_available_patterns()
    if entry.pattern not in det_registry:
        raise ConfigError(
            f"Unknown pattern '{entry.pattern}'. "
            f"Available patterns: {sorted(det_registry.keys())}. "
            f"(LLM-judged stochastic patterns are not supported in this "
            f"build; the engine is deterministic-only.)"
        )

    fn = det_registry[entry.pattern]
    coerced_args = []
    for a in entry.args:
        if isinstance(a, str) and a.isdigit():
            coerced_args.append(int(a))
        else:
            coerced_args.append(a)
    compiled = fn(*coerced_args)
    # ``desc:`` from the YAML overrides the pattern factory's default
    # desc. useful when one contract has multiple structured clauses
    # that should share a clearer per-clause label, or when the
    # factory's auto-generated desc is too terse for the trace view.
    # ``DetFormula`` is a ``frozen=True`` dataclass, so attribute
    # assignment fails silently (FrozenInstanceError); use
    # ``dataclasses.replace`` to construct a new instance with the
    # overridden desc.
    if entry.desc and hasattr(compiled, "desc"):
        import dataclasses

        if dataclasses.is_dataclass(compiled):
            try:
                compiled = dataclasses.replace(compiled, desc=entry.desc)
            except (TypeError, ValueError):
                # ``replace`` rejects non-init fields or unknown kwargs;
                # fall through to the assignment branch below for
                # non-dataclass shapes.
                pass
        else:
            try:
                compiled.desc = entry.desc
            except (AttributeError, TypeError):
                # Slots without ``desc`` or otherwise non-mutable.
                # accept the factory default rather than crash at load.
                pass

    # Many det patterns (``arg_blacklist``, ``called_with``,
    # ``arg_field_has``-style derivatives, ...) inline regex args
    # into the AST.  Pre-compile them now so an unsupported regex
    # feature (variable-width lookbehind, unbalanced groups, ...)
    # surfaces at config load instead of as a runtime ``re.error``
    # the first time the relevant tool fires.
    from sponsio.formulas.regex_check import (
        RegexValidationError,
        check_regexes,
    )

    formula_ast = getattr(compiled, "formula", None)
    if formula_ast is not None:
        try:
            check_regexes(formula_ast)
        except RegexValidationError as e:
            raise ConfigError(
                f"Invalid regex in pattern={entry.pattern!r} args={entry.args!r}: {e}"
            ) from e
    return compiled


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
        raise ConfigError(f"Failed to parse ltl formula {entry.ltl!r}: {e}") from e

    from sponsio.formulas.regex_check import RegexValidationError, check_regexes

    try:
        check_regexes(formula)
    except RegexValidationError as e:
        raise ConfigError(f"Invalid regex in ltl formula {entry.ltl!r}: {e}") from e

    return DetFormula(
        formula=formula,
        desc=entry.desc or entry.ltl,
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
    from sponsio.generation.dsl_to_contract import (
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
        # Config-driven path. a malformed DSL entry in a yaml file is
        # an op-level problem. Surface as a compile failure (None)
        # rather than crashing the loader; validators decide whether
        # to treat None as fatal.
        return None
    if result.is_det:
        return result.hard
    if result.is_sto:
        return result.sto
    return None


def _resolve_strict_compile(mode: str | None) -> bool:
    """Decide whether contract compile failures should hard-raise or soft-warn.

    Precedence:

    1. ``SPONSIO_STRICT_COMPILE`` env (``1`` / ``true`` / ``yes`` → strict;
       ``0`` / ``false`` / ``no`` → non-strict).  User has the final say.
    2. ``defaults.mode`` from yaml: ``enforce`` → strict, anything else
       (``observe`` / unset) → non-strict.

    The intent: enforce mode defaults to strict because a silently-skipped
    contract becomes a security gap in production; observe mode defaults
    to non-strict because the whole point of observe is to keep running
    and surface what would fire. one bad contract shouldn't take down
    the other 20 along with it.
    """
    import os

    env = os.environ.get("SPONSIO_STRICT_COMPILE")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    return (mode or "").strip().lower() == "enforce"


def _short_constraint_label(value: Any) -> str:
    """Best-effort label for a skipped constraint shown in the warning.

    ``value`` is the raw ``ce.guarantee`` field. a ``ConstraintEntry``,
    a list of them, or ``None``.  Returns the first non-empty handle we
    can find (pattern name + args / ltl text / nl text), truncated for
    readability.  Pure cosmetic; never raises.
    """
    if value is None:
        return "<empty enforcement>"
    if isinstance(value, list):
        return _short_constraint_label(value[0]) if value else "<empty list>"
    if isinstance(value, ConstraintEntry):
        if value.is_structured:
            return f"pattern={value.pattern!r} args={value.args!r}"[:120]
        if value.is_ltl:
            return (value.ltl or "")[:120]
        return (value.nl or "<empty>")[:120]
    return str(value)[:120]


def _synthesize_tool_policy_contract(
    policy: ToolPolicySection,
) -> dict[str, Any] | None:
    """Build a synthetic ``tool_allowlist`` contract dict from a
    ``ToolPolicySection``.

    Returns ``None`` when policy is ``allow`` (no injection). Returns a
    contract dict ready for ``BaseGuard``'s ``contracts=`` kwarg when
    policy is ``deny``. ``approved=[]`` under deny still produces a
    contract: the empty allowlist semantically means "block every
    tool", and ``tool_allowlist([])`` already handles that.

    The synthesized contract carries a clear desc so a violation in
    the trace surfaces as "tool_policy: default-deny blocked X" rather
    than the generic "only [] may be called".
    """
    if policy.default != "deny":
        return None
    from sponsio.patterns.library import tool_allowlist

    if policy.approved:
        tools_label = ", ".join(policy.approved)
        desc = f"tool_policy default-deny: only [{tools_label}] approved"
    else:
        desc = "tool_policy default-deny: no tools approved (everything blocked)"
    formula = tool_allowlist(policy.approved, desc=desc)
    return {"guarantee": formula, "desc": desc}


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

    strict = _resolve_strict_compile(config.defaults.get("mode"))

    contract_dicts: list[dict] = []
    skipped: list[tuple[str, str]] = []
    for ce in ac.contracts:
        try:
            entry: dict[str, Any] = {
                "guarantee": _compile_field(
                    ce.guarantee, tool_inventory=tool_inventory
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
            if ce.activate_at is not None:
                entry["activate_at"] = ce.activate_at
            contract_dicts.append(entry)
        except ConfigError as exc:
            # In strict mode (enforce default, or SPONSIO_STRICT_COMPILE=1)
            # any compile failure aborts loading. silently shipping a
            # broken enforcement rule in production is worse than the
            # crash.  In non-strict mode (observe default) skip the bad
            # contract and surface a single batched warning so the rest of
            # the yaml stays usable while reviewing.
            if strict:
                raise
            label = ce.desc or _short_constraint_label(ce.guarantee)
            skipped.append((label, str(exc)))

    if skipped:
        import warnings

        bullet = "\n  - ".join(f"{label}: {err}" for label, err in skipped)
        warnings.warn(
            f"sponsio: skipped {len(skipped)} contract(s) for agent "
            f"{agent_id!r} (observe mode, non-strict compile). "
            f"Set SPONSIO_STRICT_COMPILE=1 or `defaults.mode: enforce` "
            f"to escalate to ConfigError.\n  - " + bullet,
            UserWarning,
            stacklevel=2,
        )

    # NOTE: synthesis of the ``tool_policy`` deny contract used to
    # happen here. It now lives in a single place inside
    # ``BaseGuard.__init__`` so factory / direct-class / yaml entry
    # paths all behave identically and the desc string is no longer a
    # shared contract between two files. The ``tool_policy`` itself is
    # still forwarded through ``Sponsio()`` so BaseGuard can synthesize
    # from it.

    kwargs: dict[str, Any] = {
        "agent_id": agent_id,
        "contracts": contract_dicts if contract_dicts else None,
    }

    if config.defaults.get("verbose") is not None:
        kwargs["verbose"] = config.defaults["verbose"]
    if config.defaults.get("verbosity") is not None:
        kwargs["verbosity"] = config.defaults["verbosity"]
    # ``defaults.auto_summary: false`` stops the atexit ``Sponsio
    # Session Summary`` block from printing. useful in scripted
    # replays / demo gifs / tests where the framework's own narration
    # is meant to be the last visible output, and in production
    # services where the summary on shutdown is just noise in stderr.
    if config.defaults.get("auto_summary") is not None:
        kwargs["auto_summary"] = config.defaults["auto_summary"]

    return kwargs


def build_extractor(section: ExtractorSection) -> Any:
    """Construct a :class:`UnifiedExtractor` from an ``extractor:`` section.

    Returns ``None`` if no provider is configured. callers fall
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
    """Construct a ``StoEvaluator`` from a ``judge:`` section.

    This build ships no ``StoEvaluator`` implementation; this helper is
    a thin wrapper over the entry-point auto-discovery that lives in
    :func:`sponsio.integrations.base._discover_sto_evaluator`. When an
    implementation is registered via the ``sponsio.evaluators``
    entry-point group it is instantiated and returned (without
    forwarding section knobs, since the impl reads its own config);
    otherwise raises ``ConfigError``.

    Section knobs (``fallback_mode``, ``circuit_breaker``, ...) are
    parsed and validated by ``JudgeSection`` itself; they're advisory
    here because this layer can't know which impl is going to be
    discovered or what its constructor expects.
    """
    from sponsio.integrations.base import _discover_sto_evaluator

    ev = _discover_sto_evaluator()
    if ev is None:
        raise ConfigError(
            "judge: section requires a StoEvaluator implementation. "
            "This build ships none; register one via the "
            "`sponsio.evaluators` entry-point group for it to be "
            "auto-discovered."
        )
    # Section is currently unused; impls read their own config.
    # Kept on the signature so existing callers pass through unchanged.
    del section
    return ev


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
    policy_contract = _synthesize_tool_policy_contract(config.tool_policy)
    for agent_id, ac in config.agents.items():
        agent_obj = Agent(id=agent_id)
        if policy_contract is not None:
            contracts.append(
                Contract(
                    agent=agent_obj,
                    guarantee=policy_contract["guarantee"],
                    desc=policy_contract["desc"],
                )
            )
        for ce in ac.contracts:
            e = _compile_field(ce.guarantee, llm_extractor, tool_inventory)
            a = _compile_field(ce.assumption, llm_extractor, tool_inventory)
            if e is None:
                continue
            contracts.append(
                Contract(
                    agent=agent_obj,
                    guarantee=e,
                    assumption=a,
                    desc=ce.desc,
                    alpha=ce.alpha,
                    beta=ce.beta,
                )
            )

    system = System(name="config")
    system._contracts = contracts
    return system

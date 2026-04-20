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
    """A single constraint — either NL string or structured pattern+args."""

    nl: str | None = None
    pattern: str | None = None
    args: list[Any] = field(default_factory=list)
    source: str | None = None

    @property
    def is_structured(self) -> bool:
        return self.pattern is not None


@dataclass
class ContractEntry:
    """One (assumption, enforcement) pair from the YAML.

    Each field may hold None, one ``ConstraintEntry``, or a list of
    them (= logical AND). ``assumption`` is optional; ``enforcement`` is
    required.
    """

    enforcement: ConstraintEntry | list[ConstraintEntry] = None  # type: ignore[assignment]
    assumption: ConstraintEntry | list[ConstraintEntry] | None = None
    desc: str | None = None


@dataclass
class AgentConfig:
    """Parsed contract config for a single agent."""

    agent_id: str
    contracts: list[ContractEntry] = field(default_factory=list)


@dataclass
class SponsoConfig:
    """Top-level parsed config."""

    version: str = "1"
    defaults: dict[str, Any] = field(default_factory=dict)
    tools: list[ToolEntry] = field(default_factory=list)
    agents: dict[str, AgentConfig] = field(default_factory=dict)


class ConfigError(Exception):
    """Raised when a config file is invalid."""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_constraint_entry(item: Any) -> ConstraintEntry:
    """Parse a single constraint entry (string or dict)."""
    if isinstance(item, str):
        return ConstraintEntry(nl=item)
    elif isinstance(item, dict):
        if "pattern" in item:
            args = item.get("args", [])
            if not isinstance(args, list):
                args = [args]
            return ConstraintEntry(
                pattern=item["pattern"],
                args=args,
                source=item.get("source"),
            )
        else:
            raise ConfigError(
                f"Constraint dict must have 'pattern' key, got: {list(item.keys())}"
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

    return ContractEntry(
        enforcement=_parse_constraint_field(e_raw),  # type: ignore[arg-type]
        assumption=_parse_constraint_field(a_raw),
        desc=desc,
    )


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

    config = SponsoConfig(
        version=str(raw.get("version", "1")),
        defaults=raw.get("defaults", {}),
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
    from sponsio.generation.nl_to_contract import parse_nl_unified

    if entry.is_structured:
        return _compile_structured(entry)

    result = parse_nl_unified(
        entry.nl,
        llm_extractor=llm_extractor,
        tool_inventory=tool_inventory,
    )
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
                )
            )

    system = System(name="config")
    system._contracts = contracts
    return system

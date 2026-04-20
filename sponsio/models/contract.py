"""Contract dataclass — one assume/enforcement pair for an agent.

A ``Contract`` binds a single ``assumption`` (precondition over the trace)
to a single ``enforcement`` (what the agent must satisfy when the
assumption holds). An agent with multiple independent rules has multiple
``Contract`` entries — ``System.contracts`` is already a flat list, so
no new container type is needed.

Both ``assumption`` and ``enforcement`` accept either a single constraint
or a list. A list is interpreted as the logical AND of its elements.
``assumption=None`` (the default) means the contract is unconditional.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sponsio.models.agent import Agent

# A constraint is a hard formula, a sto constraint, or a list of either
Constraint = Any  # Formula | DetFormula | StoFormula | list[...]

# ANSI helpers
_BOLD = "1"
_GREEN = "32"
_YELLOW = "33"
_DIM = "2"


def _ansi(code: str, text: str, colorize: bool) -> str:
    if not colorize:
        return text
    return f"\033[{code}m{text}\033[0m"


def _as_list(value: Any) -> list:
    """Normalize a scalar / list / None to a flat list."""
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


@dataclass
class Contract:
    """A single assume/enforcement pair bound to an agent.

    Attributes:
        agent: The agent this contract belongs to.
        enforcement: What the agent must satisfy. Required. May be a
            single constraint or a list (list = logical AND).
        assumption: Precondition over the trace. ``None`` means the
            contract is unconditional. May be a single constraint or a
            list (list = logical AND).
        desc: Optional human-readable label for this contract.
    """

    agent: Agent
    enforcement: Constraint = None
    assumption: Constraint | None = None
    desc: str | None = None

    def __post_init__(self) -> None:
        if self.enforcement is None or (
            isinstance(self.enforcement, list) and not self.enforcement
        ):
            raise ValueError(
                f"Contract(agent={self.agent.id!r}) requires a non-empty enforcement. "
                f"Use Contract(..., enforcement=<constraint>) or provide a list."
            )

    # -----------------------------------------------------------------
    # Normalized views (plural properties)
    # -----------------------------------------------------------------

    @property
    def assumptions(self) -> list:
        """Assumption as a flat list (empty if unconditional).

        The singular ``.assumption`` field holds the canonical value
        (scalar, list, or ``None``); this property normalizes it to a
        list for iteration.
        """
        return _as_list(self.assumption)

    @property
    def enforcements(self) -> list:
        """Enforcement as a flat list.

        The singular ``.enforcement`` field holds the canonical value
        (scalar or list); this property normalizes it to a list for
        iteration.
        """
        return _as_list(self.enforcement)

    @property
    def is_unconditional(self) -> bool:
        return not self.assumptions

    # -----------------------------------------------------------------
    # Pretty printing
    # -----------------------------------------------------------------

    def to_str(self, colorize: bool = False) -> str:
        """Human-readable A/E representation."""
        bar = _ansi(_DIM, "\u258e", colorize)

        agent_name = _ansi(_BOLD, self.agent.id, colorize)
        lines = [f"{bar} {_ansi(_BOLD, 'contract', colorize)} \u00b7 {agent_name}"]
        if self.desc:
            lines.append(f"{bar} {_ansi(_DIM, self.desc, colorize)}")
        lines.append(f"{bar} ")

        a_tri = _ansi(_YELLOW, "\u25b8", colorize)
        e_tri = _ansi(_GREEN, "\u25b8", colorize)
        blank = " " * 8

        a_label = _ansi(_DIM, "assume  ", colorize)
        a_descs = [getattr(a, "desc", str(a)) for a in self.assumptions]
        if not a_descs:
            lines.append(f"{bar} {a_label}{_ansi(_DIM, 'true', colorize)}")
        else:
            lines.append(f"{bar} {a_label}{a_tri} {a_descs[0]}")
            for a in a_descs[1:]:
                lines.append(f"{bar} {blank}{a_tri} {a}")

        e_label = _ansi(_DIM, "enforce ", colorize)
        e_descs = [getattr(e, "desc", str(e)) for e in self.enforcements]
        lines.append(f"{bar} {e_label}{e_tri} {e_descs[0]}")
        for e in e_descs[1:]:
            lines.append(f"{bar} {blank}{e_tri} {e}")

        return "\n".join(lines)

    def __str__(self) -> str:
        return self.to_str(colorize=False)

    def __repr__(self) -> str:
        a_count = len(self.assumptions)
        e_count = len(self.enforcements)
        return (
            f"Contract(agent={self.agent.id!r}, "
            f"assumption={a_count}, enforcement={e_count})"
        )


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def make_contracts(
    agent: Agent,
    *,
    enforcements: list | None = None,
    contracts: list[dict] | None = None,
) -> list[Contract]:
    """Build a list of ``Contract`` objects from the two main input shapes.

    Args:
        agent: The agent all contracts belong to.
        enforcements: Shortcut for unconditional contracts. Each item
            becomes one ``Contract(agent, enforcement=item)`` with no
            assumption. Useful for the simple "list of rules" case.
        contracts: List of dicts, each with ``assumption`` (optional)
            and ``enforcement`` (required). Each dict becomes one
            ``Contract``. ``assumption`` / ``enforcement`` may be a
            scalar or a list; lists are preserved for later AND-combine.

    Returns:
        A flat list of ``Contract`` objects, ready for
        ``System._contracts.extend(...)``.
    """
    out: list[Contract] = []

    for item in enforcements or []:
        out.append(Contract(agent=agent, enforcement=item))

    for entry in contracts or []:
        if not isinstance(entry, dict):
            raise TypeError(
                f"contracts[] entries must be dict, got {type(entry).__name__}: {entry!r}"
            )
        enforcement = entry.get("enforcement") or entry.get("E")
        if enforcement is None:
            raise ValueError(
                f"Contract entry missing 'enforcement' (or 'E'): {entry!r}"
            )
        assumption = entry.get("assumption", entry.get("A"))
        desc = entry.get("desc")
        out.append(
            Contract(
                agent=agent,
                enforcement=enforcement,
                assumption=assumption,
                desc=desc,
            )
        )

    return out

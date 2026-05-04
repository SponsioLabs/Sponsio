"""Fluent helpers for authoring Sponsio contracts in Python."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


def _merge(existing: Any | None, value: Any) -> Any:
    """Append repeated fields while preserving scalar shorthand."""
    if existing is None:
        return value
    if isinstance(existing, list):
        return [*existing, value]
    return [existing, value]


@dataclass(frozen=True)
class ContractBuilder:
    """Small fluent builder for Python inline contracts.

    Examples::

        contract("refund policy gate")
            .assume("called `issue_refund`")
            .guarantees("must call `check_policy` before `issue_refund`")

    Repeated ``assume`` or ``guarantees`` calls are AND-combined,
    matching list-valued ``assumption`` / ``guarantee`` dict fields.
    """

    desc: str | None = None
    assumption: Any | None = None
    guarantee: Any | None = None
    alpha: float = 1.0
    beta: float = 1.0

    def assume(self, value: Any) -> ContractBuilder:
        """Add an assumption condition, the A side of an A/G contract."""
        return replace(self, assumption=_merge(self.assumption, value))

    def guarantees(self, value: Any) -> ContractBuilder:
        """Add a guarantee, the G side of an A/G contract."""
        return replace(self, guarantee=_merge(self.guarantee, value))

    def thresholds(
        self,
        *,
        alpha: float | None = None,
        beta: float | None = None,
    ) -> ContractBuilder:
        """Set stochastic assumption/guarantee thresholds."""
        return replace(
            self,
            alpha=self.alpha if alpha is None else alpha,
            beta=self.beta if beta is None else beta,
        )

    def threshold(
        self,
        *,
        alpha: float | None = None,
        beta: float | None = None,
    ) -> ContractBuilder:
        """Alias for ``thresholds``.

        The singular reads naturally for one contract, while the plural
        remains available for callers who think in A/G threshold pairs.
        """
        return self.thresholds(alpha=alpha, beta=beta)

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical inline contract dict accepted by guards."""
        if self.guarantee is None:
            raise ValueError("contract(...).guarantees(...) is required")
        out: dict[str, Any] = {
            "guarantee": self.guarantee,
            "alpha": self.alpha,
            "beta": self.beta,
        }
        if self.desc is not None:
            out["desc"] = self.desc
        if self.assumption is not None:
            out["assumption"] = self.assumption
        return out


def contract(desc: str | None = None) -> ContractBuilder:
    """Start a Python inline contract builder."""
    return ContractBuilder(desc=desc)


__all__ = ["ContractBuilder", "contract"]

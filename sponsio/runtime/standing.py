"""Standing approvals — the human decision that outlives its escalation.

An ``EscalateToHuman`` rule refuses a call and waits for a person. When the
person answers "Always allow" in the console, that decision is recorded
server-side as a *standing approval* for the exact (agent, tool, contract)
triple. This module is the runtime half of that loop: the guard pulls the
standing list at construction (same trust domain and credentials as the
cloud rulebook checkout) and ``EscalateToHuman`` consults it before
refusing — a covered call executes, recorded as ``observed`` with the
approval named in the message, instead of stopping to ask a question a
human already answered.

Failure policy is fail-closed in the safe direction: no credentials, no
network, a 500 — the standing set is simply empty and every escalation
waits on a human exactly as before. A standing approval can only ever
RELEASE a call; nothing in this module can block one.

The set is fetched once per guard construction, like the rulebook itself.
A revocation in the console therefore takes effect on the next run — the
same freshness contract the book has, and the reason this stays off the
per-call hot path entirely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_WS = re.compile(r"\s+")


def _norm(text: str | None) -> str:
    """The same label normalization the console matches decisions with:
    backticks out, whitespace collapsed, case folded."""
    return _WS.sub(" ", (text or "").replace("`", "")).strip().lower()


@dataclass(frozen=True)
class StandingApproval:
    agent: str
    tool: str
    contract: str
    approval_id: str = ""
    decided_at: str = ""


@dataclass
class StandingRegistry:
    """The standing approvals one guard runs under.

    Matching is deliberately exact on all three coordinates (normalized
    contract sentence, exact tool, exact agent): an approval granted for
    one agent's rule must never release a different agent's call, and a
    wording drift fails toward escalation, never toward release.
    """

    _entries: list[StandingApproval] = field(default_factory=list)

    def load(self, entries: list[StandingApproval]) -> None:
        self._entries = list(entries)

    def clear(self) -> None:
        self._entries = []

    def __len__(self) -> int:
        return len(self._entries)

    def covers(
        self, agent_id: str | None, tool: str | None, *contract_descs: str | None
    ) -> StandingApproval | None:
        """The approval releasing this call, or None.

        ``contract_descs`` takes every spelling the caller knows for the
        violated contract (authored sentence, compiled formula description):
        the console records whichever the trace carried, so either may be
        the stored form.
        """
        wanted = {_norm(d) for d in contract_descs if d} - {""}
        if not wanted:
            return None
        a9, t9 = _norm(agent_id), (tool or "").strip()
        for entry in self._entries:
            if entry.tool != t9:
                continue
            if _norm(entry.agent) != a9:
                continue
            if _norm(entry.contract) in wanted:
                return entry
        return None


# One registry per process, set by the guard that pulled it. A module-level
# seam (rather than threading it through Monitor and every strategy
# constructor) because strategies are USER-constructed objects — the guard
# has no path into an ``EscalateToHuman(...)`` instance the user built
# before the guard existed.
_registry = StandingRegistry()


def registry() -> StandingRegistry:
    return _registry


def load_from_cloud(client=None, *, quiet: bool = False) -> int:
    """Pull the standing list; empty on any failure (fail-closed).

    Returns the number of standing approvals loaded, for banners.
    """
    try:
        from sponsio.cloud.client import CloudClient

        client = client or CloudClient()
        if not client.configured:
            _registry.clear()
            return 0
        rows = client.standing_approvals()
        entries = [
            StandingApproval(
                agent=str(r.get("agent") or ""),
                tool=str(r.get("tool") or ""),
                contract=str(r.get("contract") or ""),
                approval_id=str(r.get("id") or ""),
                decided_at=str(r.get("decidedAt") or ""),
            )
            for r in rows
            if r.get("tool")
        ]
        _registry.load(entries)
        if entries and not quiet:
            print(
                f"  standing approvals ← cloud · {len(entries)} active "
                f"(EscalateToHuman honors them; revoke in the console)"
            )
        return len(entries)
    except Exception:  # noqa: BLE001 — any failure means: everything escalates
        _registry.clear()
        return 0

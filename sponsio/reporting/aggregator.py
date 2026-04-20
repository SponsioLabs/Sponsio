"""Aggregate shadow-mode session events into a Report.

Takes an iterable of :class:`~sponsio.reporting.reader.SessionEvent` and
folds it into a :class:`Report` dataclass — the single input to every
renderer.  Pure function; no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from sponsio.reporting.reader import SessionEvent


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class ContractStat:
    """Per-contract rollup of violations."""

    constraint: str
    pipeline: str
    violations: int = 0
    blocked: int = 0
    observed: int = 0
    retrying: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    sample_message: str = ""  # one example message, for context


@dataclass
class SessionStat:
    """Per-session (per-file) rollup."""

    source: str  # filename stem
    agent_id: str
    first_seen: float = 0.0
    last_seen: float = 0.0
    events: int = 0
    violations: int = 0


@dataclass
class Report:
    """Aggregated view of a slice of shadow-mode event log.

    The renderer consumes this directly; no further aggregation happens
    downstream.
    """

    agents: list[str] = field(default_factory=list)
    window_start: float = 0.0
    window_end: float = 0.0

    total_events: int = 0
    total_sessions: int = 0

    passed: int = 0
    blocked: int = 0
    observed: int = 0
    retrying: int = 0

    by_contract: list[ContractStat] = field(default_factory=list)
    by_session: list[SessionStat] = field(default_factory=list)

    @property
    def violations(self) -> int:
        """All caught-or-would-have-been-caught violations."""
        return self.blocked + self.observed + self.retrying

    @property
    def pass_rate(self) -> float:
        """Fraction of evaluated events that passed.  ``0.0`` if no events."""
        if self.total_events == 0:
            return 0.0
        return self.passed / self.total_events


# ---------------------------------------------------------------------------
# Core aggregator
# ---------------------------------------------------------------------------


def aggregate(events: Iterable[SessionEvent]) -> Report:
    """Fold an event stream into a Report.

    Events may arrive out of order; the aggregator tracks min/max ``ts``
    per contract and per session.

    Contracts are identified by ``(constraint, pipeline)`` — the same
    text under det and sto count as distinct rows (they are different
    checks).
    """
    rep = Report()

    agents: set[str] = set()
    by_contract: dict[tuple[str, str], ContractStat] = {}
    by_session: dict[str, SessionStat] = {}

    for ev in events:
        rep.total_events += 1
        agents.add(ev.agent_id)

        # Window bounds
        if rep.window_start == 0.0 or ev.ts < rep.window_start:
            rep.window_start = ev.ts
        if ev.ts > rep.window_end:
            rep.window_end = ev.ts

        # Outcome rollup
        if ev.is_blocked:
            rep.blocked += 1
        elif ev.is_observed:
            rep.observed += 1
        elif ev.is_retrying:
            rep.retrying += 1
        elif ev.is_pass:
            rep.passed += 1
        # "escalated" falls into `violations` via is_violation but we
        # don't give it its own top-line counter to keep the report
        # readable; surfaced in by_contract instead.

        # Per-session
        if ev.source_file is not None:
            key = ev.source_file.stem
            sess = by_session.get(key)
            if sess is None:
                sess = SessionStat(source=key, agent_id=ev.agent_id)
                by_session[key] = sess
            sess.events += 1
            if ev.is_violation:
                sess.violations += 1
            if sess.first_seen == 0.0 or ev.ts < sess.first_seen:
                sess.first_seen = ev.ts
            if ev.ts > sess.last_seen:
                sess.last_seen = ev.ts

        # Per-contract (violations only — passes bloat the table)
        if not ev.is_violation:
            continue
        ckey = (ev.constraint, ev.pipeline)
        stat = by_contract.get(ckey)
        if stat is None:
            stat = ContractStat(constraint=ev.constraint, pipeline=ev.pipeline)
            by_contract[ckey] = stat
        stat.violations += 1
        if ev.is_blocked:
            stat.blocked += 1
        elif ev.is_observed:
            stat.observed += 1
        elif ev.is_retrying:
            stat.retrying += 1
        if stat.first_seen == 0.0 or ev.ts < stat.first_seen:
            stat.first_seen = ev.ts
        if ev.ts > stat.last_seen:
            stat.last_seen = ev.ts
        if not stat.sample_message and ev.result_message:
            stat.sample_message = ev.result_message

    rep.agents = sorted(agents)
    rep.total_sessions = len(by_session)
    rep.by_contract = sorted(
        by_contract.values(), key=lambda c: (-c.violations, c.constraint)
    )
    rep.by_session = sorted(
        by_session.values(), key=lambda s: (-s.violations, -s.events, s.source)
    )

    return rep

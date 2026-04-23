"""Phase 2: Mine constraint patterns from historical execution traces.

Usage::

    from sponsio.discovery.extractors import TraceMiner
    from sponsio.models.trace import Trace

    traces = [Trace.load(f) for f in trace_files]
    miner = TraceMiner(confidence_threshold=0.95, min_support=5)
    proposals = miner.extract(traces)

    for p in proposals:
        print(p.formula.pattern_name, p.formula.desc, p.confidence)
"""

from __future__ import annotations

from collections import Counter, defaultdict

from sponsio.discovery._types import (
    ConstraintStatus,
    DiscoverySource,
    ProposedConstraint,
)
from sponsio.models.trace import Trace
from sponsio.patterns.library import (
    always_followed_by,
    idempotent,
    must_precede,
    mutual_exclusion,
    rate_limit,
)


class TraceMiner:
    """Mine constraint patterns from historical traces.

    Runs four independent statistical analyses:
    - Ordering: must_precede candidates
    - Exclusion: mutual_exclusion candidates
    - Frequency: rate_limit / idempotent candidates
    - Sequence: always_followed_by candidates

    Args:
        confidence_threshold: Minimum confidence for a pattern to be proposed.
        min_support: Minimum number of traces containing both tools.
    """

    def __init__(
        self,
        confidence_threshold: float = 0.95,
        min_support: int = 5,
    ) -> None:
        self._threshold = confidence_threshold
        self._min_support = min_support

    def extract(self, traces: list[Trace]) -> list[ProposedConstraint]:
        """Mine patterns from a list of traces.

        Args:
            traces: Historical execution traces.

        Returns:
            List of proposed constraints with confidence and evidence.
        """
        if not traces:
            return []

        results: list[ProposedConstraint] = []
        results.extend(self._mine_ordering(traces))
        results.extend(self._mine_exclusion(traces))
        results.extend(self._mine_frequency(traces))
        results.extend(self._mine_sequences(traces))
        return results

    # -----------------------------------------------------------------
    # Analysis 1: Ordering (must_precede)
    # -----------------------------------------------------------------

    def _mine_ordering(self, traces: list[Trace]) -> list[ProposedConstraint]:
        """Find pairs where A always precedes B."""
        # For each trace, extract ordered tool sequence
        pair_total: Counter[tuple[str, str]] = Counter()  # traces containing both
        pair_ordered: Counter[tuple[str, str]] = Counter()  # A before B

        for trace in traces:
            tools_seen: list[str] = []
            tool_set: set[str] = set()
            for event in trace.events:
                if event.event_type == "tool_call" and event.tool:
                    tools_seen.append(event.tool)
                    tool_set.add(event.tool)

            # Check all pairs
            for a in tool_set:
                for b in tool_set:
                    if a == b:
                        continue
                    pair_total[(a, b)] += 1
                    # Check if a always appears before b
                    first_a = next(
                        (i for i, t in enumerate(tools_seen) if t == a), None
                    )
                    first_b = next(
                        (i for i, t in enumerate(tools_seen) if t == b), None
                    )
                    if (
                        first_a is not None
                        and first_b is not None
                        and first_a < first_b
                    ):
                        pair_ordered[(a, b)] += 1

        results: list[ProposedConstraint] = []
        for (a, b), total in pair_total.items():
            if total < self._min_support:
                continue
            ordered = pair_ordered.get((a, b), 0)
            confidence = ordered / total
            if confidence >= self._threshold:
                formula = must_precede(a, b)
                results.append(
                    ProposedConstraint(
                        formula=formula,
                        source=DiscoverySource.AUTO_EXTRACTED,
                        extractor="trace_mining",
                        confidence=round(confidence, 4),
                        status=ConstraintStatus.PROPOSED,
                        provenance=f"mined from {len(traces)} traces",
                        nl_description=f"{a} always precedes {b}",
                        evidence={
                            # ``args`` is consumed by the YAML emitter
                            # (``generate_yaml``'s structured-entry
                            # branch) — keep it aligned with
                            # ``must_precede(a, b)`` arg order.
                            "args": [a, b],
                            "support": total,
                            "ordered": ordered,
                            "total_traces": len(traces),
                            "confidence": round(confidence, 4),
                        },
                    )
                )
        return results

    # -----------------------------------------------------------------
    # Analysis 2: Exclusion (mutual_exclusion)
    # -----------------------------------------------------------------

    def _mine_exclusion(self, traces: list[Trace]) -> list[ProposedConstraint]:
        """Find pairs that never co-occur in the same trace."""
        tool_traces: defaultdict[str, set[int]] = defaultdict(set)

        for i, trace in enumerate(traces):
            for event in trace.events:
                if event.event_type == "tool_call" and event.tool:
                    tool_traces[event.tool].add(i)

        tools = list(tool_traces.keys())
        results: list[ProposedConstraint] = []
        seen: set[tuple[str, str]] = set()

        for a in tools:
            for b in tools:
                if a >= b:  # avoid duplicates
                    continue
                if (a, b) in seen:
                    continue

                support_a = len(tool_traces[a])
                support_b = len(tool_traces[b])
                co_occurrence = len(tool_traces[a] & tool_traces[b])

                if support_a < self._min_support or support_b < self._min_support:
                    continue

                if co_occurrence == 0:
                    formula = mutual_exclusion(a, b)
                    results.append(
                        ProposedConstraint(
                            formula=formula,
                            source=DiscoverySource.AUTO_EXTRACTED,
                            extractor="trace_mining",
                            confidence=1.0,
                            status=ConstraintStatus.PROPOSED,
                            provenance=f"mined from {len(traces)} traces",
                            nl_description=f"{a} and {b} never co-occur",
                            evidence={
                                "args": [a, b],
                                "support_a": support_a,
                                "support_b": support_b,
                                "co_occurrence": 0,
                                "total_traces": len(traces),
                            },
                        )
                    )
                    seen.add((a, b))

        return results

    # -----------------------------------------------------------------
    # Analysis 3: Frequency (rate_limit / idempotent)
    # -----------------------------------------------------------------

    def _mine_frequency(self, traces: list[Trace]) -> list[ProposedConstraint]:
        """Find tools with consistent max invocation counts."""
        tool_counts: defaultdict[str, list[int]] = defaultdict(list)

        for trace in traces:
            counts: Counter[str] = Counter()
            for event in trace.events:
                if event.event_type == "tool_call" and event.tool:
                    counts[event.tool] += 1
            for tool, count in counts.items():
                tool_counts[tool].append(count)

        results: list[ProposedConstraint] = []
        for tool, counts in tool_counts.items():
            if len(counts) < self._min_support:
                continue

            max_count = max(counts)
            # Check if all traces have the same max (consistent limit)
            if all(c <= max_count for c in counts):
                if max_count == 1:
                    formula = idempotent(tool)
                    args = [tool]
                    nl = f"{tool} is always called at most once"
                else:
                    formula = rate_limit(tool, max_count)
                    args = [tool, max_count]
                    nl = f"{tool} is called at most {max_count} times"

                results.append(
                    ProposedConstraint(
                        formula=formula,
                        source=DiscoverySource.AUTO_EXTRACTED,
                        extractor="trace_mining",
                        confidence=1.0,
                        status=ConstraintStatus.PROPOSED,
                        provenance=f"mined from {len(traces)} traces",
                        nl_description=nl,
                        evidence={
                            "args": args,
                            "max_count": max_count,
                            "counts": counts,
                            "total_traces": len(traces),
                        },
                    )
                )

        return results

    # -----------------------------------------------------------------
    # Analysis 4: Sequence (always_followed_by)
    # -----------------------------------------------------------------

    def _mine_sequences(self, traces: list[Trace]) -> list[ProposedConstraint]:
        """Find pairs where A is always eventually followed by B."""
        # For each (A, B): count traces where A appears and B follows
        pair_trigger: Counter[tuple[str, str]] = Counter()  # traces with A
        pair_followed: Counter[tuple[str, str]] = Counter()  # traces with A then B

        for trace in traces:
            tools_seq: list[str] = []
            for event in trace.events:
                if event.event_type == "tool_call" and event.tool:
                    tools_seq.append(event.tool)

            tool_set = set(tools_seq)
            for a in tool_set:
                for b in tool_set:
                    if a == b:
                        continue
                    pair_trigger[(a, b)] += 1
                    # Check if some occurrence of a is followed by b
                    for i, t in enumerate(tools_seq):
                        if t == a and b in tools_seq[i + 1 :]:
                            pair_followed[(a, b)] += 1
                            break

        results: list[ProposedConstraint] = []
        for (a, b), trigger_count in pair_trigger.items():
            if trigger_count < self._min_support:
                continue
            followed = pair_followed.get((a, b), 0)
            confidence = followed / trigger_count
            if confidence >= self._threshold:
                formula = always_followed_by(a, b)
                results.append(
                    ProposedConstraint(
                        formula=formula,
                        source=DiscoverySource.AUTO_EXTRACTED,
                        extractor="trace_mining",
                        confidence=round(confidence, 4),
                        status=ConstraintStatus.PROPOSED,
                        provenance=f"mined from {len(traces)} traces",
                        nl_description=f"{a} is always followed by {b}",
                        evidence={
                            "args": [a, b],
                            "trigger_count": trigger_count,
                            "followed_count": followed,
                            "total_traces": len(traces),
                            "confidence": round(confidence, 4),
                        },
                    )
                )

        return results

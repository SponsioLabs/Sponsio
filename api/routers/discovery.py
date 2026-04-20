"""Discovery suggestions — mine constraint candidates from the current trace."""

from __future__ import annotations

from fastapi import APIRouter

from api.state import state
from sponsio.discovery.extractors.trace_mining import TraceMiner

router = APIRouter()


@router.get("/suggestions")
def get_discovery_suggestions(agent_id: str | None = None, min_support: int = 1):
    """Mine constraint suggestions from the current in-memory trace.

    Returns a list shaped for the frontend `SuggestedContract` type.
    """
    trace = state.monitor.trace
    if not trace.events:
        return {"suggestions": []}

    if agent_id:
        filtered_events = [e for e in trace.events if e.agent == agent_id]
        if not filtered_events:
            return {"suggestions": []}
        from sponsio.models.trace import Trace

        trace = Trace(events=filtered_events)

    miner = TraceMiner(confidence_threshold=0.6, min_support=min_support)
    proposals = miner.extract([trace])

    suggestions = []
    for i, p in enumerate(proposals):
        if p.formula is None:
            continue
        suggestions.append(
            {
                "id": f"disc-{i}",
                "nlText": p.formula.desc,
                "patternName": p.formula.pattern_name,
                "confidence": round(p.confidence, 2),
                "reason": p.provenance or f"Mined from {len(trace.events)} events",
            }
        )
    return {"suggestions": suggestions}

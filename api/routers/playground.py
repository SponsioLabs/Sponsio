"""Playground endpoints for simulating agent actions."""

from fastapi import APIRouter, HTTPException

from api.schemas import (
    EnforcementResultResponse,
    PlaygroundActionRequest,
    PlaygroundActionResponse,
)
from api.state import state

router = APIRouter()


@router.post("/action", response_model=PlaygroundActionResponse)
def simulate_action(req: PlaygroundActionRequest):
    """Simulate a single agent action and return enforcement results."""
    if req.agent_id not in state.agents:
        raise HTTPException(404, f"Agent '{req.agent_id}' not found")

    results = state.monitor.check_action(
        agent_id=req.agent_id,
        action=req.action,
        event_type=req.event_type,
        metadata=req.metadata,
    )

    blocked = any(r.action == "blocked" for r in results)

    # Rollback the event from trace if blocked (same as BaseGuard.guard_before)
    if blocked and state.monitor.trace.events:
        state.monitor.trace.events.pop()

    return PlaygroundActionResponse(
        allowed=not blocked,
        results=[
            EnforcementResultResponse(
                action=r.action,
                message=r.message,
                retry_prompt=r.retry_prompt,
            )
            for r in results
        ],
    )


@router.post("/reset")
def reset_playground():
    """Reset the monitor state (trace and log)."""
    state.monitor.reset()
    return {"status": "reset"}

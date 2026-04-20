"""Contract parsing and management endpoints."""

from fastapi import APIRouter, HTTPException

from api.schemas import (
    ConstraintItem,
    ContractCommitRequest,
    ContractParseRequest,
    ContractParseResponse,
    ContractResponse,
    ParsedConstraintResponse,
)
from api.state import state
from sponsio.generation.nl_to_contract import build_contracts, nl_to_contracts
from sponsio.models.agent import Agent
from sponsio.patterns.library import DetFormula

router = APIRouter()


@router.post("/parse", response_model=ContractParseResponse)
def parse_contracts(req: ContractParseRequest):
    """Parse NL text into constraints with live preview (no side effects)."""
    result = nl_to_contracts(req.nl_text)
    constraints = []
    for c in result.constraints:
        constraints.append(
            ParsedConstraintResponse(
                original_nl=c.original_nl,
                pattern_name=c.pattern_name,
                formula_repr=repr(c.formula.formula) if c.formula else "",
                ok=c.ok,
                error=c.error,
            )
        )
    return ContractParseResponse(constraints=constraints, ok=result.ok)


@router.post("")
def commit_contracts(req: ContractCommitRequest):
    """Commit NL constraints as contracts for an agent.

    Each parsed rule becomes one unconditional ``Contract``.
    Auto-creates the agent if it doesn't already exist so the Rulebook
    page works as a one-stop entry point.
    """
    auto_created = req.agent_id not in state.agents
    agent = state.agents.get(req.agent_id)
    if agent is None:
        agent = Agent(id=req.agent_id)
        state.agents[req.agent_id] = agent

    try:
        contracts = build_contracts(req.nl_text, agent)
    except ValueError as e:
        raise HTTPException(400, str(e))

    for c in contracts:
        state.system._contracts.append(c)
    state.rebuild_monitor()

    return {
        "agent_id": req.agent_id,
        "contracts_count": len(contracts),
        "agent_auto_created": auto_created,
    }


def _constraint_to_item(constraint) -> ConstraintItem:
    """Convert a constraint (det or sto) to an API response item."""
    if isinstance(constraint, DetFormula):
        return ConstraintItem(
            desc=constraint.desc,
            type="hard",
            pattern_name=constraint.pattern_name,
        )
    elif hasattr(constraint, "evaluator_fn"):
        return ConstraintItem(
            desc=getattr(constraint, "desc", str(constraint)),
            type="soft",
            pattern_name=getattr(constraint, "category", "soft"),
        )
    else:
        return ConstraintItem(
            desc=str(constraint),
            type="hard",
        )


@router.get("", response_model=list[ContractResponse])
def list_contracts():
    """List all contracts, one entry per ``Contract`` object.

    Since each ``Contract`` is now a single (A, E) pair, the response
    preserves that shape: one entry per contract, with ``assumptions``
    and ``guarantees`` each being a list view (for frontend compat with
    the previous list-of-lists shape).
    """
    contracts = state.system.contracts
    return [
        ContractResponse(
            agent_id=c.agent.id,
            assumptions=[_constraint_to_item(a) for a in c.assumptions],
            guarantees=[_constraint_to_item(e) for e in c.enforcements],
        )
        for c in contracts
    ]


@router.delete("/{agent_id}")
def delete_contracts_for_agent(agent_id: str):
    """Remove all contracts bound to the given agent."""
    before = len(state.system._contracts)
    state.system._contracts = [
        c for c in state.system._contracts if c.agent.id != agent_id
    ]
    removed = before - len(state.system._contracts)
    if removed == 0:
        raise HTTPException(404, f"No contracts found for agent '{agent_id}'.")
    state.rebuild_monitor()
    return {"deleted_agent_id": agent_id, "removed_count": removed}


@router.delete("/{agent_id}/{contract_index}")
def delete_contract(agent_id: str, contract_index: int):
    """Remove a single contract by its 0-based index within the agent.

    The index counts across all ``Contract`` objects owned by the agent,
    in the order they appear in ``state.system._contracts``. Since each
    Contract is one (A, E) pair, deleting it removes one rule cleanly.
    """
    if contract_index < 0:
        raise HTTPException(400, "contract_index must be non-negative")

    # Find the contract_index-th contract owned by this agent
    matches: list[int] = [
        i for i, c in enumerate(state.system._contracts) if c.agent.id == agent_id
    ]
    if not matches:
        raise HTTPException(404, f"No contracts found for agent '{agent_id}'.")
    if contract_index >= len(matches):
        raise HTTPException(
            404,
            f"Contract index {contract_index} out of range "
            f"(agent '{agent_id}' has {len(matches)} contracts).",
        )

    abs_idx = matches[contract_index]
    removed = state.system._contracts.pop(abs_idx)
    removed_desc = removed.desc or ", ".join(
        getattr(e, "desc", str(e)) for e in removed.enforcements
    )

    state.rebuild_monitor()
    return {
        "deleted_agent_id": agent_id,
        "deleted_index": contract_index,
        "deleted_desc": removed_desc,
    }

"""Scoring endpoint — accepts tool definitions, returns a safety report."""

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api import db
from sponsio.scoring import ScoringReport, ToolDef, badge_url, score_tools

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class ToolItem(BaseModel):
    name: str
    description: str = ""
    parameters: Dict[str, str] = {}


class ScoreRequest(BaseModel):
    agent_name: str = "anonymous"
    tools: List[ToolItem]
    display_name: Optional[str] = None
    description: Optional[str] = None
    email: Optional[str] = None
    framework: Optional[str] = None
    use_case: Optional[str] = None
    is_public: bool = False


class DeductionResponse(BaseModel):
    check_id: str
    points_lost: int
    description: str
    affected_tools: List[str]
    suggested_contract: str


class ScoreResponse(BaseModel):
    id: int
    score: int
    grade: str
    agent_name: str
    timestamp: str
    badge_url: str
    deductions: List[DeductionResponse]
    suggested_contracts: List[str]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("", response_model=ScoreResponse)
def create_score(req: ScoreRequest):
    """Score a set of tools for safety risks and persist the result."""
    if not req.tools:
        raise HTTPException(status_code=422, detail="tools list must not be empty")

    tool_defs = [
        ToolDef(name=t.name, description=t.description, parameters=t.parameters)
        for t in req.tools
    ]
    report: ScoringReport = score_tools(tool_defs, agent_name=req.agent_name)

    row_id = db.insert_score(
        agent_name=report.agent_name,
        score=report.score,
        grade=report.grade,
        timestamp=report.timestamp,
        details=report.to_dict(),
        display_name=req.display_name,
        description=req.description,
        email=req.email,
        framework=req.framework,
        use_case=req.use_case,
        is_public=req.is_public,
    )

    return ScoreResponse(
        id=row_id,
        score=report.score,
        grade=report.grade,
        agent_name=report.agent_name,
        timestamp=report.timestamp,
        badge_url=report.to_badge_url(),
        deductions=[DeductionResponse(**d.to_dict()) for d in report.deductions],
        suggested_contracts=report.suggested_contracts,
    )


@router.get("/{score_id}", response_model=ScoreResponse)
def get_score(score_id: int):
    """Retrieve a single scoring result by id."""
    row = db.get_score(score_id)
    if row is None:
        raise HTTPException(status_code=404, detail="score not found")
    details = row["details"]
    return ScoreResponse(
        id=row["id"],
        score=row["score"],
        grade=row["grade"],
        agent_name=row["agent_name"],
        timestamp=row["timestamp"],
        badge_url=badge_url(row["grade"], row["score"]),
        deductions=details.get("deductions", []),
        suggested_contracts=details.get("suggested_contracts", []),
    )


class ScoreListResponse(BaseModel):
    items: List[ScoreResponse]
    count: int


@router.get("", response_model=ScoreListResponse)
def list_scores(limit: int = 50, offset: int = 0):
    """List scoring results, newest first."""
    rows = db.list_scores(limit=limit, offset=offset)
    items = [
        ScoreResponse(
            id=r["id"],
            score=r["score"],
            grade=r["grade"],
            agent_name=r["agent_name"],
            timestamp=r["timestamp"],
            badge_url=badge_url(r["grade"], r["score"]),
            deductions=r["details"].get("deductions", []),
            suggested_contracts=r["details"].get("suggested_contracts", []),
        )
        for r in rows
    ]
    return ScoreListResponse(items=items, count=db.count_scores())

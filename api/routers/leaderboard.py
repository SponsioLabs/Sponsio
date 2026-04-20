"""Leaderboard endpoints — public ranking of agent safety scores."""

from typing import Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from api import db
from sponsio.scoring import badge_url

router = APIRouter()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class LeaderboardEntry(BaseModel):
    rank: int
    display_name: str
    description: Optional[str]
    score: int
    grade: str
    framework: Optional[str]
    timestamp: str
    badge_url: str


class LeaderboardResponse(BaseModel):
    entries: List[LeaderboardEntry]
    count: int


class TopAgent(BaseModel):
    display_name: str
    score: int
    grade: str


class StatsResponse(BaseModel):
    total_submissions: int
    public_entries: int
    average_score: float
    grade_distribution: Dict[str, int]
    framework_distribution: Dict[str, int]
    top_agent: Optional[TopAgent]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("", response_model=LeaderboardResponse)
def get_leaderboard(
    limit: int = 50,
    offset: int = 0,
    period: str = "all",
    framework: Optional[str] = None,
):
    """Public leaderboard — best score per agent, ranked by score DESC."""
    rows = db.leaderboard(
        limit=limit,
        offset=offset,
        period=period,
        framework=framework,
    )
    entries = [
        LeaderboardEntry(
            rank=offset + i + 1,
            display_name=r["display_name"],
            description=r.get("description"),
            score=r["score"],
            grade=r["grade"],
            framework=r.get("framework"),
            timestamp=r["timestamp"],
            badge_url=badge_url(r["grade"], r["score"]),
        )
        for i, r in enumerate(rows)
    ]
    return LeaderboardResponse(
        entries=entries,
        count=db.count_public_entries(period=period, framework=framework),
    )


@router.get("/stats", response_model=StatsResponse)
def get_stats():
    """Aggregate stats across all submissions."""
    s = db.leaderboard_stats()
    top = s["top_agent"]
    return StatsResponse(
        total_submissions=s["total_submissions"],
        public_entries=s["public_entries"],
        average_score=s["average_score"],
        grade_distribution=s["grade_distribution"],
        framework_distribution=s["framework_distribution"],
        top_agent=TopAgent(**top) if top else None,
    )

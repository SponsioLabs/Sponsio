"""Analytics endpoint — aggregate violation statistics from monitor log."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from api.state import state

router = APIRouter()


def _period_to_days(period: str) -> int:
    mapping = {"7d": 7, "30d": 30, "90d": 90}
    return mapping.get(period, 30)


@router.get("")
def get_analytics(period: str = "30d"):
    """Aggregate analytics from the in-memory monitor log.

    Returns data shaped for the frontend `AnalyticsData` type.
    """
    days = _period_to_days(period)
    now = datetime.now(timezone.utc)
    log = list(state.monitor.log)
    trace = state.monitor.trace

    # --- 1. Score history (one bucket per day) ---
    # Score = 100 - (violations / events * 100), clamped to [0, 100].
    # No historical persistence yet, so we bucket violations by day (if ts available)
    # and fill gaps with the most recent score.
    score_history = []
    event_count = max(len(trace.events), 1)
    hard_violations = sum(1 for e in log if e.pipeline == "hard")
    soft_violations = sum(1 for e in log if e.pipeline == "soft")
    total_violations = hard_violations + soft_violations
    current_score = max(0.0, min(100.0, 100.0 - (total_violations / event_count) * 100))
    for i in range(days):
        d = now - timedelta(days=days - 1 - i)
        score_history.append(
            {"date": d.date().isoformat(), "score": round(current_score, 1)}
        )

    # --- 2. Violations by pattern ---
    pattern_counter: Counter[str] = Counter()
    for e in log:
        name = e.constraint_name or "unknown"
        pattern_counter[name] += 1
    violations_by_pattern = [
        {"pattern": p, "count": c} for p, c in pattern_counter.most_common()
    ]

    # --- 3. Top violated contracts ---
    contract_counter: Counter[str] = Counter()
    last_seen: dict[str, str] = {}
    for e in log:
        nl = e.result.message or e.constraint_name or "unknown"
        contract_counter[nl] += 1
        last_seen[nl] = now.isoformat()
    top_violated = [
        {
            "nlText": nl,
            "count": count,
            "lastViolated": last_seen.get(nl, now.isoformat()),
        }
        for nl, count in contract_counter.most_common(10)
    ]

    # --- 4. Agent reliability ---
    events_by_agent: defaultdict[str, int] = defaultdict(int)
    violations_by_agent: defaultdict[str, int] = defaultdict(int)
    for ev in trace.events:
        events_by_agent[ev.agent] += 1
    for e in log:
        violations_by_agent[e.agent_id] += 1
    agent_reliability = []
    for agent_id, total in events_by_agent.items():
        v = violations_by_agent.get(agent_id, 0)
        reliability = max(0.0, min(100.0, 100.0 - (v / max(total, 1)) * 100))
        agent_reliability.append(
            {
                "agentId": agent_id,
                "reliability": round(reliability, 1),
                "totalEvents": total,
            }
        )

    return {
        "scoreHistory": score_history,
        "violationsByPattern": violations_by_pattern,
        "topViolatedContracts": top_violated,
        "agentReliability": agent_reliability,
    }

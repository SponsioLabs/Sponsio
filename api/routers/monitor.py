"""Monitor log and status endpoints."""

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from api.trace_adapter import trace_from_import_payload
from api.schemas import (
    AddContractRequest,
    MonitorEventResponse,
    MonitorStatusResponse,
    ReVerifyRequest,
    TraceEventPush,
    TraceEventResponse,
    TraceResponse,
)
from api.state import state

router = APIRouter()


# ─── Helpers: synthesize log/trace from externally pushed spans ──────────────


def _external_log_entries() -> list[MonitorEventResponse]:
    """Derive MonitorEventResponse entries from externally pushed span trees.

    This lets the /log and /status endpoints include data from agents that
    push span trees via /push-span (e.g. the walkthrough demo) rather than
    running inside the server's own monitor.
    """
    external: list[dict] = getattr(state, "_external_spans", []) or []
    entries: list[MonitorEventResponse] = []
    for span in external:
        agent_id = span.get("agent_id", "unknown")
        action = span.get("action", "?")
        blocked = span.get("blocked", False)

        for child in span.get("children", []):
            if child.get("span_type") != "sponsio.contract_check":
                continue
            status = child.get("status", "")
            contract_name = child.get("contract_name", "")

            # Determine pipeline from child structure or explicit pipeline field
            pipeline = child.get("pipeline", "hard")
            for gc in child.get("children", []):
                if gc.get("span_type") in (
                    "sponsio.sto_eval",
                    "sponsio.soft_eval",
                    "sponsio.soft_check",
                ):
                    pipeline = "soft"
                    break

            # Determine result action/message from enforcement or violation children
            result_action = "pass"
            result_message = ""
            if status == "violated":
                result_action = "block" if pipeline == "hard" else "retry"
                for gc in child.get("children", []):
                    if gc.get("span_type") == "sponsio.violation":
                        result_message = gc.get("evidence", "")
                    elif gc.get("span_type") == "sponsio.enforcement":
                        result_action = gc.get("result_action", result_action)
                        result_message = result_message or gc.get("message", "")
                    elif (
                        gc.get("span_type") == "sponsio.guarantee"
                        and gc.get("result") is False
                    ):
                        contract_name = contract_name or gc.get("formula_desc", "")

            entries.append(
                MonitorEventResponse(
                    agent_id=agent_id,
                    action=action,
                    pipeline=pipeline,
                    constraint_name=contract_name,
                    result_action=result_action,
                    result_message=result_message,
                )
            )

        # If span has no contract_check children but is blocked, emit a single entry
        if blocked and not any(
            c.get("span_type") == "sponsio.contract_check"
            for c in span.get("children", [])
        ):
            entries.append(
                MonitorEventResponse(
                    agent_id=agent_id,
                    action=action,
                    pipeline="hard",
                    constraint_name="",
                    result_action="block",
                    result_message=span.get("verdict", "blocked"),
                )
            )

    return entries


def _external_trace_events() -> list[TraceEventResponse]:
    """Derive TraceEventResponse entries from externally pushed span trees."""
    external: list[dict] = getattr(state, "_external_spans", []) or []
    events: list[TraceEventResponse] = []
    for idx, span in enumerate(external):
        agent = span.get("agent_id", "unknown")
        action = span.get("action", "?")
        verdict = span.get("verdict", "pass")
        events.append(
            TraceEventResponse(
                ts=idx,
                agent=agent,
                event_type="tool_call",
                tool=action,
                key=f"{agent}.{action}",
                to=None,
                content=verdict,
            )
        )
    return events


# ─── Endpoints ───────────────────────────────────────────────────────────────


@router.get("/log", response_model=list[MonitorEventResponse])
def get_log():
    own = [
        MonitorEventResponse(
            agent_id=e.agent_id,
            action=e.action,
            pipeline=e.pipeline,
            constraint_name=e.constraint_name,
            result_action=e.result.action,
            result_message=e.result.message,
        )
        for e in state.monitor.log
    ]
    return own + _external_log_entries()


@router.get("/status", response_model=MonitorStatusResponse)
def get_status():
    own_log = state.monitor.log
    ext_log = _external_log_entries()
    total = len(own_log) + len(ext_log)
    # Count only actual violations (not passes) — matches frontend MetricCards logic
    det = sum(
        1 for e in own_log if e.pipeline == "hard" and e.result.action != "pass"
    ) + sum(1 for e in ext_log if e.pipeline == "hard" and e.result_action != "pass")
    sto = sum(
        1 for e in own_log if e.pipeline == "soft" and e.result.action != "pass"
    ) + sum(1 for e in ext_log if e.pipeline == "soft" and e.result_action != "pass")
    return MonitorStatusResponse(
        total_events=total,
        det_violations=det,
        sto_violations=sto,
    )


@router.get("/trace", response_model=TraceResponse)
def get_trace():
    own = [
        TraceEventResponse(
            ts=e.ts,
            agent=e.agent,
            event_type=e.event_type,
            tool=e.tool,
            key=e.key,
            to=e.to,
            content=e.content,
        )
        for e in state.monitor.trace.events
    ]
    return TraceResponse(events=own + _external_trace_events())


@router.post("/push")
def push_event(event: TraceEventPush):
    from sponsio.models.trace import Event

    new_event = Event(
        ts=event.ts if event.ts is not None else len(state.monitor.trace.events),
        agent=event.agent,
        event_type=event.type,
        tool=event.tool,
        key=event.key,
        to=event.to,
        content=event.content,
    )
    state.monitor.trace.events.append(new_event)
    return {"status": "received", "event_index": len(state.monitor.trace.events) - 1}


@router.post("/import")
def import_trace(data: dict):
    trace = trace_from_import_payload(data)
    state.monitor.import_trace(trace)
    return {"status": "imported", "event_count": len(trace.events)}


@router.post("/re-verify")
def re_verify(req: ReVerifyRequest):
    from sponsio.formulas.evaluator import evaluate
    from sponsio.generation.nl_to_contract import parse_nl_unified
    from sponsio.tracer.grounding import ground

    parsed = parse_nl_unified(req.nl_text)
    if not parsed.is_det:
        raise HTTPException(422, "Only det constraints supported for re-verify")
    valuations = ground(state.monitor.trace, state.agents)
    results = []
    for i in range(len(valuations)):
        passed = evaluate(parsed.hard.formula, valuations[: i + 1])
        ev = (
            state.monitor.trace.events[i]
            if i < len(state.monitor.trace.events)
            else None
        )
        results.append(
            {
                "timestep": i,
                "passed": passed,
                "event_summary": f"{ev.agent}: {ev.tool or ev.event_type}"
                if ev
                else "",
            }
        )
    return {
        "contract_desc": parsed.hard.desc,
        "pattern_name": "",
        "results": results,
        "overall_passed": all(r["passed"] for r in results),
    }


@router.get("/stream")
async def stream_monitor():
    """Server-Sent Events stream of monitor updates.

    Emits a JSON frame whenever the log length, trace length, or span count
    changes. Polls in-process state at 500ms granularity; no external queue.
    Each frame carries the current status + the new log entries since last frame.
    """

    async def event_source():
        last_log_len = 0
        last_trace_len = 0
        last_span_count = 0
        # Send initial state
        while True:
            try:
                log = state.monitor.log
                trace_events = state.monitor.trace.events
                own_spans = len(state.monitor.turn_spans)
                ext_spans = getattr(state, "_external_spans", []) or []
                span_count = own_spans + len(ext_spans)

                # Include external span-derived entries in counts
                ext_log = _external_log_entries()
                log_len = len(log) + len(ext_log)
                trace_len = len(trace_events) + len(ext_spans)

                if (
                    log_len != last_log_len
                    or trace_len != last_trace_len
                    or span_count != last_span_count
                ):
                    new_events = [
                        {
                            "agent_id": e.agent_id,
                            "action": e.action,
                            "pipeline": e.pipeline,
                            "constraint_name": e.constraint_name,
                            "result_action": e.result.action,
                            "result_message": e.result.message,
                        }
                        for e in log[max(0, last_log_len - len(ext_log)) :]
                    ]
                    # Also include new external entries
                    new_events += [
                        {
                            "agent_id": e.agent_id,
                            "action": e.action,
                            "pipeline": e.pipeline,
                            "constraint_name": e.constraint_name,
                            "result_action": e.result_action,
                            "result_message": e.result_message,
                        }
                        for e in ext_log
                    ]

                    all_hard = sum(1 for e in log if e.pipeline == "hard") + sum(
                        1 for e in ext_log if e.pipeline == "hard"
                    )
                    all_soft = sum(1 for e in log if e.pipeline == "soft") + sum(
                        1 for e in ext_log if e.pipeline == "soft"
                    )
                    frame = {
                        "status": {
                            "total_events": log_len,
                            "det_violations": all_hard,
                            "sto_violations": all_soft,
                        },
                        "trace_len": trace_len,
                        "span_count": span_count,
                        "new_events": new_events,
                    }
                    yield f"data: {json.dumps(frame)}\n\n"
                    last_log_len = log_len
                    last_trace_len = trace_len
                    last_span_count = span_count
                else:
                    # Keep-alive comment so proxies don't close idle connection
                    yield ": ping\n\n"

                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                break

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/reset")
def reset_trace():
    """Clear trace events and spans but keep contracts and agents intact.

    This is the safe reset called by the Monitor page's "Reset" button.
    Contracts configured in Rulebook/Integrate are preserved so the user
    doesn't lose their pipeline state when clearing monitor data.
    """
    state.clear_events()
    return {"status": "reset"}


@router.post("/reset-all")
def reset_all():
    """Full reset: clears everything including contracts and agents."""
    state.reset()
    return {"status": "reset"}


@router.get("/spans")
def get_spans():
    """Return structured span trees from the current session."""
    # Combine monitor's own spans + externally pushed spans
    own = [span.to_dict() for span in state.monitor.turn_spans]
    external = getattr(state, "_external_spans", [])
    return own + external


@router.post("/push-span")
def push_span(span: dict):
    """Receive a span tree from an external agent (via monitor_graph with contracts)."""
    if not span or not isinstance(span, dict):
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="Expected a non-empty span dict")

    if not hasattr(state, "_external_spans"):
        state._external_spans = []
    state._external_spans.append(span)

    # Bridge: also store in the OTEL trace store for unified trace view
    try:
        from api.routers.otel_ingest import trace_store

        trace_store.ingest_sponsio_span(span)
    except Exception:
        pass  # Don't fail the push if trace store has issues

    return {"status": "received", "span_count": len(state._external_spans)}


@router.get("/report")
def get_report():
    """Structured JSON report: tools, contracts, violations, fixes, risk score."""
    trace = state.monitor.trace
    own_spans = [span.to_dict() for span in state.monitor.turn_spans]
    external_spans = getattr(state, "_external_spans", [])
    all_spans = own_spans + external_spans

    # 1. Tool list — unique tools seen in trace
    tools = list(dict.fromkeys(e.tool for e in trace.events if e.tool))

    # 2. Discovered contracts — from active system + external span data
    contracts_discovered = []
    seen_descs = set()
    for contract in state.system.contracts:
        for e in contract.enforcements:
            desc = getattr(e, "desc", str(e))
            pattern = getattr(e, "pattern_name", "")
            if desc not in seen_descs:
                seen_descs.add(desc)
                contracts_discovered.append(
                    {
                        "description": desc,
                        "pattern": pattern,
                        "type": "det",
                    }
                )
    # Also extract from external spans (contract info in children)
    for span in all_spans:
        for child in span.get("children", []):
            if child.get("span_type") == "sponsio.contract_check":
                # Dig into guarantee children for the actual NL desc
                for gc in child.get("children", []):
                    if gc.get("span_type") == "sponsio.guarantee" and gc.get(
                        "formula_desc"
                    ):
                        desc = gc["formula_desc"]
                        if desc not in seen_descs:
                            seen_descs.add(desc)
                            contracts_discovered.append(
                                {
                                    "description": desc,
                                    "pattern": "",
                                    "type": "det",
                                }
                            )

    # 3. Violation paths — from spans (check root.blocked OR any child violated)
    violation_paths = []
    for span in all_spans:
        blocked = span.get("blocked", False)
        if not blocked:
            # Also check if any child contract_check is violated
            for child in span.get("children", []):
                if (
                    child.get("span_type") == "sponsio.contract_check"
                    and child.get("status") == "violated"
                ):
                    blocked = True
                    break
        if not blocked:
            continue
        action = span.get("action", "?")
        # Extract violated contract from children
        violated_contract = ""
        evidence = ""
        strategy = ""
        for child in span.get("children", []):
            if (
                child.get("span_type") == "sponsio.contract_check"
                and child.get("status") == "violated"
            ):
                violated_contract = child.get("contract_name", "")
                for gc in child.get("children", []):
                    if (
                        gc.get("span_type") == "sponsio.guarantee"
                        and gc.get("result") is False
                    ):
                        violated_contract = gc.get("formula_desc", violated_contract)
                    if gc.get("span_type") == "sponsio.violation":
                        evidence = gc.get("evidence", "")
                    if gc.get("span_type") == "sponsio.enforcement":
                        strategy = gc.get("result_action", "")
        violation_paths.append(
            {
                "action": action,
                "contract_violated": violated_contract,
                "evidence": evidence,
                "enforcement": strategy or "blocked",
            }
        )

    # 4. Suggested fixes
    suggested_fixes = []
    for v in violation_paths:
        if "must precede" in v["contract_violated"]:
            parts = v["contract_violated"].split("must precede")
            if len(parts) == 2:
                pre = parts[0].strip().strip("`").replace("tool ", "")
                post = parts[1].strip().strip("`")
                suggested_fixes.append(
                    {
                        "action": v["action"],
                        "fix": f"Call {pre} before {post}",
                    }
                )
            else:
                suggested_fixes.append(
                    {"action": v["action"], "fix": f"Satisfy: {v['contract_violated']}"}
                )
        elif (
            "at most" in v["contract_violated"] or "more than" in v["contract_violated"]
        ):
            suggested_fixes.append(
                {
                    "action": v["action"],
                    "fix": f"Reduce call frequency — {v['contract_violated']}",
                }
            )
        else:
            suggested_fixes.append(
                {"action": v["action"], "fix": f"Satisfy: {v['contract_violated']}"}
            )

    # 5. Risk score
    total_contracts = len(contracts_discovered) if contracts_discovered else 1
    violation_count = len(violation_paths)
    risk_score = (
        round(violation_count / total_contracts, 2) if total_contracts > 0 else 0.0
    )

    return {
        "tools": tools,
        "contracts_discovered": contracts_discovered,
        "violation_paths": violation_paths,
        "suggested_fixes": suggested_fixes,
        "risk_score": risk_score,
        "summary": {
            "total_events": len(trace.events),
            "total_contracts": total_contracts,
            "total_violations": violation_count,
            "total_spans": len(all_spans),
        },
    }


@router.post("/add-contract")
def add_contract(req: AddContractRequest):
    """Add a new contract to the active system for future enforcement."""
    from sponsio.generation.nl_to_contract import parse_nl_unified
    from sponsio.models.agent import Agent
    from sponsio.models.contract import Contract

    parsed = parse_nl_unified(req.nl_text)
    if not parsed.is_det and not parsed.is_sto:
        raise HTTPException(422, "Could not parse contract")

    enforcement = parsed.hard if parsed.is_det else parsed.sto
    desc = getattr(enforcement, "desc", str(enforcement))

    agent = Agent(id="default")
    contract = Contract(agent=agent, enforcement=enforcement)
    state.system._contracts.append(contract)
    state.rebuild_monitor()
    return {"status": "added", "contract_desc": desc}

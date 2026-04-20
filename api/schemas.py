"""Pydantic request/response models for the API."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# --- Agents ---


class AgentCreate(BaseModel):
    id: str
    tools: list[str] = []
    permissions: list[str] = []
    reads_from: list[str] = []
    writes_to: list[str] = []


class AgentResponse(BaseModel):
    id: str
    tools: list[str]
    permissions: list[str]
    reads_from: list[str]
    writes_to: list[str]


# --- Contracts ---


class ContractParseRequest(BaseModel):
    nl_text: str = Field(..., min_length=1)


class ParsedConstraintResponse(BaseModel):
    original_nl: str
    pattern_name: str
    formula_repr: str
    ok: bool
    error: str


class ContractParseResponse(BaseModel):
    constraints: list[ParsedConstraintResponse]
    ok: bool


class ContractCommitRequest(BaseModel):
    agent_id: str
    nl_text: str


class ConstraintItem(BaseModel):
    desc: str
    type: str = "hard"  # "hard" or "soft"
    pattern_name: str = ""


class ContractResponse(BaseModel):
    agent_id: str
    assumptions: list[ConstraintItem] = []
    guarantees: list[ConstraintItem] = []


# --- Playground ---


class PlaygroundActionRequest(BaseModel):
    agent_id: str = Field(..., min_length=1)
    action: str = Field(..., min_length=1)
    event_type: str = "tool_call"
    metadata: Optional[dict] = None


class EnforcementResultResponse(BaseModel):
    action: str
    message: str
    retry_prompt: Optional[str] = None


class PlaygroundActionResponse(BaseModel):
    allowed: bool
    results: list[EnforcementResultResponse]


# --- Monitor ---


class MonitorEventResponse(BaseModel):
    agent_id: str
    action: str
    pipeline: str
    constraint_name: str
    result_action: str
    result_message: str


class MonitorStatusResponse(BaseModel):
    total_events: int
    det_violations: int
    sto_violations: int


# --- Trace ---


class TraceEventResponse(BaseModel):
    ts: int
    agent: str
    event_type: str
    tool: Optional[str] = None
    key: Optional[str] = None
    to: Optional[str] = None
    content: Optional[str] = None


class TraceResponse(BaseModel):
    events: list[TraceEventResponse]


# --- System ---


class SystemResponse(BaseModel):
    name: str
    agent_count: int
    contract_count: int
    violation_count: int


# --- Push / Import / Re-verify ---


class TraceEventPush(BaseModel):
    ts: Optional[int] = None
    agent: str
    type: str
    tool: Optional[str] = None
    key: Optional[str] = None
    to: Optional[str] = None
    content: Optional[str] = None


class TraceImportEvent(BaseModel):
    ts: int
    agent: str
    type: str
    tool: Optional[str] = None
    key: Optional[str] = None
    to: Optional[str] = None
    content: Optional[str] = None


class TraceImportRequest(BaseModel):
    events: list[TraceImportEvent]
    metadata: Optional[dict] = None


class ReVerifyRequest(BaseModel):
    nl_text: str


class ReVerifyStepResult(BaseModel):
    timestep: int
    passed: bool
    event_summary: str


class ReVerifyResponse(BaseModel):
    contract_desc: str
    pattern_name: str
    results: list[ReVerifyStepResult]
    overall_passed: bool


class AddContractRequest(BaseModel):
    nl_text: str

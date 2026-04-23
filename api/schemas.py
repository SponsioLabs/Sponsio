"""Pydantic request/response models for the API.

Length limits on free-form string fields (``MAX_NL_LEN``,
``MAX_AGENT_ID_LEN``, etc.) cap the request bodies the public API
accepts. Without these caps a single ``POST /api/contracts/parse`` with
a multi-megabyte ``nl_text`` could exhaust memory in the regex-heavy
NL parser (ReDoS surface) or block the server's event loop. Bumping
``MAX_NL_LEN`` is fine if a real contract grows beyond it; the
constant is the single knob.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

MAX_NL_LEN = 4_000  # ~600 words; well above any real single-rule contract
MAX_TOOL_NAME_LEN = 200
MAX_AGENT_ID_LEN = 200
MAX_CONTENT_LEN = 50_000  # tool output / llm_response content per event
MAX_EVENT_TYPE_LEN = 64
MAX_EVENTS_PER_IMPORT = 10_000  # request-level cap on bulk imports


# --- Agents ---


class AgentCreate(BaseModel):
    id: str = Field(..., min_length=1, max_length=MAX_AGENT_ID_LEN)
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
    nl_text: str = Field(..., min_length=1, max_length=MAX_NL_LEN)


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
    agent_id: str = Field(..., min_length=1, max_length=MAX_AGENT_ID_LEN)
    nl_text: str = Field(..., min_length=1, max_length=MAX_NL_LEN)


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
    agent_id: str = Field(..., min_length=1, max_length=MAX_AGENT_ID_LEN)
    action: str = Field(..., min_length=1, max_length=MAX_TOOL_NAME_LEN)
    event_type: str = Field("tool_call", max_length=MAX_EVENT_TYPE_LEN)
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
    agent: str = Field(..., min_length=1, max_length=MAX_AGENT_ID_LEN)
    type: str = Field(..., min_length=1, max_length=MAX_EVENT_TYPE_LEN)
    tool: Optional[str] = Field(default=None, max_length=MAX_TOOL_NAME_LEN)
    key: Optional[str] = Field(default=None, max_length=MAX_TOOL_NAME_LEN)
    to: Optional[str] = Field(default=None, max_length=MAX_AGENT_ID_LEN)
    content: Optional[str] = Field(default=None, max_length=MAX_CONTENT_LEN)


class TraceImportEvent(BaseModel):
    ts: int
    agent: str = Field(..., min_length=1, max_length=MAX_AGENT_ID_LEN)
    type: str = Field(..., min_length=1, max_length=MAX_EVENT_TYPE_LEN)
    tool: Optional[str] = Field(default=None, max_length=MAX_TOOL_NAME_LEN)
    key: Optional[str] = Field(default=None, max_length=MAX_TOOL_NAME_LEN)
    to: Optional[str] = Field(default=None, max_length=MAX_AGENT_ID_LEN)
    content: Optional[str] = Field(default=None, max_length=MAX_CONTENT_LEN)


class TraceImportRequest(BaseModel):
    events: list[TraceImportEvent] = Field(..., max_length=MAX_EVENTS_PER_IMPORT)
    metadata: Optional[dict] = None


class ReVerifyRequest(BaseModel):
    nl_text: str = Field(..., min_length=1, max_length=MAX_NL_LEN)


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
    nl_text: str = Field(..., min_length=1, max_length=MAX_NL_LEN)

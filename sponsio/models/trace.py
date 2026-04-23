"""Trace and Event dataclasses."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


# Accepted ``event_type`` discriminator values. Keep in sync with the
# grounding layer — new types must be handled by ``ground_event`` before
# being added here, otherwise contracts will silently miss them.
_VALID_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "tool_call",
        "data_read",
        "data_write",
        "message",
        "delegation",
        "llm_response",
        "llm_request",
    }
)


@dataclass
class Event:
    """A single event in an execution trace.

    Attributes:
        ts: Monotonically increasing timestamp (logical clock).
        agent: Identifier of the agent that produced this event.
        event_type: One of ``"tool_call"``, ``"data_read"``,
            ``"data_write"``, or ``"message"``.
        tool: Tool name (set when ``event_type == "tool_call"``).
        key: Data store key (set for ``"data_read"``/``"data_write"``).
        contains: Field names present in a data write payload.
        to: Target agent id (set for ``"message"`` events).
        args: Arbitrary keyword arguments passed to a tool call.
        content: Free-text content of a message event.
    """

    ts: int
    agent: str
    event_type: str  # "tool_call", "data_read", "data_write", "message"
    tool: str | None = None
    key: str | None = None
    contains: list[str] | None = None
    to: str | None = None
    args: dict | None = None
    content: str | None = None

    def __post_init__(self) -> None:
        """Data-integrity checks (#16).

        Negative / non-integer ``ts`` breaks monotonicity assumptions in
        the evaluator (the incremental backend compares timestamps to
        decide recency). Empty ``agent`` silently disables
        ``current_agent``/``segregation_of_duty`` — i.e. every contract
        becomes vacuously True on that event. And an unknown
        ``event_type`` flows through the dispatch in ``ground_event``
        and simply produces no atoms, turning the event into a no-op —
        worst of all possible failure modes for a security trace.
        """
        # We accept both ``int`` (logical clock) and ``float`` (wall-clock
        # seconds — session-log loader uses monotonic() timestamps) but
        # reject ``bool`` (which is ``int`` in Python) since it's always
        # a caller bug to pass True/False in here.
        if isinstance(self.ts, bool) or not isinstance(self.ts, (int, float)):
            raise TypeError(
                f"Event.ts must be int or float (got {type(self.ts).__name__}={self.ts!r})"
            )
        if self.ts < 0:
            raise ValueError(
                f"Event.ts must be >= 0 (got {self.ts}). Negative timestamps "
                "break trace ordering in the incremental evaluator."
            )
        if not isinstance(self.agent, str) or not self.agent:
            raise ValueError(
                f"Event.agent must be a non-empty string (got {self.agent!r}). "
                "An empty agent id collapses all per-agent atoms."
            )
        if not isinstance(self.event_type, str) or not self.event_type:
            raise ValueError(
                f"Event.event_type must be a non-empty string (got {self.event_type!r})."
            )
        if self.event_type not in _VALID_EVENT_TYPES:
            raise ValueError(
                f"Event.event_type={self.event_type!r} is not recognized. "
                f"Valid values: {sorted(_VALID_EVENT_TYPES)}. Unknown types "
                "produce no atoms and silently disable every contract that "
                "depends on the event."
            )

    def __repr__(self) -> str:
        parts = [f"ts={self.ts}", f"agent={self.agent!r}", f"type={self.event_type!r}"]
        if self.tool:
            parts.append(f"tool={self.tool!r}")
        if self.key:
            parts.append(f"key={self.key!r}")
        if self.to:
            parts.append(f"to={self.to!r}")
        return f"Event({', '.join(parts)})"


@dataclass
class Trace:
    """An execution trace — an ordered sequence of events.

    Attributes:
        events: Chronologically ordered list of ``Event`` objects.
        metadata: Optional dictionary of trace-level metadata.
    """

    events: list[Event] = field(default_factory=list)
    metadata: dict | None = None

    def __len__(self) -> int:
        return len(self.events)

    def to_dict(self) -> dict:
        """Serializes the trace to a JSON-compatible dictionary.

        Returns:
            A dict with ``"metadata"`` and ``"events"`` keys.
        """
        events = []
        for e in self.events:
            d: dict = {"ts": e.ts, "agent": e.agent, "type": e.event_type}
            if e.tool is not None:
                d["tool"] = e.tool
            if e.key is not None:
                d["key"] = e.key
            if e.contains is not None:
                d["contains"] = e.contains
            if e.to is not None:
                d["to"] = e.to
            if e.args is not None:
                d["args"] = e.args
            if e.content is not None:
                d["content"] = e.content
            events.append(d)
        return {"metadata": self.metadata or {}, "events": events}

    def to_json(self, indent: int = 2) -> str:
        """Serializes the trace to a JSON string.

        Args:
            indent: Number of spaces for JSON indentation.

        Returns:
            A JSON-formatted string.
        """
        return json.dumps(self.to_dict(), indent=indent)

    def export(self, path: str | Path) -> None:
        """Writes the trace to a JSON file.

        Args:
            path: Destination file path.
        """
        Path(path).write_text(self.to_json())

    @classmethod
    def from_dict(cls, data: dict) -> Trace:
        """Deserializes a trace from a dictionary.

        Args:
            data: A dict with ``"events"`` and optional ``"metadata"`` keys.

        Returns:
            A new ``Trace`` instance.
        """
        events = []
        for e in data.get("events", []):
            events.append(
                Event(
                    ts=e["ts"],
                    agent=e["agent"],
                    event_type=e["type"],
                    tool=e.get("tool"),
                    key=e.get("key"),
                    contains=e.get("contains"),
                    to=e.get("to"),
                    args=e.get("args"),
                    content=e.get("content"),
                )
            )
        return cls(events=events, metadata=data.get("metadata"))

    @classmethod
    def load(cls, path: str | Path) -> Trace:
        """Loads a trace from a JSON file.

        Args:
            path: Path to the JSON file.

        Returns:
            A new ``Trace`` instance.
        """
        data = json.loads(Path(path).read_text())
        return cls.from_dict(data)

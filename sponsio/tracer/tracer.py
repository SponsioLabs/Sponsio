"""Tracer class — framework-agnostic trace collection."""

from __future__ import annotations

from pathlib import Path

from sponsio.models.trace import Event, Trace


class Tracer:
    """Framework-agnostic trace collector.

    Users manually instrument their agent code with calls like::

        tracer.tool_call("agent_id", "tool_name", args={...})
        tracer.data_write("agent_id", key="cache", contains=["field"])

    Then call ``build()`` to obtain a ``Trace`` for verification.
    """

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._ts: int = 0

    def _next_ts(self) -> int:
        """Returns the next monotonic timestamp and increments the counter."""
        ts = self._ts
        self._ts += 1
        return ts

    def tool_call(self, agent: str, tool: str, *, args: dict | None = None) -> None:
        """Records a tool-call event.

        Args:
            agent: Identifier of the calling agent.
            tool: Name of the tool invoked.
            args: Optional keyword arguments passed to the tool.
        """
        self._events.append(
            Event(
                ts=self._next_ts(),
                agent=agent,
                event_type="tool_call",
                tool=tool,
                args=args,
            )
        )

    def data_write(
        self, agent: str, *, key: str, contains: list[str] | None = None
    ) -> None:
        """Records a data-write event.

        Args:
            agent: Identifier of the writing agent.
            key: Data store key being written to.
            contains: Optional list of field names in the write payload.
        """
        self._events.append(
            Event(
                ts=self._next_ts(),
                agent=agent,
                event_type="data_write",
                key=key,
                contains=contains,
            )
        )

    def data_read(self, agent: str, *, key: str) -> None:
        """Records a data-read event.

        Args:
            agent: Identifier of the reading agent.
            key: Data store key being read from.
        """
        self._events.append(
            Event(
                ts=self._next_ts(),
                agent=agent,
                event_type="data_read",
                key=key,
            )
        )

    def message(self, agent: str, *, to: str, content: str = "") -> None:
        """Records an inter-agent message event.

        Args:
            agent: Identifier of the sending agent.
            to: Identifier of the receiving agent.
            content: Optional message body.
        """
        self._events.append(
            Event(
                ts=self._next_ts(),
                agent=agent,
                event_type="message",
                to=to,
                content=content,
            )
        )

    def build(self) -> Trace:
        """Builds and returns the collected trace.

        Returns:
            A ``Trace`` containing copies of all recorded events.
        """
        return Trace(events=list(self._events))

    def export(self, path: str | Path) -> None:
        """Exports the collected trace to a JSON file.

        Args:
            path: Destination file path.
        """
        self.build().export(path)

    @staticmethod
    def load(path: str | Path) -> Trace:
        """Loads a trace from a JSON file.

        Args:
            path: Path to the JSON file.

        Returns:
            A ``Trace`` instance.
        """
        return Trace.load(path)

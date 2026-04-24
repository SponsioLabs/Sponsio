"""Unit tests for sponsio/integrations/google_adk.py."""

from __future__ import annotations

import asyncio
import inspect

from sponsio.integrations.google_adk import GoogleADKGuard


def test_check_tool_call_blocks_out_of_order_call():
    guard = GoogleADKGuard(
        contracts=["tool `search_flights` must precede `book_flight`"],
        mode="enforce",
        verbose=False,
    )

    result = guard.check_tool_call("book_flight", {"flight_id": "AA100"})

    assert result.blocked is True
    assert guard.last_check is result


def test_wrap_tool_returns_adk_friendly_error_dict_when_blocked():
    guard = GoogleADKGuard(
        contracts=["tool `search_flights` must precede `book_flight`"],
        mode="enforce",
        verbose=False,
    )

    def book_flight(flight_id: str) -> dict:
        """Book a flight."""
        return {"status": "success", "confirmation": flight_id}

    wrapped = guard.wrap_tool(book_flight)
    result = wrapped("AA100")

    assert result["status"] == "error"
    assert "BLOCKED by contract" in result["error_message"]
    assert wrapped.__name__ == "book_flight"
    assert inspect.signature(wrapped) == inspect.signature(book_flight)


def test_wrap_allows_correct_sequence():
    guard = GoogleADKGuard(
        contracts=["tool `search_flights` must precede `book_flight`"],
        mode="enforce",
        verbose=False,
    )

    def search_flights(to: str) -> dict:
        """Search flights."""
        return {"status": "success", "to": to}

    def book_flight(flight_id: str) -> dict:
        """Book a flight."""
        return {"status": "success", "confirmation": flight_id}

    search, book = guard.wrap([search_flights, book_flight])

    assert search(to="JFK") == {"status": "success", "to": "JFK"}
    assert book(flight_id="AA100") == {
        "status": "success",
        "confirmation": "AA100",
    }


def test_wrap_tool_supports_async_functions():
    guard = GoogleADKGuard(
        contracts=["tool `lookup` must precede `delete_record`"],
        mode="enforce",
        verbose=False,
    )

    async def delete_record(record_id: str) -> dict:
        """Delete a record."""
        return {"status": "success", "record_id": record_id}

    wrapped = guard.wrap_tool(delete_record)
    result = asyncio.run(wrapped("rec-1"))

    assert result["status"] == "error"
    assert "BLOCKED by contract" in result["error_message"]

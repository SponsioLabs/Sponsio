"""Google ADK Guard - Travel Booking

Shows how to add Sponsio to Google ADK Python - wrap tools before
passing them to Agent(tools=[...]).

Usage:
    python examples/integrations/python/google_adk_guard.py
    USE_MOCK=0 GOOGLE_API_KEY=... python examples/integrations/python/google_adk_guard.py
"""

from __future__ import annotations

import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from shared import USE_MOCK  # noqa: E402
from sponsio import contract  # noqa: E402

CONTRACTS = [
    contract("must search before booking")
    .assume("called `book_flight`")
    .enforce("must call `search_flights` before `book_flight`"),
    contract("charge cap").enforce("tool `charge_payment` at most 1 times"),
]


def search_flights(origin: str, destination: str) -> dict:
    """Search available flights between two cities."""
    return {"status": "success", "report": f"Found {origin} to {destination} from $320"}


def book_flight(flight_id: str) -> dict:
    """Book a flight by id."""
    return {"status": "success", "confirmation": f"Booked {flight_id}"}


def charge_payment(amount: int) -> dict:
    """Charge the traveler."""
    return {"status": "success", "receipt": f"Charged ${amount}"}


TOOLS = {
    "search_flights": search_flights,
    "book_flight": book_flight,
    "charge_payment": charge_payment,
}


def run_mock(guard):
    search, book, charge = guard.wrap([search_flights, book_flight, charge_payment])
    script = [
        (book, {"flight_id": "AA100"}),
        (search, {"origin": "SFO", "destination": "JFK"}),
        (book, {"flight_id": "AA100"}),
        (charge, {"amount": 320}),
        (charge, {"amount": 10}),
    ]
    for fn, args in script:
        result = fn(**args)
        status = "BLOCKED" if result.get("status") == "error" else "OK"
        print(f"  [{status}] {fn.__name__}: {result}")


def run_real(guard):
    if not os.environ.get("GOOGLE_API_KEY"):
        print("ERROR: Set GOOGLE_API_KEY for real mode.")
        sys.exit(1)

    try:
        from google.adk.agents.llm_agent import Agent
    except ImportError:
        print("ERROR: pip install google-adk for real mode.")
        sys.exit(1)

    root_agent = Agent(
        name="travel_agent",
        model="gemini-flash-latest",
        instruction="Search before booking. Charge only once.",
        tools=guard.wrap([search_flights, book_flight, charge_payment]),
    )
    print(f"  Created ADK agent with Sponsio-guarded tools: {root_agent.name}")
    print("  Run with: adk run <your_agent_module>")


def main():
    # ======== Add Sponsio: 2 lines ========
    from sponsio.google_adk import Sponsio

    guard = Sponsio(agent_id="travel_agent", contracts=CONTRACTS, mode="enforce")
    # ======================================

    if USE_MOCK:
        run_mock(guard)
    else:
        run_real(guard)

    print()
    guard.print_summary()


if __name__ == "__main__":
    main()

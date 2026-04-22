"""MCP Guard — Data Pipeline Agent

Scenario: MCP-based agent with database reads and external API writes.
Contracts enforce that database must be read before writing to external API,
and email sends are rate-limited to 2 per session.

Usage:
    python examples/integrations/mcp_guard.py                            # Mock mode
    USE_MOCK=0 GOOGLE_API_KEY=... python examples/integrations/mcp_guard.py

Note: MCP is an async protocol. In mock mode, we use sponsio.Sponsio() with
guard_before/guard_after (same as all other integrations). In real mode,
MCPContractProxy wraps the MCP client for transparent enforcement.
"""

from __future__ import annotations

import asyncio
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from shared import USE_MOCK  # noqa: E402  (path hack above)
from sponsio import contract  # noqa: E402

CONTRACTS = [
    # Conditional (A, E) pair — assumption triggers the enforcement
    contract("read DB before writing to external API")
    .assume("called `write_external_api`")
    .enforce("must call `read_database` before `write_external_api`"),
    # Unconditional rate limit — no .assume(), only .enforce()
    contract("email rate limit").enforce("tool `send_email` at most 2 times"),
]


# -- Mock MCP tools (plain functions) -----------------------------------------


def read_database(table: str) -> dict:
    return {"rows": 42, "table": table}


def write_external_api(data: str) -> dict:
    return {"status": "ok", "data": data}


def send_email(to: str) -> dict:
    return {"sent": True, "to": to}


TOOLS = {
    "read_database": read_database,
    "write_external_api": write_external_api,
    "send_email": send_email,
}


# -- Mock mode: sponsio.Sponsio() + guard_before/after ------------------------


def run_mock():
    import sponsio

    # ======== Add Sponsio: 3 lines ========
    guard = sponsio.Sponsio(
        agent_id="mcp_agent",
        contracts=CONTRACTS,
    )
    # =======================================

    calls = [
        (
            "write_external_api",
            {"data": "batch_1"},
        ),  # blocked: read_database not called
        ("read_database", {"table": "customers"}),  # allowed
        ("write_external_api", {"data": "batch_1"}),  # allowed
        ("send_email", {"to": "alice@corp.com"}),  # allowed (1/2)
        ("send_email", {"to": "bob@corp.com"}),  # allowed (2/2)
        ("send_email", {"to": "carol@corp.com"}),  # blocked: rate limit
    ]

    for tool_name, args in calls:
        result = guard.guard_before(tool_name, args)
        if not result.blocked:
            output = TOOLS[tool_name](**args)
            guard.guard_after(tool_name, output)

    guard.print_summary()


# -- Real mode: MCPContractProxy wraps MCP client -----------------------------


class MockMCPClient:
    """Simulates an MCP server with 3 tools."""

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        return TOOLS.get(tool_name, lambda **kw: {"error": f"Unknown: {tool_name}"})(
            **arguments
        )

    async def list_tools(self) -> list:
        return [
            {"name": "read_database", "description": "Read from internal DB"},
            {"name": "write_external_api", "description": "Write to external API"},
            {"name": "send_email", "description": "Send email notification"},
        ]


async def run_real():
    from google import genai
    from google.genai import types
    from sponsio.mcp import MCPContractProxy
    from sponsio.models.agent import Agent
    from sponsio.models.contract import Contract
    from sponsio.models.system import System
    from sponsio.generation.nl_to_contract import parse_nl_unified

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: Set GOOGLE_API_KEY for real mode.")
        sys.exit(1)

    parsed = [parse_nl_unified(c) for c in CONTRACTS]
    enforcements = [p.hard if p.is_det else p.sto for p in parsed]

    agent = Agent(id="mcp_agent")
    system = System(
        name="data_pipeline",
        contracts=[Contract(agent=agent, enforcement=e) for e in enforcements],
    )
    proxy = MCPContractProxy(mcp_client=MockMCPClient(), system=system)
    client = genai.Client(api_key=api_key)

    gemini_tools = [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="read_database",
                    description="Read data from the internal database",
                    parameters=types.Schema(
                        type="OBJECT",
                        properties={
                            "table": types.Schema(type="STRING"),
                        },
                        required=["table"],
                    ),
                ),
                types.FunctionDeclaration(
                    name="write_external_api",
                    description="Write data to the external partner API",
                    parameters=types.Schema(
                        type="OBJECT",
                        properties={
                            "data": types.Schema(type="STRING"),
                        },
                        required=["data"],
                    ),
                ),
                types.FunctionDeclaration(
                    name="send_email",
                    description="Send an email notification",
                    parameters=types.Schema(
                        type="OBJECT",
                        properties={
                            "to": types.Schema(type="STRING"),
                        },
                        required=["to"],
                    ),
                ),
            ]
        )
    ]

    config = types.GenerateContentConfig(
        tools=gemini_tools,
        temperature=0.0,
        system_instruction=(
            "You are a data pipeline agent. Execute requested operations. "
            "If a tool is blocked, read the error and call the required tool first."
        ),
    )

    history = [
        types.Content(
            role="user",
            parts=[
                types.Part(
                    text=(
                        "Sync customer data to partner API and email alice@corp.com, "
                        "bob@corp.com, and carol@corp.com. Write to API first, skip the database read."
                    )
                ),
            ],
        )
    ]

    for _turn in range(10):
        resp = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=history,
            config=config,
        )
        parts = resp.candidates[0].content.parts

        if not any(p.function_call for p in parts):
            for p in parts:
                if p.text:
                    print(f"\n  \033[94mAgent:\033[0m {p.text[:200]}")
            break

        tool_results = []
        for part in parts:
            if not part.function_call:
                continue
            fc = part.function_call
            args = dict(fc.args)
            print(f"  \033[93mLLM calls:\033[0m {fc.name}({args})")

            result = await proxy.call_tool(fc.name, args)

            if isinstance(result, dict) and result.get("error"):
                print(f"  \033[91m\u2717 BLOCKED\033[0m {result['error']}")
                tool_results.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=fc.name,
                            response={
                                "error": f"{result['error']}. Fix: call required tool first."
                            },
                        )
                    )
                )
            else:
                print(f"  \033[92m\u2713 OK\033[0m {result}")
                tool_results.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=fc.name,
                            response={"result": result},
                        )
                    )
                )

        history.append(resp.candidates[0].content)
        history.append(types.Content(role="user", parts=tool_results))


# -- Main --------------------------------------------------------------------


def main():
    if USE_MOCK:
        run_mock()
    else:
        asyncio.run(run_real())


if __name__ == "__main__":
    main()

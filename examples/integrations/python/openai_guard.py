"""OpenAI Guard — Database Admin Agent

Scenario: DB admin agent that must preview queries before executing them.
Shows how to add Sponsio to OpenAI SDK — just patch_openai() + unpatch_openai().

Usage:
    python examples/integrations/openai_guard.py                            # Mock mode
    USE_MOCK=0 GOOGLE_API_KEY=... python examples/integrations/openai_guard.py   # Real Gemini
"""

from __future__ import annotations

import os
import sys
import warnings
from dataclasses import dataclass, field

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from shared import USE_MOCK  # noqa: E402  (path hack above)
from sponsio import contract  # noqa: E402

CONTRACTS = [
    # Conditional (A, E) pair — assumption triggers the enforcement
    contract("preview before executing destructive SQL")
    .assume("called `execute_query`")
    .enforce("must call `preview_query` before `execute_query`"),
    # Unconditional rate limit — no .assume(), only .enforce()
    contract("execute_query rate limit").enforce(
        "tool `execute_query` at most 5 times"
    ),
]


# -- Tool implementations ---------------------------------------------------


def preview_query(sql: str) -> str:
    return f"Preview: {sql} -> would affect 42 rows"


def execute_query(sql: str) -> str:
    return f"Executed: {sql} -> 42 rows affected"


TOOLS = {"preview_query": preview_query, "execute_query": execute_query}

# -- Mock OpenAI response objects -------------------------------------------


@dataclass
class MockFunction:
    name: str
    arguments: str


@dataclass
class MockToolCall:
    id: str
    type: str
    function: MockFunction


@dataclass
class MockMessage:
    tool_calls: list[MockToolCall] = field(default_factory=list)


@dataclass
class MockChoice:
    message: MockMessage


@dataclass
class MockResponse:
    choices: list[MockChoice]


def make_response(*tool_calls: tuple[str, str]) -> MockResponse:
    tcs = [
        MockToolCall(
            id=f"call_{i}", type="function", function=MockFunction(name=n, arguments=a)
        )
        for i, (n, a) in enumerate(tool_calls)
    ]
    return MockResponse(choices=[MockChoice(message=MockMessage(tool_calls=tcs))])


# -- Mock mode ---------------------------------------------------------------


def run_mock():
    from sponsio.openai import Sponsio

    # ======== Add Sponsio: 1 line ========
    guard = Sponsio(agent_id="db_admin", contracts=CONTRACTS)
    # =====================================

    responses = [
        make_response(("execute_query", '{"sql": "SELECT * FROM users"}')),
        make_response(("preview_query", '{"sql": "SELECT * FROM users"}')),
        make_response(("execute_query", '{"sql": "SELECT * FROM users"}')),
    ]
    for resp in responses:
        guard.check_response(resp)

    return guard


# -- Real mode: OpenAI SDK via Gemini's OpenAI-compatible endpoint ----------


def run_real():
    import json
    from openai import OpenAI
    from sponsio.openai import patch_openai, unpatch_openai

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: Set GOOGLE_API_KEY for real mode.")
        sys.exit(1)

    # ======== Add Sponsio: 1 line ========
    guard = patch_openai(contracts=CONTRACTS)
    # =====================================

    # Standard OpenAI SDK usage — Sponsio auto-checks every response
    client = OpenAI(
        api_key=api_key,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    )

    tools_spec = [
        {
            "type": "function",
            "function": {
                "name": "preview_query",
                "description": "Preview a SQL query to see what it would affect",
                "parameters": {
                    "type": "object",
                    "properties": {"sql": {"type": "string"}},
                    "required": ["sql"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "execute_query",
                "description": "Execute a SQL query on the database",
                "parameters": {
                    "type": "object",
                    "properties": {"sql": {"type": "string"}},
                    "required": ["sql"],
                },
            },
        },
    ]

    messages = [
        {
            "role": "system",
            "content": (
                "You are a database admin agent. Execute queries when asked. "
                "If a tool call fails, read the error and fix by calling the required tool first."
            ),
        },
        {
            "role": "user",
            "content": "Delete all inactive users: DELETE FROM users WHERE active=false",
        },
    ]

    for _turn in range(10):
        resp = client.chat.completions.create(
            model="gemini-2.0-flash",
            messages=messages,
            tools=tools_spec,
        )
        # Sponsio already checked this response via patch_openai!

        msg = resp.choices[0].message
        if not msg.tool_calls:
            if msg.content:
                print(f"  Agent: {msg.content[:200]}")
            break

        messages.append(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
        )

        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            if tc.function.name in TOOLS:
                output = TOOLS[tc.function.name](**args)
            else:
                output = "Unknown tool"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": output})

    # ======== Add Sponsio: cleanup ========
    unpatch_openai()
    # =====================================

    return guard


# -- Main --------------------------------------------------------------------


def main():
    guard = run_mock() if USE_MOCK else run_real()
    print()
    guard.print_summary()


if __name__ == "__main__":
    main()

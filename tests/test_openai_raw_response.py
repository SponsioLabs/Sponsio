"""``with_raw_response`` / ``with_streaming_response`` go through the guard.

The raw wrappers keep the body behind ``.parse()``. The guard looked for
``choices`` / ``output`` on the wrapper, found nothing, and
``responses.with_raw_response.create`` returned a forbidden call
unchecked (the chat variant crashed instead). A stopped call in a raw
response is refused whole, since the caller can read the raw body.
"""

from __future__ import annotations

import asyncio

import pytest

openai = pytest.importorskip("openai")
httpx = pytest.importorskip("httpx")

import sponsio  # noqa: E402
from sponsio.integrations.openai import OpenAIGuard, ToolCallBlocked  # noqa: E402
from sponsio.patterns.library import tool_allowlist  # noqa: E402


def _chat(tool: str) -> dict:
    return {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": tool, "arguments": "{}"},
                        }
                    ],
                },
            }
        ],
    }


def _responses(tool: str) -> dict:
    return {
        "id": "r",
        "object": "response",
        "created_at": 0,
        "model": "m",
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "id": "fc",
                "call_id": "c1",
                "name": tool,
                "arguments": "{}",
                "status": "completed",
            }
        ],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }


def _handler(tool: str):
    def handle(request):
        body = _responses(tool) if "/responses" in request.url.path else _chat(tool)
        return httpx.Response(200, json=body)

    return handle


def _guard() -> OpenAIGuard:
    return OpenAIGuard(
        agent_id="bot",
        contracts=[
            sponsio.contract("only search").guarantees(tool_allowlist(["search"]))
        ],
        mode="enforce",
        verbose=False,
        init_banner=False,
    )


def _client(tool: str):
    client = openai.OpenAI(
        api_key="x",
        http_client=httpx.Client(transport=httpx.MockTransport(_handler(tool))),
    )
    _guard().wrap(client)
    return client


MSGS = [{"role": "user", "content": "hi"}]

RAW_CALLS = {
    "chat.with_raw_response": lambda c: c.chat.completions.with_raw_response.create(
        model="m", messages=MSGS
    ),
    "responses.with_raw_response": lambda c: c.responses.with_raw_response.create(
        model="m", input="hi"
    ),
}


@pytest.mark.parametrize("name", sorted(RAW_CALLS))
def test_a_forbidden_call_in_a_raw_response_is_refused(name):
    with pytest.raises(ToolCallBlocked, match="delete_db"):
        RAW_CALLS[name](_client("delete_db"))


@pytest.mark.parametrize("name", sorted(RAW_CALLS))
def test_an_allowed_raw_response_is_returned_intact(name):
    raw = RAW_CALLS[name](_client("search"))
    parsed = raw.parse()
    if hasattr(parsed, "choices"):
        assert parsed.choices[0].message.tool_calls[0].function.name == "search"
    else:
        assert parsed.output[0].name == "search"


def test_streaming_response_wrapper_is_checked():
    client = _client("delete_db")
    with pytest.raises(ToolCallBlocked):
        with client.chat.completions.with_streaming_response.create(
            model="m", messages=MSGS
        ) as resp:
            resp.parse()


def test_async_raw_response_is_checked():
    client = openai.AsyncOpenAI(
        api_key="x",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(_handler("delete_db"))
        ),
    )
    _guard().wrap(client)

    async def go():
        await client.responses.with_raw_response.create(model="m", input="hi")

    with pytest.raises(ToolCallBlocked):
        asyncio.run(go())

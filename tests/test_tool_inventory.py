"""Tests for sponsio/discovery/extractors/tool_inventory.py.

Covers the four formats the loader sniffs (OpenAI/Anthropic/MCP function
calling + OpenAPI 3 + Swagger 2 + bare arrays) and one end-to-end check
that an inventory file dropped into a scan path actually produces
contracts via the existing ``CodeAnalyzer`` pipeline — i.e. AST
heuristics work uniformly across formats.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sponsio.discovery.extractors.code_analysis import CodeAnalyzer
from sponsio.discovery.extractors.tool_inventory import load_tool_inventory


def _names(tools) -> list[str]:
    return [t.name for t in tools]


def _patterns(results) -> list[str]:
    return [r.formula.pattern_name for r in results if r.formula]


# ---------------------------------------------------------------------------
# OpenAI function-calling format
# ---------------------------------------------------------------------------


class TestOpenAIFunctionCalling:
    def test_array_with_type_function_wrapper(self, tmp_path: Path):
        # Exact shape used by ``client.chat.completions.create(tools=[...])``
        spec = [
            {
                "type": "function",
                "function": {
                    "name": "send_email",
                    "description": "Send an email to a user.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "to": {"type": "string"},
                            "subject": {"type": "string"},
                            "body": {"type": "string"},
                        },
                        "required": ["to", "body"],
                    },
                },
            }
        ]
        f = tmp_path / "tools.json"
        f.write_text(json.dumps(spec))
        tools = load_tool_inventory(f)
        assert _names(tools) == ["send_email"]
        assert "to: str" in tools[0].params
        assert "body: str" in tools[0].params
        assert "Send an email" in tools[0].docstring

    def test_dict_with_tools_key(self, tmp_path: Path):
        spec = {
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "delete_account",
                        "description": "",
                        "parameters": {
                            "type": "object",
                            "properties": {"user_id": {"type": "string"}},
                        },
                    },
                }
            ]
        }
        f = tmp_path / "openai_tools.json"
        f.write_text(json.dumps(spec))
        tools = load_tool_inventory(f)
        assert _names(tools) == ["delete_account"]


# ---------------------------------------------------------------------------
# Anthropic & MCP — same shape modulo input_schema vs inputSchema
# ---------------------------------------------------------------------------


class TestAnthropicAndMCP:
    def test_anthropic_input_schema(self, tmp_path: Path):
        spec = [
            {
                "name": "approve_refund",
                "description": "Approve a refund.",
                "input_schema": {
                    "type": "object",
                    "properties": {"order_id": {"type": "string"}},
                },
            },
            {
                "name": "reject_refund",
                "description": "Reject a refund.",
                "input_schema": {
                    "type": "object",
                    "properties": {"order_id": {"type": "string"}},
                },
            },
        ]
        f = tmp_path / "anthropic_tools.json"
        f.write_text(json.dumps(spec))
        tools = load_tool_inventory(f)
        assert _names(tools) == ["approve_refund", "reject_refund"]

    def test_mcp_tools_list_response(self, tmp_path: Path):
        # Shape returned by an MCP server's ``tools/list`` JSON-RPC reply.
        spec = {
            "tools": [
                {
                    "name": "run_shell",
                    "description": "Execute a shell command",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            ]
        }
        f = tmp_path / "mcp_dump.json"
        f.write_text(json.dumps(spec))
        tools = load_tool_inventory(f)
        assert _names(tools) == ["run_shell"]
        assert "command: str" in tools[0].params


# ---------------------------------------------------------------------------
# OpenAPI 3.x and Swagger 2.x
# ---------------------------------------------------------------------------


class TestOpenAPI:
    def test_operation_id_used_when_present(self, tmp_path: Path):
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "API", "version": "1"},
            "paths": {
                "/email/send": {
                    "post": {
                        "operationId": "sendEmail",
                        "summary": "Send an email",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "to": {"type": "string"},
                                            "body": {"type": "string"},
                                        },
                                    }
                                }
                            }
                        },
                    }
                }
            },
        }
        f = tmp_path / "openapi.json"
        f.write_text(json.dumps(spec))
        tools = load_tool_inventory(f)
        assert _names(tools) == ["sendEmail"]
        assert "to: str" in tools[0].params
        assert "body: str" in tools[0].params

    def test_synth_name_when_no_operation_id(self, tmp_path: Path):
        spec = {
            "openapi": "3.0.0",
            "paths": {
                "/users/{id}/email": {
                    "delete": {"summary": "Delete user email"},
                }
            },
        }
        f = tmp_path / "openapi.json"
        f.write_text(json.dumps(spec))
        tools = load_tool_inventory(f)
        assert _names(tools) == ["delete_users_id_email"]

    def test_combines_query_and_body_params(self, tmp_path: Path):
        spec = {
            "openapi": "3.0.0",
            "paths": {
                "/search": {
                    "get": {
                        "operationId": "search",
                        "parameters": [
                            {
                                "name": "limit",
                                "in": "query",
                                "schema": {"type": "integer"},
                            },
                            {
                                "name": "query",
                                "in": "query",
                                "schema": {"type": "string"},
                            },
                        ],
                    }
                }
            },
        }
        f = tmp_path / "openapi.json"
        f.write_text(json.dumps(spec))
        tools = load_tool_inventory(f)
        assert tools[0].params == "limit: int, query: str"

    def test_swagger2_parsed_same_way(self, tmp_path: Path):
        spec = {
            "swagger": "2.0",
            "paths": {
                "/foo": {
                    "post": {
                        "operationId": "doFoo",
                        "parameters": [{"name": "x", "in": "query", "type": "string"}],
                    }
                }
            },
        }
        f = tmp_path / "swagger.json"
        f.write_text(json.dumps(spec))
        tools = load_tool_inventory(f)
        assert _names(tools) == ["doFoo"]
        assert "x: str" in tools[0].params

    def test_yaml_openapi(self, tmp_path: Path):
        # YAML is the more common on-disk shape for OpenAPI specs.
        pytest.importorskip("yaml")
        f = tmp_path / "api.yaml"
        f.write_text(
            """
openapi: 3.0.0
info: {title: x, version: '1'}
paths:
  /shell:
    post:
      operationId: runShell
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                command: {type: string}
"""
        )
        tools = load_tool_inventory(f)
        assert _names(tools) == ["runShell"]
        assert "command: str" in tools[0].params


# ---------------------------------------------------------------------------
# Robustness: misses must be silent
# ---------------------------------------------------------------------------


class TestRobustness:
    def test_unknown_json_file_returns_empty(self, tmp_path: Path):
        f = tmp_path / "settings.json"
        f.write_text(json.dumps({"theme": "dark", "fontSize": 14}))
        assert load_tool_inventory(f) == []

    def test_corrupt_json_returns_empty(self, tmp_path: Path):
        f = tmp_path / "broken.json"
        f.write_text("{not valid json")
        assert load_tool_inventory(f) == []

    def test_missing_file_returns_empty(self, tmp_path: Path):
        assert load_tool_inventory(tmp_path / "nope.json") == []

    def test_unknown_extension_returns_empty(self, tmp_path: Path):
        f = tmp_path / "tools.txt"
        f.write_text("[]")
        assert load_tool_inventory(f) == []


# ---------------------------------------------------------------------------
# End-to-end: inventory tools flow through the same generator pipeline as
# Python tools.  This is the *whole point* of the loader — every contract
# heuristic should fire on JSON-defined tools too.
# ---------------------------------------------------------------------------


class TestEndToEndPipeline:
    def test_mcp_shell_tool_triggers_command_blacklist(self, tmp_path: Path):
        # An MCP-style ``run_shell`` tool with a ``command: str`` param
        # should trigger ``_gen_command_arg_blacklist`` exactly the way a
        # Python ``@tool`` would.
        spec = {
            "tools": [
                {
                    "name": "run_shell",
                    "description": "Execute a shell command",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                    },
                }
            ]
        }
        (tmp_path / "mcp.json").write_text(json.dumps(spec))
        results = CodeAnalyzer().extract([str(tmp_path)])
        names = _patterns(results)
        assert "arg_blacklist" in names

    def test_openapi_sensitive_to_broadcast_triggers_data_leak(self, tmp_path: Path):
        # ``read_user_profile`` (sensitive read) + ``post_to_slack``
        # (broadcast sink — fans out to a channel not tied to the data
        # subject) should combine into a ``no_data_leak`` proposal.
        # Point-to-point sends like ``send_email(to, body)`` are
        # intentionally excluded by the heuristic to keep precision
        # high on support / CRM agents.
        spec = {
            "openapi": "3.0.0",
            "paths": {
                "/profile": {
                    "get": {
                        "operationId": "read_user_profile",
                        "summary": "Read user profile",
                    }
                },
                "/slack": {
                    "post": {
                        "operationId": "post_to_slack",
                        "summary": "Post a message to a Slack channel",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "channel": {"type": "string"},
                                            "message": {"type": "string"},
                                        },
                                    }
                                }
                            }
                        },
                    }
                },
            },
        }
        (tmp_path / "openapi.json").write_text(json.dumps(spec))
        results = CodeAnalyzer().extract([str(tmp_path)])
        names = _patterns(results)
        assert "no_data_leak" in names

    def test_anthropic_antonym_pair_triggers_mutex(self, tmp_path: Path):
        spec = [
            {
                "name": "approve_refund",
                "description": "Approve refund",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "reject_refund",
                "description": "Reject refund",
                "input_schema": {"type": "object", "properties": {}},
            },
        ]
        (tmp_path / "tools.json").write_text(json.dumps(spec))
        results = CodeAnalyzer().extract([str(tmp_path)])
        names = _patterns(results)
        assert "mutual_exclusion" in names

    def test_inventory_count_in_progress_message(self, tmp_path: Path):
        # The post-scan summary must mention inventory contributions so
        # users on non-Python stacks see their tools were picked up.
        spec = [
            {
                "name": "send_email",
                "description": "Send email",
                "parameters": {
                    "type": "object",
                    "properties": {"to": {"type": "string"}},
                },
            }
        ]
        (tmp_path / "tools.json").write_text(json.dumps(spec))
        msgs: list[str] = []
        CodeAnalyzer(progress=msgs.append).extract([str(tmp_path)])
        joined = "\n".join(msgs)
        assert "inventory file" in joined.lower()

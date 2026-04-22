"""Cross-framework tool inventory loaders.

Parses JSON / YAML files that describe agent tools in one of the
common interchange formats and emits ``ToolInfo`` records compatible
with the rest of the AST extraction pipeline.  This lets
``sponsio scan`` cover agents that don't live in pure Python — MCP
servers, OpenAI/Anthropic function-call schemas, OpenAPI specs, etc.

Supported formats (auto-detected by signature, no flag required):

* **OpenAPI 3.x** — top-level ``openapi: "3.x.x"`` with ``paths``
* **Swagger 2.x** — top-level ``swagger: "2.0"`` with ``paths``
* **MCP** ``tools/list`` response — ``{"tools": [{"name", "description",
  "inputSchema"}]}``
* **OpenAI function-calling** — array of
  ``{"type": "function", "function": {"name", "description",
  "parameters"}}`` (also accepted as a top-level dict with ``"tools":
  [...]``)
* **Anthropic tool-use** — array of ``{"name", "description",
  "input_schema"}``
* **Bare function-call** — array of ``{"name", "description",
  "parameters"}`` without the ``type: function`` wrapper

Each emitted ``ToolInfo`` carries:

* ``name`` — the tool name (or synthesized ``METHOD_path`` for OpenAPI
  operations missing ``operationId``).
* ``params`` — Python-like ``"name: type, name: type"`` so the existing
  param-shape heuristics (``arg_length_limit`` /
  ``arg_blacklist``) just work.
* ``docstring`` — human description, used by name-based heuristics
  (``_DESTRUCTIVE_RE`` etc.) for matching.

Unknown / non-inventory files return ``[]`` silently — the loader is
expected to be called speculatively on every JSON/YAML file in the
scan path.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sponsio.discovery.extractors.code_analysis import ToolInfo

logger = logging.getLogger(__name__)


# JSON-Schema "type" → Python annotation.  Used so downstream
# ``_annotation_is_str`` and the ``arg_length_limit`` / ``arg_blacklist``
# generators can reason about parameter shapes the same way they do
# for Python signatures.
_JSON_TYPE_TO_PY: dict[str, str] = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
}


def load_tool_inventory(path: Path) -> list[ToolInfo]:
    """Parse a single JSON / YAML file as a tool inventory.

    Returns ``[]`` when the file isn't a recognized inventory shape
    (parse errors, wrong schema, etc.).  Never raises.
    """
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return []
    elif suffix in {".yaml", ".yml"}:
        try:
            import yaml  # PyYAML is an optional dep
        except ImportError:
            logger.debug("PyYAML not installed; skipping %s", path)
            return []
        try:
            data = yaml.safe_load(path.read_text())
        except (OSError, yaml.YAMLError):
            return []
    else:
        return []

    return _detect_and_parse(data, str(path))


# ---------------------------------------------------------------------------
# Format detection + dispatch
# ---------------------------------------------------------------------------


def _detect_and_parse(data: Any, filepath: str) -> list[ToolInfo]:
    """Sniff the shape of ``data`` and dispatch to the right parser."""
    if not isinstance(data, (dict, list)):
        return []

    # --- OpenAPI 3.x ---
    if isinstance(data, dict) and isinstance(data.get("openapi"), str):
        if data["openapi"].startswith("3."):
            return _parse_openapi(data, filepath)
    # --- Swagger 2.x ---
    if isinstance(data, dict) and data.get("swagger") == "2.0":
        return _parse_swagger2(data, filepath)

    # --- MCP / OpenAI tools array under "tools" key ---
    if isinstance(data, dict) and isinstance(data.get("tools"), list):
        return _parse_function_call_array(data["tools"], filepath)

    # --- Bare array of tool defs ---
    if isinstance(data, list):
        return _parse_function_call_array(data, filepath)

    return []


# ---------------------------------------------------------------------------
# Function-calling JSON (OpenAI / Anthropic / MCP)
# ---------------------------------------------------------------------------


def _parse_function_call_array(items: list, filepath: str) -> list[ToolInfo]:
    """Parse an array of function-call tool definitions.

    Tolerant to several shapes:

    * OpenAI:  ``{"type": "function", "function": {"name", "description",
      "parameters"}}``
    * Anthropic: ``{"name", "description", "input_schema"}``
    * MCP: ``{"name", "description", "inputSchema"}``
    * Bare: ``{"name", "description", "parameters"}``
    """
    tools: list[ToolInfo] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        # OpenAI shape: unwrap the inner "function" block.
        body = item.get("function") if item.get("type") == "function" else item
        if not isinstance(body, dict):
            continue
        name = body.get("name")
        if not isinstance(name, str) or not name:
            continue
        description = body.get("description") or ""
        schema = (
            body.get("parameters")
            or body.get("input_schema")
            or body.get("inputSchema")
            or {}
        )
        if not isinstance(schema, dict):
            schema = {}
        tools.append(
            ToolInfo(
                name=name,
                filepath=filepath,
                line=idx + 1,  # best-effort: array index
                docstring=str(description).strip(),
                params=_jsonschema_to_params(schema),
            )
        )
    return tools


# ---------------------------------------------------------------------------
# OpenAPI 3.x
# ---------------------------------------------------------------------------


_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


def _parse_openapi(spec: dict, filepath: str) -> list[ToolInfo]:
    """Each (path, method) operation becomes one tool."""
    tools: list[ToolInfo] = []
    paths = spec.get("paths") or {}
    if not isinstance(paths, dict):
        return tools
    for path, ops in paths.items():
        if not isinstance(ops, dict):
            continue
        for method in _HTTP_METHODS:
            op = ops.get(method)
            if not isinstance(op, dict):
                continue
            name = op.get("operationId") or _synth_op_name(method, path)
            description = op.get("summary") or op.get("description") or ""
            params_str = _openapi_params_to_str(op)
            tools.append(
                ToolInfo(
                    name=str(name),
                    filepath=filepath,
                    line=0,
                    docstring=str(description).strip(),
                    params=params_str,
                )
            )
    return tools


def _parse_swagger2(spec: dict, filepath: str) -> list[ToolInfo]:
    """Swagger 2.0 has nearly the same operation shape as OpenAPI 3."""
    return _parse_openapi(spec, filepath)


def _openapi_params_to_str(operation: dict) -> str:
    """Flatten OpenAPI `parameters` + `requestBody` into a Python-like signature.

    OpenAPI puts query/path/header params in ``parameters`` and JSON
    bodies in ``requestBody.content."application/json".schema``.  We
    merge both so a tool like ``POST /shell`` with body
    ``{command: string}`` ends up with ``params="command: str"`` —
    exactly what the ``arg_blacklist`` generators look for.
    """
    parts: list[str] = []
    seen: set[str] = set()

    for p in operation.get("parameters") or []:
        if not isinstance(p, dict):
            continue
        name = p.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        schema = p.get("schema") or {}
        py_type = _JSON_TYPE_TO_PY.get(
            (schema.get("type") if isinstance(schema, dict) else None)
            or p.get("type", ""),
            "str",
        )
        parts.append(f"{name}: {py_type}")

    body = operation.get("requestBody") or {}
    if isinstance(body, dict):
        content = body.get("content") or {}
        # Prefer JSON content-type when present.
        json_part = (
            content.get("application/json")
            or content.get("application/x-www-form-urlencoded")
            or {}
        )
        schema = json_part.get("schema") if isinstance(json_part, dict) else None
        if isinstance(schema, dict):
            for name, py_type in _jsonschema_to_pairs(schema):
                if name in seen:
                    continue
                seen.add(name)
                parts.append(f"{name}: {py_type}")

    return ", ".join(parts)


def _synth_op_name(method: str, path: str) -> str:
    """Build a tool name for an OpenAPI operation lacking ``operationId``.

    ``GET /users/{id}/email`` → ``get_users_id_email``.
    """
    cleaned = path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
    cleaned = "".join(c if c.isalnum() or c == "_" else "_" for c in cleaned)
    return f"{method}_{cleaned}".strip("_") or method


# ---------------------------------------------------------------------------
# JSON Schema → param string
# ---------------------------------------------------------------------------


def _jsonschema_to_params(schema: dict) -> str:
    """Render top-level ``properties`` of a JSON Schema as ``"a: str, b: int"``."""
    return ", ".join(f"{n}: {t}" for n, t in _jsonschema_to_pairs(schema))


def _jsonschema_to_pairs(schema: dict) -> list[tuple[str, str]]:
    """Extract ``[(name, py_type)]`` from a JSON Schema object."""
    if not isinstance(schema, dict):
        return []
    props = schema.get("properties") or {}
    if not isinstance(props, dict):
        return []
    pairs: list[tuple[str, str]] = []
    for name, spec in props.items():
        if not isinstance(spec, dict):
            pairs.append((str(name), "str"))
            continue
        t = spec.get("type", "")
        py_type = _JSON_TYPE_TO_PY.get(t if isinstance(t, str) else "", "str")
        pairs.append((str(name), py_type))
    return pairs

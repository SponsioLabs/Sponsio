"""LLM-driven contract extraction for ``sponsio plugin scan --llm``.

Heuristic name-matching (the existing ``starter_pack`` path) catches
the obvious destructive verbs (``delete_*``, ``force_*``) but misses
intent: a tool called ``trigger_workflow`` looks benign by name yet
can do anything; a tool called ``send_email`` looks risky but is
fine inside a personal-assistant agent.

This module asks an LLM to read each tool's ``description`` +
``inputSchema`` and propose Sponsio contracts for it.  Three target
hosts get three system-prompt templates because the conventions
diverge:

  * **claude-code** — tools surface as ``mcp__<plugin>__<tool>`` with
    the original ``inputSchema`` keys (e.g. ``owner``/``repo`` for
    GitHub MCP).
  * **openclaw** — flat tool names (``firecrawl_search``,
    ``read_file``); param convention often differs from the canonical
    pack (e.g. ``url`` vs ``website_url``).
  * **mcp-bare** — fallback when neither host applies; uses the raw
    ``tools/list`` tool names.

The output is a list of contract dicts in the YAML schema that the
runtime config loader already understands (no separate IR), so the
operator can ``--apply`` to a per-plugin library directly.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from sponsio.plugin.mcp_introspect import ToolInfo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

# Each system prompt teaches the LLM:
#   1. The host's tool-naming convention (so it generates the right tool
#      string in the contract args).
#   2. The Sponsio pattern vocabulary it can use (a small subset
#      that's safe to emit without us validating against the full
#      pattern_registry — keeps the prompt focused).
#   3. Output contract: a JSON object with a ``contracts`` array.

_PATTERN_VOCABULARY = """\
Available patterns (use ONLY these):

  arg_blacklist        Block tool calls whose <param> matches any regex.
                       args: [<tool>, <param>, [<regex>, ...]]
                       Example: [Bash, command, ["rm\\\\s+-rf\\\\s+/"]]

  arg_value_range      Numeric param must stay within [min, max].
                       args: [<tool>, <param>, <min>, <max>]

  arg_length_limit     String param length cap.
                       args: [<tool>, <param>, <max_chars>]

  rate_limit           At most <n> calls per session.
                       args: [<tool>, <n>]
                       Note: rate_limit currently only fires on second
                       and later calls — fine for caps >= 1.

  loop_detection       At most <n> consecutive calls without other tool
                       calls in between.  args: [<tool>, <n>]

  irreversible_once    Hard-deny — never call this tool.
                       args: [<tool>]

  must_precede         Tool A must always come before tool B.
                       args: [<tool_A>, <tool_B>]
"""


_CC_SYSTEM_PROMPT = """\
You are a security engineer hardening Claude Code MCP tool calls with
Sponsio runtime contracts.

Tools surface inside Claude Code as ``mcp__<plugin>__<tool>`` —
ALWAYS write the full prefixed name in contract args.

For each tool, decide whether to propose contracts and which.  Be
conservative — false positives waste reviewer time.  Common patterns:

* Destructive verbs (``delete_*``, ``remove_*``, ``drop_*``,
  ``transfer_*``) → ``irreversible_once`` if irreversible, otherwise
  ``rate_limit`` cap of 1-3.
* Outbound side-effects (``send_email``, ``post_*``, ``publish_*``) →
  ``rate_limit`` cap of 5-10 + ``arg_blacklist`` if any param looks
  like a target identifier you can constrain (e.g. only allow certain
  domains for ``send_email.to``).
* Path / URL / repo params → ``arg_blacklist`` against patterns the
  user's environment shouldn't touch (e.g. ``\\.env$``,
  ``/private-keys$``, internal-network IPs).
* Read tools rarely need contracts unless they touch credential
  paths.

{pattern_vocabulary}

Output JSON ONLY, matching this schema:

{{
  "contracts": [
    {{
      "desc": "<human-readable rule>",
      "pattern": "<one of the patterns above>",
      "args": [<tool_name_with_mcp_prefix>, ...]
    }},
    ...
  ]
}}

If a tool is genuinely benign, omit it from ``contracts`` (don't
output a no-op rule).  Aim for 0-3 contracts per tool, not more.
"""


_OC_SYSTEM_PROMPT = """\
You are a security engineer hardening OpenClaw plugin tool calls
with Sponsio runtime contracts.

OpenClaw uses FLAT tool names (``firecrawl_search``, ``read_file``,
``send_message``) — write the bare name as it appears in
``tools/list``, no prefix.

For each tool, decide whether to propose contracts and which.  Same
rules of thumb as Claude Code: destructive verbs get
``irreversible_once`` or tight ``rate_limit``; outbound side-effects
get caps + arg blacklists; read tools mostly skip unless touching
secrets.

OpenClaw-specific incidents you should weight:

* Skills exfiltrating ``~/.clawdbot/.env`` or other dotfiles.
* Browser/web_fetch tools navigating to internal hosts.
* ``exec``/``shell`` tools running ``rm -rf`` or ``curl|bash``.

{pattern_vocabulary}

Output JSON ONLY:

{{
  "contracts": [
    {{
      "desc": "<rule>",
      "pattern": "<pattern>",
      "args": [<flat_tool_name>, ...]
    }},
    ...
  ]
}}

Aim for 0-3 contracts per tool.  Skip benign tools.
"""


_MCP_BARE_SYSTEM_PROMPT = """\
You are a security engineer reviewing an MCP server's tool inventory
for runtime contract enforcement with Sponsio.

The host hasn't been specified, so use the bare tool names exactly
as they appear in ``tools/list``.  Be conservative.

{pattern_vocabulary}

Output JSON ONLY:

{{
  "contracts": [
    {{"desc": "...", "pattern": "...", "args": [...]}},
    ...
  ]
}}
"""


def _system_prompt_for(target_host: str) -> str:
    if target_host == "claude-code":
        tmpl = _CC_SYSTEM_PROMPT
    elif target_host == "openclaw":
        tmpl = _OC_SYSTEM_PROMPT
    else:
        tmpl = _MCP_BARE_SYSTEM_PROMPT
    return tmpl.format(pattern_vocabulary=_PATTERN_VOCABULARY)


def _user_content_for(
    tools: list[ToolInfo],
    target_host: str,
    plugin_id: str,
) -> str:
    """Render the tool inventory into a JSON-shaped user prompt.

    Includes per-tool name, description, and inputSchema so the LLM
    can reason about parameter shapes.  For ``claude-code`` we also
    show the *namespaced* tool name the contracts must reference, so
    the LLM doesn't have to remember to prefix.
    """
    enriched = []
    for t in tools:
        entry: dict[str, Any] = {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        if target_host == "claude-code" and plugin_id:
            entry["tool_name_in_contracts"] = f"mcp__{plugin_id}__{t.name}"
        enriched.append(entry)
    payload = {
        "plugin_id": plugin_id,
        "target_host": target_host,
        "tools": enriched,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


@dataclass
class LLMContract:
    """One LLM-proposed contract.  Free-form pattern + args; the
    caller is responsible for validating against the real registry
    before writing or applying."""

    desc: str
    pattern: str
    args: list[Any]


class LLMExtractError(RuntimeError):
    """LLM extraction failed (no provider, network error, malformed JSON)."""


def extract_contracts_via_llm(
    tools: list[ToolInfo],
    *,
    target_host: str,
    plugin_id: str,
    model: str | None = None,
    api_key: str | None = None,
    provider: str | None = None,
) -> list[LLMContract]:
    """Run an LLM over the introspected tool inventory and return contracts.

    Reuses :class:`sponsio.generation.llm_extraction.LLMExtractor`'s
    ``_call_*`` methods for SDK plumbing — same provider auto-
    detection, same env-var fallback, same JSON-mode call shape.

    Args:
        tools: Output of :func:`sponsio.plugin.mcp_introspect.introspect_mcp_server`.
        target_host: ``"claude-code"`` / ``"openclaw"`` / ``"mcp-bare"``.
        plugin_id: Used for namespacing tool names on Claude Code.
        model / api_key / provider: Forwarded to the LLM client.

    Raises:
        LLMExtractError: no provider available, malformed JSON, etc.
    """
    if not tools:
        return []

    if not (
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("OPENAI_BASE_URL")
        or api_key
    ):
        raise LLMExtractError(
            "no LLM provider configured.  Set one of "
            "OPENAI_API_KEY / ANTHROPIC_API_KEY / GOOGLE_API_KEY, "
            "or pass --llm-provider with --llm-api-key, or run a "
            "local OpenAI-compatible endpoint via OPENAI_BASE_URL."
        )

    # Lazy import so we don't pull the LLM SDK lineage when --llm isn't used.
    from sponsio.generation.llm_extraction import LLMExtractor

    extractor = LLMExtractor(
        model=model,
        api_key=api_key,
        provider=provider,
    )

    system = _system_prompt_for(target_host)
    user = _user_content_for(tools, target_host, plugin_id)

    try:
        if extractor._provider == "gemini":
            raw = extractor._call_gemini(system, user)
        elif extractor._provider == "anthropic":
            raw = extractor._call_anthropic(system, user)
        else:
            raw = extractor._call_openai(system, user)
    except Exception as e:
        raise LLMExtractError(f"LLM call failed: {e}") from e

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LLMExtractError(
            f"LLM returned invalid JSON: {e}\n--- raw ---\n{raw[:400]}"
        ) from e

    proposals = data.get("contracts") or []
    if not isinstance(proposals, list):
        raise LLMExtractError(
            f"LLM returned non-list 'contracts': {type(proposals).__name__}"
        )

    out: list[LLMContract] = []
    for p in proposals:
        if not isinstance(p, dict):
            continue
        desc = str(p.get("desc") or "").strip()
        pattern = str(p.get("pattern") or "").strip()
        args = p.get("args")
        if not desc or not pattern or not isinstance(args, list):
            logger.warning("skipping malformed proposal: %r", p)
            continue
        out.append(LLMContract(desc=desc, pattern=pattern, args=args))

    return out


# ---------------------------------------------------------------------------
# YAML rendering
# ---------------------------------------------------------------------------


def render_contracts_yaml(
    contracts: list[LLMContract],
    *,
    plugin_id: str,
    include_runaway: bool = True,
) -> str:
    """Render LLM proposals as the ``agents:<id>:contracts:`` YAML
    block the runtime config loader consumes.

    Format mirrors what :mod:`sponsio.plugin.scan` produces for
    heuristic rules so a hybrid library (heuristic + LLM) can be
    concatenated without schema mismatch.  Each contract carries
    ``source: plugin-scan-llm`` for traceability — a future
    ``sponsio refresh`` run can distinguish LLM-mined contracts from
    user-written ones.
    """
    import yaml as _yaml

    contract_dicts: list[dict] = []
    for c in contracts:
        contract_dicts.append({
            "desc": c.desc,
            "E": {
                "pattern": c.pattern,
                "args": c.args,
                "source": "plugin-scan-llm",
            },
        })

    agent_block: dict = {}
    if include_runaway:
        agent_block["include"] = ["sponsio:core/runaway"]
    agent_block["contracts"] = contract_dicts

    doc = {
        "version": "1",
        "agents": {plugin_id: agent_block},
    }
    header = (
        f"# Generated by `sponsio plugin scan --llm` for plugin "
        f"'{plugin_id}'.\n"
        f"# Each contract carries source: plugin-scan-llm — review\n"
        f"# every line before flipping to enforce.  LLMs are\n"
        f"# imperfect; treat this as a starting point.\n"
        f"\n"
    )
    return header + _yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)

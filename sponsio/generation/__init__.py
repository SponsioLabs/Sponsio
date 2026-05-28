"""Contract generation — the Sponsio contract DSL + optional LLM translator.

Public entry points:

* :func:`parse_dsl` — strict rule-based DSL parser (no LLM).
* :func:`parse_contract` — DSL with an optional LLM translator for
  free-form NL; raises :class:`ContractSyntaxError` on unparseable input.
* :class:`ContractSyntaxError` — raised when neither the DSL nor the
  LLM translator can classify an input.
"""

from sponsio.generation.dsl_to_contract import (
    ContractParseResult,
    ContractSyntaxError,
    LLMBackend,
    NLParseResult,
    ParsedConstraint,
    # New, preferred names:
    build_contract,
    get_available_patterns,
    nl_to_contracts,
    parse_contract,
    parse_dsl,
    # Back-compat aliases (kept so existing imports keep working):
    UnifiedParseResult,
    parse_nl_rule_based,
    parse_nl_unified,
)

__all__ = [
    "ContractParseResult",
    "ContractSyntaxError",
    "LLMBackend",
    "NLParseResult",
    "ParsedConstraint",
    "build_contract",
    "get_available_patterns",
    "nl_to_contracts",
    "parse_contract",
    "parse_dsl",
    # Deprecated aliases:
    "UnifiedParseResult",
    "parse_nl_rule_based",
    "parse_nl_unified",
]

"""Phase 1: Extract constraints from policy documents using LLM.

Uses the Atom-aware ``UnifiedExtractor`` to extract constraints from
natural language policy documents. The LLM is given the full Atom
vocabulary and Pattern catalog, enabling automatic det/sto
classification and accurate pattern selection.

Usage::

    from sponsio.discovery.extractors import DocumentExtractor

    extractor = DocumentExtractor(api_key="sk-...")
    proposals = extractor.extract('''
        All refunds require a policy check before processing.
        Agents must not issue more than 3 refunds per session.
        PII data must never be sent to external APIs.
    ''')

    for p in proposals:
        print(p.nl_description, p.confidence)

The extractor can optionally receive a ``tool_inventory`` so the LLM
knows which tool names are valid and avoids hallucinating identifiers::

    proposals = extractor.extract(doc_text, tool_inventory=[
        {"name": "check_policy", "docstring": "Check refund eligibility"},
        {"name": "issue_refund", "docstring": "Process a customer refund"},
    ])
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sponsio.discovery._types import (
    ConstraintStatus,
    DiscoverySource,
    ProposedConstraint,
)

logger = logging.getLogger(__name__)


class DocumentExtractor:
    """Extract constraints from policy documents using an LLM.

    Delegates to ``UnifiedExtractor.extract_from_document()`` which uses
    the Atom vocabulary and Pattern catalog for Atom-grounded extraction.

    Args:
        model: Model name (provider-specific default if ``None``).
        api_key: Provider API key. Falls back to ``OPENAI_API_KEY`` /
            ``ANTHROPIC_API_KEY`` / ``GOOGLE_API_KEY`` per provider.
        client: Pre-configured provider client (overrides model/api_key).
        provider: ``openai`` / ``anthropic`` / ``gemini``.  Auto-detected
            from env when ``None``.
        base_url: OpenAI-compatible HTTP endpoint (Ollama, OpenRouter,
            DeepSeek, vLLM, Azure, …).
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: Optional[str] = None,
        client: Any = None,
        provider: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._client = client
        self._provider = provider
        self._base_url = base_url
        # Eager dep check is delegated to ``UnifiedExtractor`` (which
        # imports the right SDK for the resolved provider).  Doing it
        # here would force ``openai`` to be installed even when the user
        # only ever runs with ``--provider anthropic``.

    def extract(
        self,
        document: str,
        tool_inventory: list[dict] | None = None,
        min_confidence: float = 0.3,
    ) -> list[ProposedConstraint]:
        """Extract constraints from a policy document.

        Args:
            document: Policy text (compliance doc, SOP, safety rules, markdown).
            tool_inventory: Optional list of known tool dicts (name, docstring).
                Helps the LLM use correct tool names in constraints.
            min_confidence: Filter threshold (default 0.3).

        Returns:
            List of proposed constraints with confidence scores.
        """
        if not document.strip():
            return []

        from sponsio.generation.llm_extraction import UnifiedExtractor

        try:
            extractor = UnifiedExtractor(
                model=self._model,
                api_key=self._api_key,
                client=self._client,
                provider=self._provider,
                base_url=self._base_url,
            )
        except ImportError as e:
            logger.error("LLM provider SDK not installed: %s", e)
            return []

        results = extractor.extract_from_document(
            document=document,
            tool_inventory=tool_inventory,
            min_confidence=min_confidence,
        )

        # Convert ExtractionResults to ProposedConstraints
        proposals: list[ProposedConstraint] = []
        for r in results:
            if not r.ok:
                logger.warning(
                    "Document constraint failed: %s — %s",
                    r.nl_description,
                    r.error,
                )
                continue

            proposal = ProposedConstraint(
                source=DiscoverySource.AUTO_EXTRACTED,
                extractor="document",
                confidence=r.confidence,
                status=ConstraintStatus.PROPOSED,
                provenance=r.source_quote,
                nl_description=r.nl_description,
                evidence={
                    "source_quote": r.source_quote,
                    "model": self._model,
                    "pattern": r.pattern_name,
                    "args": r.args,
                },
            )

            if r.constraint_type == "det":
                proposal.formula = r.compiled
            else:
                proposal.sto = r.compiled

            proposals.append(proposal)

        return proposals

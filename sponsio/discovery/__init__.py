"""Automatic contract discovery — extract constraints from documents, traces, and code."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from sponsio.discovery._types import (
    ConstraintStatus,
    DiscoverySource,
    ProposedConstraint,
)

# ``PatternStore`` is the cross-customer aggregate persistence layer
# and lives in Sponsio Cloud. The OSS package keeps the import
# best-effort so single-project use of ``discover()`` keeps working;
# any cloud caller passing a real ``PatternStore`` instance keeps
# working when the cloud package is also installed.
try:  # pragma: no cover - guard against the cloud module being absent
    from sponsio.discovery.store import PatternStore  # type: ignore[import-not-found]
except ImportError:
    PatternStore = None  # type: ignore[assignment,misc]

__all__ = [
    "ConstraintStatus",
    "DiscoverySource",
    "ProposedConstraint",
    "PatternStore",
    "discover",
]


def discover(
    documents: Optional[list[str]] = None,
    document_files: Optional[list[Union[str, Path]]] = None,
    traces: Optional[list] = None,
    trace_files: Optional[list[Union[str, Path]]] = None,
    code_paths: Optional[list[Union[str, Path]]] = None,
    store: Optional[PatternStore] = None,
    api_key: Optional[str] = None,
    confidence_threshold: float = 0.95,
    min_support: int = 5,
    validate: bool = True,
) -> list[ProposedConstraint]:
    """Run automatic contract discovery from all available sources.

    Each source is optional. Accepts both in-memory objects and file paths.

    Args:
        documents: Policy texts as strings (Phase 1).
        document_files: Paths to policy files — .txt, .md, .pdf (Phase 1).
        traces: Trace objects for mining (Phase 2).
        trace_files: Paths to trace JSON files or glob patterns
            like ``"traces/*.json"`` (Phase 2).
        code_paths: Python source files, directories, or glob patterns
            like ``"agents/*.py"`` (Phase 3).
        store: PatternStore to import proposals into.
        api_key: OpenAI API key for document extraction.
        confidence_threshold: Minimum confidence for trace mining.
        min_support: Minimum trace support for mining patterns.
        validate: Whether to run the validation pipeline.

    Returns:
        All proposed constraints (both valid and invalid).

    Examples::

        # From files
        proposals = discover(
            document_files=["policy.md", "compliance.pdf"],
            trace_files=["traces/*.json"],
            code_paths=["agents/"],
        )

        # From memory
        proposals = discover(
            documents=["All refunds require policy check."],
            traces=historical_traces,
        )

        # Mixed
        proposals = discover(
            document_files=["policy.md"],
            documents=["Extra rule: max 3 refunds."],
            trace_files=["traces/*.json"],
            code_paths=["agents/"],
            store=PatternStore.default(),
        )
    """
    all_proposals: list[ProposedConstraint] = []

    # --- Resolve file inputs ---
    all_documents = list(documents or [])
    all_traces = list(traces or [])

    if document_files:
        from sponsio.discovery.loaders import load_documents

        all_documents.extend(load_documents(document_files))

    if trace_files:
        from sponsio.discovery.loaders import load_traces

        all_traces.extend(load_traces(trace_files))

    if code_paths:
        from sponsio.discovery.loaders import resolve_code_paths

        code_paths = [str(p) for p in resolve_code_paths(code_paths)]

    # --- Phase 1: Document extraction ---
    if all_documents:
        try:
            from sponsio.discovery.extractors.document import DocumentExtractor

            extractor = DocumentExtractor(api_key=api_key)
            for doc in all_documents:
                all_proposals.extend(extractor.extract(doc))
        except ImportError:
            pass  # openai not installed

    # --- Phase 2: Trace mining ---
    # Cross-trace pattern mining is a Sponsio Cloud feature
    # (`sponsio refresh` is the user-facing entry point for it). In OSS
    # we skip Phase 2 silently when the cloud module is absent so
    # `discover(documents=[...], code_paths=[...])` still works for
    # the single-project case.
    if all_traces:
        try:  # pragma: no cover - guarded import
            from sponsio.discovery.extractors.trace_mining import (  # type: ignore[import-not-found]
                TraceMiner,
            )

            miner = TraceMiner(
                confidence_threshold=confidence_threshold,
                min_support=min_support,
            )
            all_proposals.extend(miner.extract(all_traces))
        except ImportError:
            pass

    # --- Phase 3: Code analysis ---
    if code_paths:
        from sponsio.discovery.extractors.code_analysis import CodeAnalyzer

        analyzer = CodeAnalyzer()
        all_proposals.extend(analyzer.extract(code_paths))

    # --- Validation ---
    # The OSS ValidationPipeline was removed (its triviality and
    # consistency steps had known false-positive / false-negative
    # patterns — see docs/internal/proprietary-validation-pipeline.md).
    # Pre-deploy validation now lives in the proprietary `sponsio-pro`
    # package; OSS users get a minimal trace replay via the
    # `sponsio validate --traces` CLI instead.
    if validate and all_proposals:
        import warnings

        warnings.warn(
            "sponsio.discovery.discover(validate=True) is a no-op in OSS — "
            "the bundled ValidationPipeline was removed because its "
            "triviality and consistency algorithms produced unreliable "
            "results. Install `sponsio-pro` for the replacement pipeline, "
            "or run `sponsio validate --traces` for a basic trace-replay "
            "report. The `validate=` parameter will be dropped in the "
            "next minor release.",
            DeprecationWarning,
            stacklevel=2,
        )

    # --- Import into store ---
    if store:
        store.import_proposed([p for p in all_proposals if p.ok])

    return all_proposals

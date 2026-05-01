"""Contract discovery extractors."""

from sponsio.discovery.extractors.document import DocumentExtractor
from sponsio.discovery.extractors.code_analysis import CodeAnalyzer

# ``TraceMiner`` is the cross-trace mining extractor that backs the
# ``sponsio refresh`` CLI; it's a Sponsio Cloud feature, not bundled
# with the OSS engine. Best-effort import keeps ``from
# sponsio.discovery.extractors import TraceMiner`` working when the
# cloud package is also installed alongside OSS.
try:  # pragma: no cover - guarded import
    from sponsio.discovery.extractors.trace_mining import (  # type: ignore[import-not-found]
        TraceMiner,
    )
except ImportError:
    TraceMiner = None  # type: ignore[assignment,misc]

__all__ = ["DocumentExtractor", "TraceMiner", "CodeAnalyzer"]

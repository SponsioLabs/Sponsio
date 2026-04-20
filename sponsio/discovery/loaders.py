"""File loaders for the three discovery input sources.

Handles loading and converting various file formats into the types
expected by each extractor.

Supported formats:

- **Documents**: ``.txt``, ``.md``, ``.pdf``
- **Traces**: ``.json`` (single trace or array of traces)
- **Code**: ``.py`` files or directories
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from sponsio.models.trace import Trace


# ---------------------------------------------------------------------------
# Document loading
# ---------------------------------------------------------------------------

_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".rst", ".text"}


def load_document(path: Union[str, Path]) -> str:
    """Load a document file and return its text content.

    Supports:
    - ``.txt``, ``.md``, ``.markdown``, ``.rst`` — read as plain text
    - ``.pdf`` — extract text (requires ``PyPDF2`` or ``pdfplumber``)

    Args:
        path: Path to the document file.

    Returns:
        The document text as a string.

    Raises:
        ValueError: If the file format is not supported.
        FileNotFoundError: If the file does not exist.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Document not found: {path}")

    suffix = path.suffix.lower()

    if suffix in _TEXT_EXTENSIONS:
        return path.read_text(encoding="utf-8")

    if suffix == ".pdf":
        return _load_pdf(path)

    raise ValueError(
        f"Unsupported document format: {suffix}. "
        f"Supported: {', '.join(sorted(_TEXT_EXTENSIONS | {'.pdf'}))}"
    )


def load_documents(paths: list[Union[str, Path]]) -> list[str]:
    """Load multiple document files. Returns list of text strings."""
    return [load_document(p) for p in paths]


def _load_pdf(path: Path) -> str:
    """Extract text from a PDF file."""
    # Try pdfplumber first (better extraction quality)
    try:
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
            return "\n\n".join(pages)
    except ImportError:
        pass

    # Fall back to PyPDF2
    try:
        from PyPDF2 import PdfReader

        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages)
    except ImportError:
        raise ImportError(
            "PDF support requires pdfplumber or PyPDF2. "
            "Install with: pip install pdfplumber"
        )


# ---------------------------------------------------------------------------
# Trace loading
# ---------------------------------------------------------------------------


def load_trace(path: Union[str, Path]) -> list[Trace]:
    """Load traces from a JSON file.

    Supports two JSON formats:

    1. Single trace::

        {"metadata": {...}, "events": [...]}

    2. Array of traces::

        [{"metadata": {...}, "events": [...]}, ...]

    Args:
        path: Path to a ``.json`` file.

    Returns:
        A list of Trace objects.

    Raises:
        ValueError: If the file is not valid JSON or not a recognized format.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Trace file not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))

    if isinstance(data, list):
        return [Trace.from_dict(item) for item in data]

    if isinstance(data, dict) and "events" in data:
        return [Trace.from_dict(data)]

    raise ValueError(
        f"Unrecognized trace format in {path}. "
        "Expected a trace object with 'events' key, or an array of trace objects."
    )


def load_traces(paths: list[Union[str, Path]]) -> list[Trace]:
    """Load traces from multiple JSON files. Supports glob patterns.

    Args:
        paths: List of file paths or glob patterns (e.g. ``"traces/*.json"``).

    Returns:
        Flat list of all Trace objects.
    """
    all_traces: list[Trace] = []
    for p in paths:
        path = Path(p)
        # Handle glob patterns
        if "*" in str(p):
            parent = path.parent
            pattern = path.name
            if parent.exists():
                for match in sorted(parent.glob(pattern)):
                    all_traces.extend(load_trace(match))
        else:
            all_traces.extend(load_trace(path))
    return all_traces


# ---------------------------------------------------------------------------
# Code path resolution
# ---------------------------------------------------------------------------


def resolve_code_paths(paths: list[Union[str, Path]]) -> list[Path]:
    """Resolve code paths to actual ``.py`` files.

    Accepts:
    - Individual ``.py`` files
    - Directories (recursively finds all ``.py`` files)
    - Glob patterns (e.g. ``"agents/*.py"``)

    Args:
        paths: File paths, directories, or glob patterns.

    Returns:
        Sorted list of resolved ``.py`` file paths.
    """
    result: list[Path] = []
    for p in paths:
        path = Path(p)
        if "*" in str(p):
            parent = path.parent
            pattern = path.name
            if parent.exists():
                result.extend(sorted(parent.glob(pattern)))
        elif path.is_dir():
            result.extend(sorted(path.rglob("*.py")))
        elif path.is_file() and path.suffix == ".py":
            result.append(path)
    return result

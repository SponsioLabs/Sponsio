"""File loaders for the three discovery input sources.

Handles loading and converting various file formats into the types
expected by each extractor.

Supported formats:

- **Documents**: ``.txt``, ``.md``, ``.pdf``
- **Traces**: ``.json`` / ``.jsonl`` — native Sponsio, OTLP/JSON, or
  Sponsio session event streams.  Format is sniffed from content,
  not extension, so a ``.log`` file of OTLP spans still loads.
- **Code**: ``.py`` files or directories
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Union

from sponsio._paths import safe_resolve
from sponsio.models.trace import Event, Trace


def _check_safe(path: Path, safe_root: Path | str | None) -> Path:
    """Confine ``path`` under ``safe_root`` if one was supplied.

    Returns the resolved :class:`Path`. Raises
    :class:`~sponsio._paths.PathEscapeError` on escape. Pass
    ``safe_root=None`` (the default at every CLI call site) for
    backward-compatible "trust the caller" behavior.
    """
    if safe_root is None:
        return path
    return safe_resolve(path, safe_root=Path(safe_root))


# Directory names a code scan should always skip. Without this filter
# ``sponsio scan my-project/`` recurses into ``.venv`` / ``node_modules``
# and tries to parse the entire world (reported: 11k .py files on a
# trivial fresh project). Keep the set conservative — dependency
# trees, build artifacts, and VCS metadata only.
_SCAN_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        ".venv",
        "venv",
        ".env",
        "env",
        "node_modules",
        "__pycache__",
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "site-packages",
        ".ipynb_checkpoints",
        ".next",
        ".turbo",
        "target",  # Rust / Maven
    }
)


def _is_excluded(path: Path) -> bool:
    """True if any path component matches a well-known dependency /
    build / VCS directory. Works on relative and absolute paths.
    """
    return any(part in _SCAN_EXCLUDE_DIRS for part in path.parts)


def iter_python_files(root: Path) -> list[Path]:
    """Recursively collect ``.py`` files under ``root`` while skipping
    dependency and build directories.

    Shared by :func:`resolve_code_paths`, the AST scanner, and
    ``sponsio doctor`` so excludes stay in lockstep — previously each
    site had its own partial filter (or none) and the inconsistency
    leaked ``.venv``-sourced "tools" into generated sponsio.yaml.
    """
    return [p for p in root.rglob("*.py") if not _is_excluded(p)]


# ---------------------------------------------------------------------------
# Document loading
# ---------------------------------------------------------------------------

_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".rst", ".text"}


def load_document(
    path: Union[str, Path],
    *,
    safe_root: Union[str, Path, None] = None,
) -> str:
    """Load a document file and return its text content.

    Supports:
    - ``.txt``, ``.md``, ``.markdown``, ``.rst`` — read as plain text
    - ``.pdf`` — extract text (requires ``PyPDF2`` or ``pdfplumber``)

    Args:
        path: Path to the document file.
        safe_root: Optional containment root. When supplied, the
            resolved path **must** be a descendant of (or equal to)
            ``safe_root`` — any ``..`` traversal raises
            :class:`~sponsio._paths.PathEscapeError`. Defaults to
            ``None`` (no containment) for backward-compatible CLI use;
            pass it from API / server code where ``path`` may originate
            from a network client.

    Returns:
        The document text as a string.

    Raises:
        ValueError: If the file format is not supported.
        FileNotFoundError: If the file does not exist.
        PathEscapeError: If ``safe_root`` is set and ``path`` escapes.
    """
    path = Path(path)
    path = _check_safe(path, safe_root)
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


def _is_otlp_payload(data: Any) -> bool:
    """True if ``data`` is the top-level OTLP/JSON shape (``resourceSpans``)."""
    return isinstance(data, dict) and isinstance(data.get("resourceSpans"), list)


def _is_native_trace(data: Any) -> bool:
    """True if ``data`` is a Sponsio native trace dict (``{"events": [...]}`)."""
    return isinstance(data, dict) and "events" in data


def _looks_like_event(data: Any) -> bool:
    """True if ``data`` looks like a single Sponsio Event dict.

    A native Event line (the shape produced by ``Trace.to_dict()`` for
    each event) carries the two mandatory fields ``ts`` + ``type``.
    """
    return (
        isinstance(data, dict)
        and "ts" in data
        and "type" in data
        and "resourceSpans" not in data
        and "events" not in data
    )


def _looks_like_session_log(data: Any) -> bool:
    """True if ``data`` is a :class:`SessionLogger`-style ``MonitorEvent``.

    Sponsio's runtime logger writes per-decision records — not raw
    Events — to ``~/.sponsio/sessions/<agent_id>/*.jsonl``.  Each
    record looks like::

        {"ts": ..., "agent_id": "bot", "action": "search_web",
         "pipeline": "det", "constraint": "c1",
         "result": {"action": "allow", "message": "..."}}

    We sniff on the **combination** of ``action`` + ``pipeline`` (no
    other supported shape carries both) to avoid false positives on
    OpenAI tool-call events that happen to use ``action`` for some
    other purpose.
    """
    return (
        isinstance(data, dict)
        and "ts" in data
        and "action" in data
        and "pipeline" in data
        and "type" not in data
        and "events" not in data
        and "resourceSpans" not in data
    )


def _dict_to_trace(data: dict) -> Trace:
    """Dispatch a single JSON-decoded dict to the right adapter.

    Recognises four shapes — the two batch shapes (``resourceSpans``
    OTLP, native ``events``) and the two per-event shapes
    (native Event, session-log MonitorEvent), the latter wrapped in
    a single-event Trace so a one-record JSONL file round-trips.
    """
    if _is_otlp_payload(data):
        # Deferred import — OTLP path only touches this module when
        # users actually pass OTLP files, so the minimal pip install
        # doesn't drag in the tracer package.
        from sponsio.tracer.otel_consumer import otel_to_trace

        return otel_to_trace(data)
    if _is_native_trace(data):
        return Trace.from_dict(data)
    if _looks_like_event(data):
        return Trace(events=[_event_from_dict(data)])
    if _looks_like_session_log(data):
        return Trace(events=[_event_from_session_log(data)])
    raise ValueError(
        "Unrecognized trace shape — expected a native Sponsio trace "
        "(``events`` key), an OTLP/JSON payload (``resourceSpans`` key), "
        "or a per-event record (``ts`` + ``type``, or ``ts`` + ``action`` "
        "+ ``pipeline`` for session-log records)."
    )


def _emit_drop_warning(path: Path, kind: str, dropped: int, kept: int) -> None:
    """Write a one-line warning to stderr about silently filtered JSONL records.

    Trace-mining is statistical, so a quietly-shrunken event count
    weakens contracts in ways that are hard to debug.  We surface
    drops so users can re-export or pre-filter their files instead
    of wondering why proposals look weak.
    """
    if dropped <= 0:
        return
    import sys

    print(
        f"[sponsio] warning: {path} — {kind} mode kept {kept} line(s), "
        f"dropped {dropped} non-matching line(s)",
        file=sys.stderr,
    )


def _load_jsonl(path: Path) -> list[Trace]:
    """Load a JSONL file as one or more traces.

    Sniffs the first non-empty line to pick a mode:

    * Line has ``events`` → each line = one native trace.
    * Line has ``resourceSpans`` → each line = an OTLP batch; we
      merge them all into one synthetic OTLP payload and emit one trace.
    * Line looks like a native ``Event`` (``ts`` + ``type``) →
      aggregate **all** matching lines into one trace.
    * Line looks like a :class:`SessionLogger` ``MonitorEvent``
      (``ts`` + ``action`` + ``pipeline``) → translate each record
      into a synthetic ``tool_call`` Event so trace-mining can run
      directly against ``~/.sponsio/sessions/<agent>/*.jsonl``.

    When subsequent lines disagree with the first-line shape they are
    silently dropped from the same Trace, but we emit a one-line
    stderr warning so the count surfaces — silently shrinking a
    statistical sample is a footgun for downstream miners.
    """
    lines: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Invalid JSONL in {path} — line could not be parsed: {e.msg}"
            ) from e
        if isinstance(obj, dict):
            lines.append(obj)

    if not lines:
        return []

    first = lines[0]
    if _is_native_trace(first):
        kept = [obj for obj in lines if _is_native_trace(obj)]
        _emit_drop_warning(path, "native-trace", len(lines) - len(kept), len(kept))
        return [Trace.from_dict(obj) for obj in kept]

    if _is_otlp_payload(first):
        merged: list[dict] = []
        kept_count = 0
        for obj in lines:
            if _is_otlp_payload(obj):
                merged.extend(obj["resourceSpans"])
                kept_count += 1
        _emit_drop_warning(path, "OTLP", len(lines) - kept_count, kept_count)
        from sponsio.tracer.otel_consumer import otel_to_trace

        return [otel_to_trace({"resourceSpans": merged})]

    if _looks_like_event(first):
        # Native Event-per-line: collapse into a single Trace.
        kept = [obj for obj in lines if _looks_like_event(obj)]
        _emit_drop_warning(path, "Event", len(lines) - len(kept), len(kept))
        events = [_event_from_dict(obj) for obj in kept]
        return [Trace(events=events)]

    if _looks_like_session_log(first):
        # SessionLogger output — translate MonitorEvent records into
        # synthetic tool_call Events so TraceMiner can use them.
        kept = [obj for obj in lines if _looks_like_session_log(obj)]
        _emit_drop_warning(path, "session-log", len(lines) - len(kept), len(kept))
        events = [_event_from_session_log(obj) for obj in kept]
        return [Trace(events=events)]

    raise ValueError(
        f"Unrecognized JSONL trace format in {path} — first line must be a "
        "native trace dict, an OTLP batch, a native Event, or a Sponsio "
        "session-log MonitorEvent record."
    )


def _event_from_dict(data: dict) -> Event:
    """Construct an Event from its native dict form (Event-per-line JSONL).

    Mirrors :meth:`Trace.from_dict` per-event handling so per-line
    JSONL and native trace JSON stay in sync.
    """
    return Event(
        ts=data["ts"],
        agent=data.get("agent", "agent"),
        event_type=data["type"],
        tool=data.get("tool"),
        key=data.get("key"),
        contains=data.get("contains"),
        to=data.get("to"),
        args=data.get("args"),
        content=data.get("content"),
    )


def _event_from_session_log(data: dict) -> Event:
    """Translate a :class:`SessionLogger` ``MonitorEvent`` line into an Event.

    The session log records *runtime decisions* (action, pipeline,
    constraint, result) rather than raw tool calls.  For trace-mining
    purposes the most useful projection is "the agent took action X" —
    so we synthesise a ``tool_call`` Event where ``tool`` is the
    decision's ``action`` field, surfacing the constraint name and
    decision result on ``args`` for downstream patterns that care.

    Records whose ``result.action`` is ``"deny"`` / ``"block"`` are
    still translated, since trace-mining benefits from seeing the
    pattern of *attempted* tool calls.  Callers that want to
    pre-filter blocked attempts can do so on ``ev.args["decision"]``.
    """
    result = data.get("result") or {}
    args = {
        "constraint": data.get("constraint"),
        "pipeline": data.get("pipeline"),
        "decision": result.get("action") if isinstance(result, dict) else None,
    }
    args = {k: v for k, v in args.items() if v is not None}

    return Event(
        ts=data["ts"],
        agent=data.get("agent_id") or data.get("agent", "agent"),
        event_type="tool_call",
        tool=data.get("action"),
        args=args or None,
        content=result.get("message") if isinstance(result, dict) else None,
    )


_TRACE_FILE_SUFFIXES = (".json", ".jsonl", ".ndjson")


def _files_in_directory(path: Path) -> list[Path]:
    """Enumerate trace-shaped files inside ``path`` (one level, sorted).

    Recursing would be surprising for ``-t traces/`` since a code repo
    typically has unrelated JSON living at deeper levels (fixtures,
    package manifests, etc.).  Users who want recursion can ask for
    it explicitly with ``traces/**/*.jsonl``.
    """
    return sorted(
        p
        for p in path.iterdir()
        if p.is_file() and p.suffix.lower() in _TRACE_FILE_SUFFIXES
    )


def load_trace(
    path: Union[str, Path],
    *,
    safe_root: Union[str, Path, None] = None,
) -> list[Trace]:
    """Load traces from a single file or directory.

    Supports four formats, sniffed from content:

    1. **Native Sponsio JSON** — one object with ``events``::

           {"metadata": {...}, "events": [...]}

    2. **Array of native traces** — a top-level JSON list.

    3. **OTLP/JSON** — top-level ``resourceSpans`` (the format emitted
       by the OpenTelemetry Collector, Phoenix, Langfuse, etc.).

    4. **JSONL** — line-delimited form of any of the above, **or** a
       Sponsio session log (``~/.sponsio/sessions/<agent>/*.jsonl``).
       Both per-line ``Event`` shape (``ts`` + ``type``) and the
       runtime ``MonitorEvent`` shape produced by :class:`SessionLogger`
       (``ts`` + ``action`` + ``pipeline``) are recognised.

    Args:
        path: Path to the trace file or a directory containing trace
            files.  ``~`` is expanded.  When a directory is given, all
            ``.json`` / ``.jsonl`` / ``.ndjson`` files at the top level
            are loaded (use a glob like ``dir/**/*.jsonl`` for deep
            recursion).  Extension does not matter for the sniffer —
            content drives detection.

    Returns:
        A list of :class:`Trace` objects — most files yield exactly
        one, but native arrays, native-trace JSONL, and directory
        inputs can yield many.

    Raises:
        FileNotFoundError: If the path does not exist after ``~``
            expansion.
        ValueError: If the file is neither valid JSON nor parseable
            JSONL in one of the supported shapes, or a directory
            input contains no readable trace files.
    """
    path = Path(path).expanduser()
    path = _check_safe(path, safe_root)
    if not path.exists():
        raise FileNotFoundError(f"Trace file not found: {path}")

    if path.is_dir():
        files = _files_in_directory(path)
        if not files:
            raise ValueError(
                f"No trace files found in directory {path} "
                f"(looked for {', '.join(_TRACE_FILE_SUFFIXES)})."
            )
        traces: list[Trace] = []
        for f in files:
            traces.extend(load_trace(f))
        return traces

    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return _load_jsonl(path)

    if isinstance(data, list):
        out: list[Trace] = []
        for item in data:
            if isinstance(item, dict):
                out.append(_dict_to_trace(item))
        if not out:
            raise ValueError(f"Trace array in {path} had no recognizable entries.")
        return out

    if isinstance(data, dict):
        return [_dict_to_trace(data)]

    raise ValueError(
        f"Unrecognized trace format in {path}. Expected a native trace, an "
        "OTLP/JSON payload, or a JSONL file of either."
    )


def _expand_glob(path_str: str) -> list[Path]:
    """Expand a path that may contain ``*`` / ``?`` / ``**``.

    Uses :meth:`Path.glob` rooted at the first non-glob parent so
    patterns like ``sessions/**/*.jsonl`` work without needing
    ``glob.glob``.  ``~`` is expanded before splitting.  A missing
    parent returns ``[]`` rather than raising; the caller turns that
    into a friendly "0 traces found" message rather than a stack
    trace.
    """
    path = Path(path_str).expanduser()
    parts = path.parts
    split = 0
    for i, part in enumerate(parts):
        if any(ch in part for ch in "*?["):
            split = i
            break
    parent = Path(*parts[:split]) if split else Path(".")
    pattern = str(Path(*parts[split:]))
    if not parent.exists():
        return []
    return sorted(parent.glob(pattern))


def load_traces(paths: list[Union[str, Path]]) -> list[Trace]:
    """Load traces from multiple files, directories, or glob patterns.

    Args:
        paths: File paths, directory paths, or glob patterns
            (e.g. ``"traces/*.json"``, ``"traces/"``,
            ``"~/.sponsio/sessions/bot/*.jsonl"``, or a mix).
            ``~`` is expanded in every form.

    Returns:
        Flat list of all :class:`Trace` objects across every input.

    Raises:
        FileNotFoundError: If a non-glob, non-directory path doesn't exist.
        ValueError: If any file fails to parse in a recognized format.
    """
    all_traces: list[Trace] = []
    for p in paths:
        path_str = str(p)
        if any(ch in path_str for ch in "*?["):
            matches = _expand_glob(path_str)
            for match in matches:
                all_traces.extend(load_trace(match))
        else:
            all_traces.extend(load_trace(path_str))
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
            result.extend(sorted(iter_python_files(path)))
        elif path.is_file() and path.suffix == ".py":
            result.append(path)
    return result

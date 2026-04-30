"""Built-in OTel exporters for Sponsio span trees.

Three implementations, all matching the duck-typed ``export(span)``
protocol that :class:`sponsio.integrations.base.BaseGuard` calls after
each ``check_action``:

1. :class:`JsonlFileExporter` — append OTLP-per-line to a local file.
   Stdlib only. Use case: offline trace corpus for ``sponsio eval``,
   audit log staging, feeding a custom backend via tail-and-ship.

2. :class:`OtlpHttpExporter` — async POST to an OTLP/HTTP collector
   (Datadog / Honeycomb / Grafana Cloud / your own endpoint). Stdlib
   only. Background worker thread, bounded queue, batching, retry
   with exponential backoff. The hot path (``check_action``) only
   does a non-blocking ``queue.put_nowait`` so the agent never
   stalls on a slow / down collector.

3. :class:`MultiExporter` — fan out to N downstream exporters with
   isolated failure (one exporter throwing doesn't poison the others).

All three forward through :func:`sponsio.tracer.otel_writer.span_tree_to_otlp`,
so the on-the-wire shape is the same Sponsio Semantic Conventions
schema documented in ``docs/observability.md``.

Usage::

    from sponsio import Sponsio
    from sponsio.tracer.exporters import OtlpHttpExporter

    guard = Sponsio(
        agent_id="bot",
        contracts=[...],
        otel_exporter=OtlpHttpExporter(
            endpoint="https://otlp.your-vendor.com/v1/traces",
            headers={"x-api-key": os.environ["OTEL_API_KEY"]},
            host="cursor",
        ),
    )

The ``host=`` / ``conversation_id_fn=`` / ``redact_args=`` /
``truncate=`` kwargs forward through to ``span_tree_to_otlp`` so a
single exporter instance can stamp consistent metadata on every
turn without the user re-specifying it per call.
"""

from __future__ import annotations

import atexit
import json
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from sponsio.tracer.otel_writer import span_tree_to_otlp


class _BaseExporter:
    """Mixin for shape-conversion + per-turn metadata stamping.

    Subclasses get ``self._to_otlp(span)`` as their single conversion
    entry point. The constructor stores keyword args that forward
    verbatim to :func:`span_tree_to_otlp`, plus optional callables that
    let the user derive ``conversation_id`` from the span (e.g. by
    reading a thread-local set in their integration) without having
    to pass it on every export.
    """

    def __init__(
        self,
        *,
        host: str | None = None,
        conversation_id: str | None = None,
        conversation_id_fn: Callable[[Any], str | None] | None = None,
        service_name: str | None = None,
        redact_args: bool = True,
        truncate: bool = True,
    ) -> None:
        self._host = host
        self._conversation_id = conversation_id
        self._conversation_id_fn = conversation_id_fn
        self._service_name = service_name
        self._redact_args = redact_args
        self._truncate = truncate

    def _to_otlp(self, span: Any) -> dict:
        """Render a span tree to OTLP using the exporter's defaults.

        The dynamic ``conversation_id_fn`` (if provided) wins over the
        static ``conversation_id`` so per-turn ids can flow through —
        useful when one exporter instance serves many concurrent
        conversations.
        """
        conv_id = self._conversation_id
        if self._conversation_id_fn is not None:
            try:
                dynamic = self._conversation_id_fn(span)
                if dynamic:
                    conv_id = dynamic
            except Exception as exc:  # pragma: no cover - user code
                print(
                    f"[sponsio] conversation_id_fn raised: {exc}",
                    file=sys.stderr,
                )

        return span_tree_to_otlp(
            span,
            host=self._host,
            conversation_id=conv_id,
            event_tool=getattr(span, "action", None),
            service_name=self._service_name,
            redact_args=self._redact_args,
            truncate=self._truncate,
        )


# ---------------------------------------------------------------------------
# JSONL file exporter — synchronous, stdlib only
# ---------------------------------------------------------------------------


class JsonlFileExporter(_BaseExporter):
    """Append one OTLP-encoded line per turn to a local file.

    Synchronous (writes inside ``export()``), stdlib only. A single
    JSONL file is the simplest possible audit substrate — every
    line is independently parseable, no schema migration ever
    breaks tail-and-ship pipelines, and ``sponsio eval`` can replay
    it verbatim.

    Args:
        path: File path. Created with parents on first write.
        rotate_bytes: Optional rotation threshold. When the current
            file exceeds this size, it's renamed with a ``.<seq>``
            suffix and a fresh file is started. ``None`` = never
            rotate.
        host / conversation_id / redact_args / truncate: forward to
            :func:`span_tree_to_otlp`.

    Thread-safety: uses a lock around ``write`` so concurrent
    ``check_action`` calls don't tear lines. The lock is held only
    for the duration of one append, so a slow disk doesn't bottleneck
    the whole monitor.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        rotate_bytes: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._path = Path(path).expanduser()
        self._rotate_bytes = rotate_bytes
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def export(self, span: Any) -> None:
        """Append one line. Errors are logged to stderr, not raised —
        the agent's hot path must never break on a failed export."""
        try:
            otlp = self._to_otlp(span)
            line = json.dumps(otlp, separators=(",", ":")) + "\n"
        except Exception as exc:
            print(f"[sponsio] JsonlFileExporter encode failed: {exc}", file=sys.stderr)
            return

        with self._lock:
            try:
                if (
                    self._rotate_bytes is not None
                    and self._path.exists()
                    and self._path.stat().st_size >= self._rotate_bytes
                ):
                    self._rotate()
                with open(self._path, "a", encoding="utf-8") as fh:
                    fh.write(line)
            except OSError as exc:
                print(
                    f"[sponsio] JsonlFileExporter write failed: {exc}", file=sys.stderr
                )

    def _rotate(self) -> None:
        seq = 1
        while True:
            target = self._path.with_suffix(self._path.suffix + f".{seq}")
            if not target.exists():
                self._path.rename(target)
                return
            seq += 1


# ---------------------------------------------------------------------------
# OTLP/HTTP exporter — async, batched, retried
# ---------------------------------------------------------------------------


class OtlpHttpExporter(_BaseExporter):
    """Async push to an OTLP/HTTP collector with batching + retry.

    Construction starts a daemon worker thread that drains a bounded
    queue and POSTs batches as JSON to ``endpoint``. The hot path
    (``export()``) is non-blocking — full queue means drop-oldest
    with a stderr warning, never agent stall.

    The export protocol: ``application/json`` POST of the OTLP/HTTP
    envelope (``{"resourceSpans": [...]}``). This is what every
    OTel collector and observability vendor supports out of the box.

    Args:
        endpoint: OTLP/HTTP endpoint URL (typically ending in
            ``/v1/traces``).
        headers: Extra HTTP headers (auth keys, tenant ids, …). The
            ``Content-Type`` header is set automatically.
        batch_size: Spans batched per POST. Larger = fewer round trips
            but each retry replays a bigger payload. 50 is a sane
            default for most deployments.
        flush_interval_s: Even if the batch isn't full, flush after
            this many seconds so low-traffic agents don't sit on
            spans for minutes. 2.0s is short enough to feel "live"
            on dashboards without spamming the collector.
        max_queue: Bounded queue size. Beyond this, drop-oldest fires
            and a counter is exposed via :attr:`dropped`.
        max_retries: Per-batch retry attempts before giving up. With
            exponential backoff (0.5s, 1s, 2s, …, capped at 5s) so
            a transient outage doesn't stampede the collector.
        timeout_s: Per-request HTTP timeout.
        on_drop: Optional callback fired when the queue overflows.
            Useful for paging an SRE or bumping a Prometheus counter.
        host / conversation_id / conversation_id_fn / service_name /
        redact_args / truncate: forward to :func:`span_tree_to_otlp`.

    Lifecycle: the worker is a daemon thread, so a normal Python exit
    won't hang. We register :meth:`close` via ``atexit`` so the last
    in-flight batch gets flushed (with timeout). For long-lived
    services that need explicit shutdown, call ``close()``.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        headers: dict[str, str] | None = None,
        batch_size: int = 50,
        flush_interval_s: float = 2.0,
        max_queue: int = 1000,
        max_retries: int = 3,
        timeout_s: float = 5.0,
        on_drop: Callable[[int], None] | None = None,
        **convert_kwargs: Any,
    ) -> None:
        super().__init__(**convert_kwargs)
        self.endpoint = endpoint
        self.headers = {"Content-Type": "application/json"}
        if headers:
            self.headers.update(headers)
        self.batch_size = batch_size
        self.flush_interval_s = flush_interval_s
        self.max_retries = max_retries
        self.timeout_s = timeout_s
        self._on_drop = on_drop
        self._q: queue.Queue[dict] = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._dropped = 0
        self._dropped_lock = threading.Lock()
        self._worker = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="sponsio-otlp-exporter",
        )
        self._worker.start()
        atexit.register(self.close)

    @property
    def dropped(self) -> int:
        """Total spans dropped due to queue saturation since startup."""
        with self._dropped_lock:
            return self._dropped

    def export(self, span: Any) -> None:
        """Enqueue a span for async export. Non-blocking on the hot
        path; oldest in flight gets dropped if the queue is full."""
        try:
            otlp = self._to_otlp(span)
        except Exception as exc:
            print(f"[sponsio] OtlpHttpExporter encode failed: {exc}", file=sys.stderr)
            return

        try:
            self._q.put_nowait(otlp)
            return
        except queue.Full:
            pass

        # Queue full — drop oldest, then enqueue. Newest data is
        # most actionable for live dashboards / on-call audit, so
        # we sacrifice the trailing edge rather than the leading edge.
        try:
            self._q.get_nowait()
            with self._dropped_lock:
                self._dropped += 1
            if self._on_drop is not None:
                try:
                    self._on_drop(1)
                except Exception:
                    pass
        except queue.Empty:
            pass

        try:
            self._q.put_nowait(otlp)
        except queue.Full:
            with self._dropped_lock:
                self._dropped += 1

    def close(self, timeout_s: float = 5.0) -> None:
        """Stop the worker and flush any pending batch.

        Idempotent. After ``close()`` the exporter accepts further
        ``export()`` calls but they're queued without ever being
        flushed (the worker is gone) — operators should not call
        ``export()`` after ``close()`` in practice.
        """
        if self._stop.is_set():
            return
        self._stop.set()
        # Wake the worker if it's blocked on q.get() with a sentinel
        # — None bypasses the JSON encode path in _post_with_retry's
        # batch merge.
        try:
            self._q.put_nowait({"_sentinel": True})
        except queue.Full:
            pass
        self._worker.join(timeout=timeout_s)

    def _worker_loop(self) -> None:
        """Drain the queue, batch-flush on size or timeout."""
        batch: list[dict] = []
        deadline = time.monotonic() + self.flush_interval_s

        while True:
            now = time.monotonic()
            wait = max(0.01, deadline - now)
            try:
                otlp = self._q.get(timeout=wait)
                if "_sentinel" in otlp:
                    # Final flush requested; drain anything remaining
                    # then break.
                    while True:
                        try:
                            extra = self._q.get_nowait()
                            if "_sentinel" not in extra:
                                batch.append(extra)
                        except queue.Empty:
                            break
                    if batch:
                        self._post_with_retry(batch)
                    return
                batch.append(otlp)
            except queue.Empty:
                pass

            now = time.monotonic()
            should_flush = (
                len(batch) >= self.batch_size
                or (batch and now >= deadline)
                or (self._stop.is_set() and batch)
            )
            if should_flush:
                self._post_with_retry(batch)
                batch = []
                deadline = now + self.flush_interval_s

            if self._stop.is_set() and self._q.empty():
                if batch:
                    self._post_with_retry(batch)
                return

    @staticmethod
    def _merge_batch(batch: list[dict]) -> dict | None:
        """Merge multiple per-turn OTLP envelopes into one, amortising
        per-POST overhead. Each input is ``{"resourceSpans": [...]}``;
        we concatenate the inner lists into a single envelope."""
        if not batch:
            return None
        if len(batch) == 1:
            return batch[0]
        merged: list = []
        for otlp in batch:
            merged.extend(otlp.get("resourceSpans", []))
        return {"resourceSpans": merged}

    def _post_with_retry(self, batch: list[dict]) -> None:
        payload = self._merge_batch(batch)
        if payload is None:
            return
        try:
            body = json.dumps(payload).encode("utf-8")
        except Exception as exc:
            print(f"[sponsio] OTLP batch encode failed: {exc}", file=sys.stderr)
            return

        for attempt in range(self.max_retries + 1):
            try:
                req = urllib.request.Request(
                    self.endpoint,
                    data=body,
                    headers=self.headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    if 200 <= resp.status < 300:
                        return
                    # Non-2xx but no exception — server-side rejection.
                    if attempt < self.max_retries:
                        time.sleep(min(0.5 * (2**attempt), 5.0))
                        continue
                    print(
                        f"[sponsio] OTLP collector returned {resp.status}; "
                        f"giving up on {len(batch)} spans",
                        file=sys.stderr,
                    )
                    return
            except (urllib.error.URLError, OSError) as exc:
                if attempt < self.max_retries:
                    time.sleep(min(0.5 * (2**attempt), 5.0))
                    continue
                print(
                    f"[sponsio] OTLP export gave up after {attempt + 1} tries: {exc}",
                    file=sys.stderr,
                )
                return


# ---------------------------------------------------------------------------
# Multi-exporter — fan out
# ---------------------------------------------------------------------------


class MultiExporter:
    """Fan out span exports to multiple downstream exporters.

    Useful when the same spans need to land in two places — e.g.
    a JSONL audit log on disk plus a real-time push to your
    observability vendor. Each downstream's failure is isolated;
    a panicking exporter doesn't poison its siblings.

    The :meth:`close` method best-efforts a graceful shutdown of every
    downstream that exposes one (e.g. :class:`OtlpHttpExporter`).
    """

    def __init__(self, *exporters: Any) -> None:
        self._exporters = list(exporters)

    def export(self, span: Any) -> None:
        for exp in self._exporters:
            try:
                exp.export(span)
            except Exception as exc:
                print(
                    f"[sponsio] {type(exp).__name__}.export failed: {exc}",
                    file=sys.stderr,
                )

    def close(self, timeout_s: float = 5.0) -> None:
        for exp in self._exporters:
            close_fn = getattr(exp, "close", None)
            if not callable(close_fn):
                continue
            try:
                # Some exporters take a timeout, others don't. Try
                # both signatures so we don't hard-couple to one shape.
                try:
                    close_fn(timeout_s=timeout_s)
                except TypeError:
                    close_fn()
            except Exception as exc:
                print(
                    f"[sponsio] {type(exp).__name__}.close failed: {exc}",
                    file=sys.stderr,
                )

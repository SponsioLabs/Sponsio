"""Tests for the in-tree OTel exporters.

Three exporters, three suites:

- :class:`JsonlFileExporter` — verify lines are appended verbatim and
  rotation fires at the configured byte threshold.
- :class:`OtlpHttpExporter` — stub the HTTP layer with a counter,
  verify batching / retry / drop-oldest-on-overflow.
- :class:`MultiExporter` — verify failure isolation.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error


from sponsio.models.spans import AgentTurnSpan, ContractCheckSpan, GuaranteeSpan
from sponsio.tracer import semconv
from sponsio.tracer.exporters import (
    JsonlFileExporter,
    MultiExporter,
    OtlpHttpExporter,
)


def _make_span(blocked: bool = False) -> AgentTurnSpan:
    """Build a minimal valid span tree for export."""
    now = time.monotonic()
    turn = AgentTurnSpan(
        span_type=semconv.SPAN_AGENT_TURN,
        start_time=now,
        end_time=now + 0.001,
        status="violated" if blocked else "ok",
        agent_id="bot",
        action="Bash",
        blocked=blocked,
    )
    contract = ContractCheckSpan(
        span_type=semconv.SPAN_CONTRACT_CHECK,
        start_time=now,
        end_time=now + 0.0005,
        status="violated" if blocked else "ok",
        contract_name="test rule",
        pipeline="hard",
    )
    turn.children.append(contract)
    contract.children.append(
        GuaranteeSpan(
            span_type=semconv.SPAN_GUARANTEE,
            start_time=now,
            end_time=now + 0.0002,
            status="violated" if blocked else "ok",
            formula_desc="G(x)",
            result=not blocked,
        )
    )
    return turn


# ---------------------------------------------------------------------------
# JsonlFileExporter
# ---------------------------------------------------------------------------


class TestJsonlFileExporter:
    def test_appends_one_line_per_export(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        exp = JsonlFileExporter(path, host="cursor")
        exp.export(_make_span())
        exp.export(_make_span(blocked=True))

        lines = path.read_text().splitlines()
        assert len(lines) == 2
        # Each line is one full OTLP envelope
        envelope = json.loads(lines[0])
        assert "resourceSpans" in envelope

    def test_rotation_kicks_in_at_threshold(self, tmp_path):
        path = tmp_path / "rot.jsonl"
        # Tiny threshold so a single span exceeds it.
        exp = JsonlFileExporter(path, rotate_bytes=10)
        exp.export(_make_span())
        exp.export(_make_span())
        exp.export(_make_span())

        rotated = sorted(p.name for p in tmp_path.glob("rot.jsonl*"))
        # Either rot.jsonl + rot.jsonl.1 + rot.jsonl.2, or similar.
        assert len(rotated) >= 2
        # The current file must always exist after rotation.
        assert path.exists()

    def test_concurrent_writes_dont_tear_lines(self, tmp_path):
        path = tmp_path / "concurrent.jsonl"
        exp = JsonlFileExporter(path)

        def writer():
            for _ in range(20):
                exp.export(_make_span())

        threads = [threading.Thread(target=writer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        lines = path.read_text().splitlines()
        assert len(lines) == 80
        for ln in lines:
            json.loads(ln)  # each line must be standalone-parseable

    def test_encode_error_does_not_raise(self, tmp_path, capsys):
        """A malformed span shouldn't tank the agent — error to stderr only."""
        path = tmp_path / "bad.jsonl"
        exp = JsonlFileExporter(path)
        # Pass something that triggers AttributeError downstream
        exp.export(object())
        captured = capsys.readouterr()
        assert "encode failed" in captured.err
        # File may or may not exist; key invariant is no exception escaped.


# ---------------------------------------------------------------------------
# OtlpHttpExporter
# ---------------------------------------------------------------------------


class _FakeUrlopen:
    """Replace ``urllib.request.urlopen`` for tests. Records every
    POST + lets us script success / failure / non-2xx outcomes."""

    def __init__(self):
        self.calls: list[bytes] = []
        self.responses: list = []  # each entry: int (status) or Exception
        self.lock = threading.Lock()

    def __call__(self, req, timeout=None):
        with self.lock:
            self.calls.append(req.data)
            entry = self.responses.pop(0) if self.responses else 200
        if isinstance(entry, Exception):
            raise entry
        return _FakeResponse(entry)


class _FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class TestOtlpHttpExporter:
    def test_batches_then_posts(self, monkeypatch):
        fake = _FakeUrlopen()
        monkeypatch.setattr("sponsio.tracer.exporters.urllib.request.urlopen", fake)
        exp = OtlpHttpExporter(
            "http://collector.invalid/v1/traces",
            batch_size=3,
            flush_interval_s=10.0,  # no time-based flush during this test
        )
        try:
            for _ in range(3):
                exp.export(_make_span())
            # Wait for worker to drain
            for _ in range(50):
                if fake.calls:
                    break
                time.sleep(0.02)
        finally:
            exp.close(timeout_s=2.0)

        assert len(fake.calls) >= 1
        envelope = json.loads(fake.calls[0])
        # Three turns merged into one envelope
        assert len(envelope["resourceSpans"]) == 3

    def test_retry_then_succeed(self, monkeypatch):
        fake = _FakeUrlopen()
        # First two attempts fail, third succeeds.
        fake.responses = [
            urllib.error.URLError("connection refused"),
            urllib.error.URLError("connection refused"),
            200,
        ]
        monkeypatch.setattr("sponsio.tracer.exporters.urllib.request.urlopen", fake)
        # Speed retries up so the test isn't slow.
        monkeypatch.setattr("sponsio.tracer.exporters.time.sleep", lambda _s: None)

        exp = OtlpHttpExporter(
            "http://collector.invalid/v1/traces",
            batch_size=1,
            flush_interval_s=10.0,
            max_retries=3,
        )
        try:
            exp.export(_make_span())
            for _ in range(50):
                if len(fake.calls) >= 3:
                    break
                time.sleep(0.02)
        finally:
            exp.close(timeout_s=2.0)
        assert len(fake.calls) == 3

    def test_drop_oldest_on_queue_overflow(self, monkeypatch):
        # Block the worker by making urlopen wait forever — every
        # span queued will pile up.
        block = threading.Event()

        def slow_urlopen(req, timeout=None):
            block.wait(timeout=10)
            return _FakeResponse(200)

        monkeypatch.setattr(
            "sponsio.tracer.exporters.urllib.request.urlopen", slow_urlopen
        )

        exp = OtlpHttpExporter(
            "http://collector.invalid/v1/traces",
            batch_size=1,
            flush_interval_s=0.05,
            max_queue=5,
        )
        try:
            # Push more spans than the queue holds
            for _ in range(20):
                exp.export(_make_span())
            # Worker is blocked, so dropped count must have grown
            time.sleep(0.1)
            assert exp.dropped > 0
        finally:
            block.set()
            exp.close(timeout_s=2.0)

    def test_non_2xx_does_not_silently_succeed(self, monkeypatch):
        fake = _FakeUrlopen()
        # Always return 503; exporter should retry then give up.
        fake.responses = [503, 503, 503, 503]
        monkeypatch.setattr("sponsio.tracer.exporters.urllib.request.urlopen", fake)
        monkeypatch.setattr("sponsio.tracer.exporters.time.sleep", lambda _s: None)

        exp = OtlpHttpExporter(
            "http://collector.invalid/v1/traces",
            batch_size=1,
            flush_interval_s=10.0,
            max_retries=3,
        )
        try:
            exp.export(_make_span())
            for _ in range(50):
                if len(fake.calls) >= 4:
                    break
                time.sleep(0.02)
        finally:
            exp.close(timeout_s=2.0)
        # 1 initial + 3 retries
        assert len(fake.calls) == 4

    def test_close_flushes_pending(self, monkeypatch):
        fake = _FakeUrlopen()
        monkeypatch.setattr("sponsio.tracer.exporters.urllib.request.urlopen", fake)

        exp = OtlpHttpExporter(
            "http://collector.invalid/v1/traces",
            batch_size=100,  # never reached in this test
            flush_interval_s=60.0,  # never reached in this test
        )
        exp.export(_make_span())
        exp.export(_make_span())
        exp.close(timeout_s=2.0)

        # close() must flush rather than dropping pending data — the
        # worker may flush in one or two batches depending on race
        # with the sentinel, but every queued span must reach the
        # collector. The invariant: total ``resourceSpans`` across all
        # POSTs equals the number of exports.
        assert len(fake.calls) >= 1
        total_spans = sum(len(json.loads(body)["resourceSpans"]) for body in fake.calls)
        assert total_spans == 2


# ---------------------------------------------------------------------------
# MultiExporter
# ---------------------------------------------------------------------------


class _CountingExporter:
    def __init__(self, fail: bool = False):
        self.calls = 0
        self.closed = False
        self.fail = fail

    def export(self, span):
        self.calls += 1
        if self.fail:
            raise RuntimeError("induced failure")

    def close(self, timeout_s: float = 5.0):
        self.closed = True


class TestMultiExporter:
    def test_fans_out_to_all(self):
        a, b, c = _CountingExporter(), _CountingExporter(), _CountingExporter()
        m = MultiExporter(a, b, c)
        m.export(_make_span())
        m.export(_make_span())
        assert (a.calls, b.calls, c.calls) == (2, 2, 2)

    def test_one_failure_does_not_poison_siblings(self, capsys):
        bad = _CountingExporter(fail=True)
        good = _CountingExporter()
        m = MultiExporter(bad, good)
        m.export(_make_span())
        # bad raised, good still got the export
        assert good.calls == 1
        out = capsys.readouterr().err
        assert "_CountingExporter.export failed" in out

    def test_close_propagates_to_children(self):
        a, b = _CountingExporter(), _CountingExporter()
        m = MultiExporter(a, b)
        m.close()
        assert a.closed and b.closed

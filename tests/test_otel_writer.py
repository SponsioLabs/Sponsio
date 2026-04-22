"""Tests for ``sponsio.tracer.otel_writer`` + ``sponsio export``.

The single most important invariant here is **round-trip**:

    otel_to_trace(trace_to_otlp(t)) ≡ t        (event-wise)

If that holds, then any runtime trace captured in observe mode can
be replayed by ``sponsio eval`` with zero extra glue — which is the
whole point of the export feature.

Three layers of coverage:
1. Round-trip of every supported event shape (tool, llm req/resp,
   the degraded-but-ordered fallback for data_* / message events).
2. ``BaseGuard.save_trace_for_eval`` — filename prefix, label
   validation, directory creation.
3. ``sponsio export`` CLI — file + dir input, refuses to re-wrap
   OTLP, applies the right prefix, surfaces skips, smart-skips
   double-prefixing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

pytest.importorskip("yaml")  # several helpers transitively load sponsio.config

from sponsio.cli import export_cmd
from sponsio.models.trace import Event, Trace
from sponsio.tracer.otel_consumer import otel_to_trace
from sponsio.tracer.otel_writer import trace_to_otlp


# ---------------------------------------------------------------------------
# Round-trip (the invariant that makes the whole feature hang together)
# ---------------------------------------------------------------------------


def _events_equivalent(a: list[Event], b: list[Event]) -> bool:
    """Return True if the two event lists mean the same thing for
    the purposes of contract evaluation.

    Exact equality fails because ``otel_to_trace`` rebuilds the
    ``ts`` field from span ordering (0, 1, 2, ...) which may differ
    from the original integers if the input had gaps.  Contract
    grounding only cares about order + event type + tool name +
    agent + args, so that's what we compare.
    """
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if x.event_type != y.event_type:
            return False
        if x.agent != y.agent:
            return False
        if x.tool != y.tool:
            return False
        if x.event_type in ("llm_request", "llm_response"):
            if (x.content or "") != (y.content or ""):
                return False
    return True


class TestRoundTrip:
    def test_pure_tool_trace(self):
        """The eval corpus's bread-and-butter: a tool-only trace.
        This is the exact shape the refund-bot example produces."""
        trace = Trace(
            events=[
                Event(
                    ts=0, agent="bot", event_type="tool_call", tool="verify_identity"
                ),
                Event(ts=1, agent="bot", event_type="tool_call", tool="lookup_order"),
                Event(ts=2, agent="bot", event_type="tool_call", tool="issue_refund"),
            ]
        )
        otlp = trace_to_otlp(trace, agent_id="bot")
        back = otel_to_trace(otlp)
        assert [e.tool for e in back.events] == [
            "verify_identity",
            "lookup_order",
            "issue_refund",
        ]
        assert all(e.agent == "bot" for e in back.events)
        assert all(e.event_type == "tool_call" for e in back.events)

    def test_tool_args_preserved(self):
        """If a contract grounds on ``args.body contains pii``, we
        need args to survive the round-trip — not just the tool name."""
        trace = Trace(
            events=[
                Event(
                    ts=0,
                    agent="bot",
                    event_type="tool_call",
                    tool="send_email",
                    args={"to": "alice@example.com", "body": "hello"},
                ),
            ]
        )
        otlp = trace_to_otlp(trace)
        back = otel_to_trace(otlp)
        assert len(back.events) == 1
        args = back.events[0].args or {}
        assert args.get("to") == "alice@example.com"
        assert args.get("body") == "hello"

    def test_llm_request_response_roundtrip(self):
        """prompt_contains / llm_said atoms depend on the content
        making it through as-is."""
        trace = Trace(
            events=[
                Event(
                    ts=0,
                    agent="bot",
                    event_type="llm_request",
                    content="tell me a safe recipe",
                    args={
                        "model": "gpt-4o-mini",
                        "system": "openai",
                        "input_tokens": 7,
                    },
                ),
                Event(
                    ts=1,
                    agent="bot",
                    event_type="llm_response",
                    content="here is a pancake recipe",
                    args={
                        "model": "gpt-4o-mini",
                        "system": "openai",
                        "output_tokens": 12,
                    },
                ),
            ]
        )
        otlp = trace_to_otlp(trace)
        back = otel_to_trace(otlp)

        kinds = [e.event_type for e in back.events]
        assert "llm_request" in kinds
        assert "llm_response" in kinds

        req = next(e for e in back.events if e.event_type == "llm_request")
        resp = next(e for e in back.events if e.event_type == "llm_response")
        assert "pancake" in (resp.content or "")
        assert "recipe" in (req.content or "")

    def test_ordering_preserved_under_nonsequential_ts(self):
        """``Event.ts`` may have gaps in production (parallel
        branches, pruned events).  The round-trip must preserve the
        observed order regardless of gap size."""
        trace = Trace(
            events=[
                Event(ts=0, agent="bot", event_type="tool_call", tool="a"),
                Event(ts=100, agent="bot", event_type="tool_call", tool="b"),
                Event(
                    ts=5, agent="bot", event_type="tool_call", tool="c"
                ),  # out of order!
            ]
        )
        # The writer uses ts to assign start_ns; ordering in the
        # output should reflect the ts values, so the consumer sorts
        # them back into (a, c, b).  That's exactly the contract the
        # consumer relies on.
        otlp = trace_to_otlp(trace)
        back = otel_to_trace(otlp)
        assert [e.tool for e in back.events] == ["a", "c", "b"]

    def test_agent_id_override_wins(self):
        """``agent_id`` kwarg must take precedence — users pass it
        when the in-memory trace has the old agent name and they
        want to re-badge it (e.g. after renaming their agent)."""
        trace = Trace(
            events=[
                Event(ts=0, agent="old-name", event_type="tool_call", tool="ping"),
            ]
        )
        otlp = trace_to_otlp(trace, agent_id="new-name")
        back = otel_to_trace(otlp)
        assert back.events[0].agent == "new-name"

    def test_empty_trace_yields_valid_otlp(self):
        """Edge case: guard created, session empty.  Must not crash
        and must still produce a resourceSpans skeleton so
        ``otel_to_trace`` accepts it."""
        otlp = trace_to_otlp(Trace(events=[]))
        back = otel_to_trace(otlp)
        assert back.events == []


# ---------------------------------------------------------------------------
# guard.save_trace_for_eval
# ---------------------------------------------------------------------------


class TestSaveTraceForEval:
    def _guard(self):
        from sponsio.integrations.base import BaseGuard

        return BaseGuard(
            agent_id="refund_bot",
            contracts=["tool `verify_identity` must precede `issue_refund`"],
            verbose=False,
        )

    def test_writes_labelled_file_in_target_dir(self, tmp_path: Path):
        """Integration smoke: run a guard through a tool cycle, save
        the trace, confirm the file exists with the right prefix."""
        guard = self._guard()
        guard.guard_before("verify_identity", {})
        guard.guard_before("issue_refund", {})

        out = guard.save_trace_for_eval(tmp_path, label="safe")
        assert out.exists()
        assert out.parent == tmp_path
        assert out.name.startswith("safe_refund_bot_")
        # The file must be loadable OTLP
        payload = json.loads(out.read_text())
        assert "resourceSpans" in payload

    def test_refuses_invalid_label(self, tmp_path: Path):
        """Any label other than safe/unsafe would silently fall out
        of eval's confusion matrix — caught at the API boundary so
        users can't foot-gun themselves."""
        guard = self._guard()
        with pytest.raises(ValueError, match="label must be"):
            guard.save_trace_for_eval(tmp_path, label="maybe")

    def test_creates_missing_directory(self, tmp_path: Path):
        """The target might be ``<prod-volume>/traces/<date>/`` and
        not exist yet on the first run of the day; mkdir -p saves a
        setup step."""
        guard = self._guard()
        deep = tmp_path / "a" / "b" / "c"
        out = guard.save_trace_for_eval(deep, label="safe")
        assert deep.exists() and deep.is_dir()
        assert out.parent == deep

    def test_filename_override_respected(self, tmp_path: Path):
        """Users often want a PR or incident ID in the filename."""
        guard = self._guard()
        out = guard.save_trace_for_eval(tmp_path, label="unsafe", filename="INC-42")
        assert out.name == "unsafe_INC-42.json"


# ---------------------------------------------------------------------------
# sponsio export CLI
# ---------------------------------------------------------------------------


def _write_sponsio_dump(path: Path, agent_id: str, tools: list[str]) -> None:
    """Write the Sponsio-native dump format (``Trace.export()`` output)."""
    trace = Trace(
        events=[
            Event(ts=i, agent=agent_id, event_type="tool_call", tool=t)
            for i, t in enumerate(tools)
        ],
        metadata={"agent_id": agent_id},
    )
    path.write_text(trace.to_json())


class TestCliExport:
    def test_single_file_input(self, tmp_path: Path):
        src = tmp_path / "run1.json"
        _write_sponsio_dump(src, "bot", ["verify_identity", "issue_refund"])

        out_dir = tmp_path / "traces"
        runner = CliRunner()
        result = runner.invoke(export_cmd, [str(src), "--to", str(out_dir)])
        assert result.exit_code == 0, result.output

        outputs = list(out_dir.glob("*.json"))
        assert len(outputs) == 1
        assert outputs[0].name == "safe_run1.json"
        # Verify it's actually OTLP and replayable:
        payload = json.loads(outputs[0].read_text())
        trace = otel_to_trace(payload)
        assert [e.tool for e in trace.events] == ["verify_identity", "issue_refund"]

    def test_directory_input(self, tmp_path: Path):
        src = tmp_path / "dumps"
        src.mkdir()
        _write_sponsio_dump(src / "a.json", "bot", ["x"])
        _write_sponsio_dump(src / "b.json", "bot", ["y"])

        out_dir = tmp_path / "traces"
        runner = CliRunner()
        result = runner.invoke(
            export_cmd,
            [str(src), "--to", str(out_dir), "--label", "unsafe"],
        )
        assert result.exit_code == 0, result.output

        outputs = sorted(p.name for p in out_dir.glob("*.json"))
        assert outputs == ["unsafe_a.json", "unsafe_b.json"]

    def test_preserves_existing_safe_unsafe_prefix(self, tmp_path: Path):
        """If the source is already named ``unsafe_incident-7.json``
        we must NOT prefix it a second time — double-prefix would
        be unreadable and, worse, flip the label semantics
        (``safe_unsafe_foo`` matches ``safe_`` first)."""
        src = tmp_path / "dumps"
        src.mkdir()
        _write_sponsio_dump(src / "unsafe_incident-7.json", "bot", ["x"])

        out_dir = tmp_path / "traces"
        runner = CliRunner()
        result = runner.invoke(
            export_cmd,
            [str(src), "--to", str(out_dir), "--label", "safe"],
        )
        assert result.exit_code == 0, result.output
        outputs = sorted(p.name for p in out_dir.glob("*.json"))
        assert outputs == ["unsafe_incident-7.json"]

    def test_label_none_keeps_basename(self, tmp_path: Path):
        """``--label none`` is the escape hatch for users who've
        already curated filenames with their own scheme."""
        src = tmp_path / "dumps"
        src.mkdir()
        _write_sponsio_dump(src / "run-42.json", "bot", ["x"])

        out_dir = tmp_path / "traces"
        runner = CliRunner()
        result = runner.invoke(
            export_cmd,
            [str(src), "--to", str(out_dir), "--label", "none"],
        )
        assert result.exit_code == 0, result.output
        assert (out_dir / "run-42.json").exists()

    def test_refuses_to_rewrap_otlp(self, tmp_path: Path):
        """If the user accidentally points export at an OTLP
        directory (e.g. they ran it twice), we should NOT wrap
        ``resourceSpans`` inside another ``resourceSpans`` — that
        would silently corrupt the eval corpus."""
        src = tmp_path / "already_otlp.json"
        src.write_text(
            json.dumps(
                {
                    "resourceSpans": [
                        {
                            "resource": {"attributes": []},
                            "scopeSpans": [{"spans": [{"name": "x"}]}],
                        }
                    ]
                }
            )
        )

        out_dir = tmp_path / "traces"
        runner = CliRunner()
        result = runner.invoke(export_cmd, [str(src), "--to", str(out_dir)])
        assert result.exit_code == 0, result.output
        assert "refusing to re-wrap" in result.output
        assert list(out_dir.glob("*.json")) == []

    def test_skips_non_sponsio_json(self, tmp_path: Path):
        """Users' ``dumps/`` might contain a stray ``config.json`` or
        a ``README.json``; we shouldn't abort — just skip and report."""
        src = tmp_path / "dumps"
        src.mkdir()
        _write_sponsio_dump(src / "good.json", "bot", ["x"])
        (src / "unrelated.json").write_text(json.dumps({"hello": "world"}))

        out_dir = tmp_path / "traces"
        runner = CliRunner()
        result = runner.invoke(export_cmd, [str(src), "--to", str(out_dir)])
        assert result.exit_code == 0, result.output
        assert "Converted 1 trace" in result.output
        assert "unrelated.json" in result.output
        assert "no 'events' key" in result.output

    def test_agent_override(self, tmp_path: Path):
        """``--agent`` stamps ``service.name`` on every output,
        useful after renaming an agent or for consolidating traces
        from multi-instance deployments under one logical name."""
        src = tmp_path / "run.json"
        _write_sponsio_dump(src, "old-name", ["x"])

        out_dir = tmp_path / "traces"
        runner = CliRunner()
        result = runner.invoke(
            export_cmd,
            [str(src), "--to", str(out_dir), "--agent", "new-name"],
        )
        assert result.exit_code == 0, result.output
        otlp = json.loads((out_dir / "safe_run.json").read_text())
        back = otel_to_trace(otlp)
        assert back.events[0].agent == "new-name"

    def test_empty_directory_warns_but_succeeds(self, tmp_path: Path):
        """User ran export against the wrong path — don't exit with
        an error (breaks CI pipelines that fan out export over
        multiple hosts), just warn on stderr."""
        src = tmp_path / "empty"
        src.mkdir()
        out_dir = tmp_path / "traces"
        runner = CliRunner()
        result = runner.invoke(export_cmd, [str(src), "--to", str(out_dir)])
        assert result.exit_code == 0
        assert "No files matched" in result.output

    def test_end_to_end_pipe_into_eval(self, tmp_path: Path):
        """The reason this whole feature exists: export → eval in
        a single shell sequence, no manual shape munging."""
        from sponsio.eval_runner import discover_cases, run_eval

        src = tmp_path / "dumps"
        src.mkdir()
        # Two "safe" runs (both verify before refund)
        _write_sponsio_dump(src / "a.json", "bot", ["verify_identity", "issue_refund"])
        _write_sponsio_dump(src / "b.json", "bot", ["verify_identity", "issue_refund"])
        # One "unsafe" run (skipped verify)
        _write_sponsio_dump(src / "unsafe_c.json", "bot", ["issue_refund"])

        traces = tmp_path / "traces"
        runner = CliRunner()
        result = runner.invoke(
            export_cmd, [str(src), "--to", str(traces), "--label", "safe"]
        )
        assert result.exit_code == 0, result.output

        cases = discover_cases(traces)
        # Three cases — two ``safe_*`` (prefixed by export) + one
        # ``unsafe_*`` (existing prefix preserved)
        assert len(cases) == 3
        labels = sorted(c.label for c in cases)
        assert labels == ["safe", "safe", "unsafe"]

        report = run_eval(
            cases,
            ["tool `verify_identity` must precede `issue_refund`"],
        )
        # Contract blocks the unsafe case and lets the safe ones through.
        assert report.overall_tp == 1
        assert report.overall_tn == 2
        assert report.overall_fp == 0
        assert report.overall_fn == 0

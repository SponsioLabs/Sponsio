"""Tests for the unified trace loader + ``sponsio scan --trace`` wiring.

Three layers of coverage:

1. **Loader** (`load_trace` / `load_traces`) — sniffing OTLP vs native,
   single-doc JSON vs JSONL, session event logs, globs.
2. **OpenInference attr extraction** in :func:`otel_to_trace`.
3. **CLI integration** — ``sponsio scan -t ...`` produces a YAML with
   trace-mined contracts labeled ``source: trace``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from sponsio.cli import cli
from sponsio.discovery.loaders import load_trace, load_traces
from sponsio.tracer.otel_consumer import otel_to_trace


# ---------------------------------------------------------------------------
# Fixtures — concise builders for each trace shape
# ---------------------------------------------------------------------------


def _native_trace(tools: list[str]) -> dict:
    return {
        "events": [
            {"ts": i, "agent": "a", "type": "tool_call", "tool": t}
            for i, t in enumerate(tools)
        ]
    }


def _otlp_payload(span_specs: list[tuple[str, list[tuple[str, str]]]]) -> dict:
    """Build a minimal OTLP/JSON payload from ``(name, [(k, v), ...])`` specs."""
    spans = []
    for i, (name, attrs) in enumerate(span_specs):
        spans.append(
            {
                "name": name,
                "startTimeUnixNano": str((i + 1) * 1000),
                "attributes": [
                    {"key": k, "value": {"stringValue": v}} for k, v in attrs
                ],
            }
        )
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "bot"}}
                    ]
                },
                "scopeSpans": [{"spans": spans}],
            }
        ]
    }


# ---------------------------------------------------------------------------
# Loader: format sniffing
# ---------------------------------------------------------------------------


class TestLoadTrace:
    def test_native_single_json(self, tmp_path: Path) -> None:
        p = tmp_path / "t.json"
        p.write_text(json.dumps(_native_trace(["A", "B"])))
        traces = load_trace(p)
        assert len(traces) == 1
        assert [e.tool for e in traces[0].events] == ["A", "B"]

    def test_native_array_json(self, tmp_path: Path) -> None:
        p = tmp_path / "t.json"
        p.write_text(json.dumps([_native_trace(["A"]), _native_trace(["B"])]))
        traces = load_trace(p)
        assert len(traces) == 2

    def test_otlp_single_json(self, tmp_path: Path) -> None:
        p = tmp_path / "t.json"
        p.write_text(json.dumps(_otlp_payload([("toolA", []), ("toolB", [])])))
        traces = load_trace(p)
        assert len(traces) == 1
        # Both spans should classify as tool calls (no gen_ai / llm attrs).
        types = [e.event_type for e in traces[0].events]
        assert types == ["tool_call", "tool_call"]

    def test_native_jsonl(self, tmp_path: Path) -> None:
        p = tmp_path / "t.jsonl"
        p.write_text("\n".join(json.dumps(_native_trace([t])) for t in ("A", "B", "C")))
        traces = load_trace(p)
        assert len(traces) == 3
        assert [t.events[0].tool for t in traces] == ["A", "B", "C"]

    def test_otlp_jsonl_merges_into_one(self, tmp_path: Path) -> None:
        # Multiple OTLP batches (each line is a batch) → one trace with
        # all spans flattened.  This matches what `otel` CLI tools emit
        # when streaming to a file.
        p = tmp_path / "t.jsonl"
        lines = [
            json.dumps(_otlp_payload([("toolA", [])])),
            json.dumps(_otlp_payload([("toolB", [])])),
        ]
        p.write_text("\n".join(lines))
        traces = load_trace(p)
        assert len(traces) == 1
        assert len(traces[0].events) == 2

    def test_session_jsonl_native_event_shape(self, tmp_path: Path) -> None:
        # The "native Event per line" form — the shape `Trace.to_dict()`
        # serialises events to.
        p = tmp_path / "session.jsonl"
        p.write_text(
            "\n".join(
                json.dumps({"ts": i, "agent": "a", "type": "tool_call", "tool": t})
                for i, t in enumerate(["A", "B", "C"])
            )
        )
        traces = load_trace(p)
        assert len(traces) == 1
        assert [e.tool for e in traces[0].events] == ["A", "B", "C"]

    def test_session_jsonl_real_session_logger_format(self, tmp_path: Path) -> None:
        # The shape `SessionLogger._serialize` actually writes — a
        # `MonitorEvent` record, not a raw Event.  This is the format
        # `~/.sponsio/sessions/<agent>/*.jsonl` files contain in
        # production, and what users will (correctly) point
        # `sponsio scan --trace` at after reading the docs.
        from sponsio.runtime.session_log import SessionLogger

        logger = SessionLogger(agent_id="bot", base_dir=tmp_path)

        # Build minimal MonitorEvent stand-ins — shape mirrors
        # `_serialize` exactly so this test catches drift in either
        # direction (logger output OR loader expectations).
        records = [
            {
                "ts": 1.0,
                "agent_id": "bot",
                "action": "search_web",
                "pipeline": "det",
                "constraint": "c1",
                "result": {"action": "allow", "message": "ok"},
            },
            {
                "ts": 2.0,
                "agent_id": "bot",
                "action": "issue_refund",
                "pipeline": "det",
                "constraint": "c2",
                "result": {"action": "deny", "message": "blocked"},
            },
        ]
        with logger.path.open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        traces = load_trace(logger.path)
        assert len(traces) == 1
        ev = traces[0].events
        assert [e.tool for e in ev] == ["search_web", "issue_refund"]
        # Each translated event preserves the runtime decision so
        # downstream miners / filters can still distinguish allow vs deny.
        assert ev[0].args == {
            "constraint": "c1",
            "pipeline": "det",
            "decision": "allow",
        }
        assert ev[1].args["decision"] == "deny"
        assert ev[1].content == "blocked"

    def test_session_jsonl_mixed_shapes_emits_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Mixed-shape JSONL: first line picks the mode, dissimilar
        # lines must be dropped *and* surfaced via stderr so users
        # don't get inexplicably weak proposals downstream.
        p = tmp_path / "mixed.jsonl"
        p.write_text(
            "\n".join(
                [
                    json.dumps(
                        {"ts": 1, "agent": "a", "type": "tool_call", "tool": "A"}
                    ),
                    json.dumps({"unrelated": "junk"}),
                    json.dumps(
                        {"ts": 2, "agent": "a", "type": "tool_call", "tool": "B"}
                    ),
                ]
            )
        )
        traces = load_trace(p)
        # 2 of 3 lines kept; warning surfaces the drop count.
        assert len(traces[0].events) == 2
        captured = capsys.readouterr()
        assert "dropped 1 non-matching" in captured.err
        assert str(p) in captured.err

    def test_directory_loads_all_top_level_traces(self, tmp_path: Path) -> None:
        # `-t traces/` — directory expansion shouldn't crash with
        # IsADirectoryError; instead, every top-level *.json* file is
        # loaded.
        d = tmp_path / "traces"
        d.mkdir()
        (d / "a.json").write_text(json.dumps(_native_trace(["A"])))
        (d / "b.jsonl").write_text(
            json.dumps({"ts": 1, "agent": "a", "type": "tool_call", "tool": "B"})
        )
        # Non-trace siblings must be ignored — keeps `-t .` viable.
        (d / "README.md").write_text("ignore me")
        traces = load_trace(d)
        tools = sorted(e.tool for t in traces for e in t.events)
        assert tools == ["A", "B"]

    def test_empty_directory_raises_friendly_error(self, tmp_path: Path) -> None:
        d = tmp_path / "empty"
        d.mkdir()
        with pytest.raises(ValueError, match="No trace files"):
            load_trace(d)

    def test_tilde_expansion_in_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # docstring promises `~/.sponsio/sessions/...`; pin the
        # expansion behaviour by faking $HOME to tmp_path.
        monkeypatch.setenv("HOME", str(tmp_path))
        target = tmp_path / "trace.json"
        target.write_text(json.dumps(_native_trace(["A"])))
        traces = load_trace("~/trace.json")
        assert [e.tool for t in traces for e in t.events] == ["A"]

    def test_blank_lines_tolerated(self, tmp_path: Path) -> None:
        # Editors love to add trailing newlines; they mustn't derail sniffing.
        p = tmp_path / "t.jsonl"
        p.write_text(
            "\n\n"
            + json.dumps({"ts": 1, "agent": "a", "type": "tool_call", "tool": "A"})
            + "\n\n\n"
            + json.dumps({"ts": 2, "agent": "a", "type": "tool_call", "tool": "B"})
            + "\n",
        )
        traces = load_trace(p)
        assert len(traces[0].events) == 2

    def test_bad_json_raises_helpfully(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.jsonl"
        p.write_text("{invalid json\n{also bad}")
        with pytest.raises(ValueError, match="Invalid JSONL"):
            load_trace(p)

    def test_unknown_shape_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "weird.json"
        p.write_text(json.dumps({"hello": "world"}))
        with pytest.raises(ValueError, match="Unrecognized"):
            load_trace(p)

    def test_missing_file_raises_filenotfound(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_trace(tmp_path / "does-not-exist.json")


class TestLoadTracesGlobs:
    def test_glob_star(self, tmp_path: Path) -> None:
        for i in range(3):
            (tmp_path / f"t{i}.json").write_text(json.dumps(_native_trace(["A"])))
        traces = load_traces([str(tmp_path / "*.json")])
        assert len(traces) == 3

    def test_recursive_glob(self, tmp_path: Path) -> None:
        sub = tmp_path / "nested"
        sub.mkdir()
        (tmp_path / "top.json").write_text(json.dumps(_native_trace(["A"])))
        (sub / "inner.json").write_text(json.dumps(_native_trace(["B"])))
        traces = load_traces([str(tmp_path / "**" / "*.json")])
        # ``**`` with ``*.json`` catches both files; at minimum the
        # nested one must be discovered.
        tools = [e.tool for t in traces for e in t.events]
        assert "B" in tools

    def test_mixed_paths_and_globs(self, tmp_path: Path) -> None:
        (tmp_path / "a.json").write_text(json.dumps(_native_trace(["A"])))
        (tmp_path / "b.json").write_text(json.dumps(_native_trace(["B"])))
        extra = tmp_path / "c.jsonl"
        extra.write_text(json.dumps(_native_trace(["C"])))
        traces = load_traces([str(tmp_path / "*.json"), str(extra)])
        tools = {e.tool for t in traces for e in t.events}
        assert tools == {"A", "B", "C"}

    def test_missing_glob_returns_empty(self, tmp_path: Path) -> None:
        # Non-matching glob should silently return [] — callers treat
        # that as "no mined contracts" rather than crashing.
        assert load_traces([str(tmp_path / "missing-*.json")]) == []

    def test_tilde_expansion_in_glob(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `~/.sponsio/sessions/bot/*.jsonl` is the headline example in
        # the docstring — it must actually expand.
        monkeypatch.setenv("HOME", str(tmp_path))
        sub = tmp_path / "traces"
        sub.mkdir()
        (sub / "x.json").write_text(json.dumps(_native_trace(["A"])))
        (sub / "y.json").write_text(json.dumps(_native_trace(["B"])))
        traces = load_traces(["~/traces/*.json"])
        tools = sorted(e.tool for t in traces for e in t.events)
        assert tools == ["A", "B"]

    def test_directory_arg_to_load_traces(self, tmp_path: Path) -> None:
        # The CLI invocation `sponsio scan -t traces/` flows through
        # `load_traces` with a literal directory string.
        d = tmp_path / "traces"
        d.mkdir()
        (d / "a.json").write_text(json.dumps(_native_trace(["A"])))
        traces = load_traces([str(d)])
        assert traces and traces[0].events[0].tool == "A"


# ---------------------------------------------------------------------------
# OpenInference attribute extraction
# ---------------------------------------------------------------------------


class TestOpenInferenceAttrs:
    def test_llm_kind_classifies_as_llm(self) -> None:
        # Even with zero ``gen_ai.*`` attrs, the Phoenix/Langfuse
        # ``openinference.span.kind == "LLM"`` hint alone should
        # flip classification.
        data = _otlp_payload(
            [
                (
                    "chat",
                    [
                        ("openinference.span.kind", "LLM"),
                        ("llm.model_name", "gpt-4o-mini"),
                        ("llm.output_messages.0.message.content", "hi"),
                    ],
                )
            ]
        )
        trace = otel_to_trace(data)
        # llm_response at minimum; llm_request is optional when
        # there's no prompt.
        types = [e.event_type for e in trace.events]
        assert "llm_response" in types

    def test_token_counts_pulled_from_llm_namespace(self) -> None:
        # Build payload with non-string int values — the attr helper
        # has to accept both intValue and stringValue.
        data = {
            "resourceSpans": [
                {
                    "resource": {"attributes": []},
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "name": "chat",
                                    "startTimeUnixNano": "1",
                                    "attributes": [
                                        {
                                            "key": "openinference.span.kind",
                                            "value": {"stringValue": "LLM"},
                                        },
                                        {
                                            "key": "llm.token_count.prompt",
                                            "value": {"intValue": "42"},
                                        },
                                        {
                                            "key": "llm.token_count.completion",
                                            "value": {"intValue": "7"},
                                        },
                                        {
                                            "key": "llm.output_messages.0.message.content",
                                            "value": {"stringValue": "hi"},
                                        },
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        }
        trace = otel_to_trace(data)
        resp = next(e for e in trace.events if e.event_type == "llm_response")
        assert resp.args is not None
        assert resp.args["input_tokens"] == 42
        assert resp.args["output_tokens"] == 7
        assert resp.args["tokens"] == 49

    def test_tool_name_and_parameters_override_span_name(self) -> None:
        # Phoenix wraps tool calls under a generic span name like
        # ``langchain.tool`` with real identity in ``tool.name``.
        data = _otlp_payload(
            [
                (
                    "langchain.tool",
                    [
                        ("tool.name", "search_web"),
                        ("tool.parameters", '{"query": "tokyo weather"}'),
                    ],
                )
            ]
        )
        trace = otel_to_trace(data)
        assert len(trace.events) == 1
        ev = trace.events[0]
        assert ev.event_type == "tool_call"
        assert ev.tool == "search_web"
        assert ev.args == {"query": "tokyo weather"}

    def test_tool_parameters_non_json_falls_back(self) -> None:
        data = _otlp_payload([("tool.call", [("input.value", "raw prompt input")])])
        trace = otel_to_trace(data)
        # Not an LLM span, so classified as tool_call; input.value is
        # the last-resort fallback and gets wrapped.
        assert trace.events[0].event_type == "tool_call"
        assert trace.events[0].args == {"value": "raw prompt input"}


# ---------------------------------------------------------------------------
# CLI: sponsio scan -t
# ---------------------------------------------------------------------------


class TestScanTraceCLI:
    def _write_traces(self, dest: Path, sequences: list[list[str]]) -> None:
        dest.mkdir(exist_ok=True)
        for i, seq in enumerate(sequences):
            (dest / f"t{i}.json").write_text(json.dumps(_native_trace(seq)))

    def test_scan_emits_trace_sourced_contracts(self, tmp_path: Path) -> None:
        # 3 traces that always go A → B → C → exactly the pattern
        # TraceMiner should pick up as ``must_precede``.
        src = tmp_path / "src"
        src.mkdir()
        (src / "noop.py").write_text("def noop(): pass\n")

        self._write_traces(
            tmp_path / "traces",
            [["A", "B", "C"], ["A", "B", "C"], ["A", "B", "C"]],
        )

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(
                cli,
                [
                    "scan",
                    str(src),
                    "-t",
                    str(tmp_path / "traces" / "*.json"),
                    "-o",
                    "-",
                ],
            )
        assert result.exit_code == 0, result.output
        # CliRunner merges stderr into output by default.
        assert "pattern: must_precede" in result.output
        assert "source: trace" in result.output
        # Trace-mining progress line should surface the count.
        assert "Trace mining" in result.output

    def test_scan_no_traces_is_fine(self, tmp_path: Path) -> None:
        # `-t` absent → trace mining is skipped cleanly (regression
        # guard for the fact we fallthrough when ``trace_paths`` is
        # None).
        src = tmp_path / "src"
        src.mkdir()
        (src / "noop.py").write_text("def noop(): pass\n")

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["scan", str(src), "-o", "-"])
        assert result.exit_code == 0
        assert "Trace mining" not in result.output

    def test_scan_missing_trace_glob_is_warning_not_crash(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "noop.py").write_text("def noop(): pass\n")

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(
                cli,
                [
                    "scan",
                    str(src),
                    "-t",
                    str(tmp_path / "does-not-exist-*.json"),
                    "-o",
                    "-",
                ],
            )
        assert result.exit_code == 0, result.output
        # User should see the empty-match message, not a traceback.
        assert "0 trace(s) found" in result.output

    def test_scan_min_support_drops_low_frequency_patterns(
        self, tmp_path: Path
    ) -> None:
        # One-off: A appears in only 1/3 traces. With min_support=2 the
        # miner should drop anything involving it.
        src = tmp_path / "src"
        src.mkdir()
        (src / "noop.py").write_text("def noop(): pass\n")
        self._write_traces(
            tmp_path / "traces",
            [["A", "B"], ["B"], ["B"]],
        )
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(
                cli,
                [
                    "scan",
                    str(src),
                    "-t",
                    str(tmp_path / "traces" / "*.json"),
                    "--trace-min-support",
                    "2",
                    "-o",
                    "-",
                ],
            )
        assert result.exit_code == 0, result.output
        # No must_precede should mention A — only B's idempotent survives.
        assert ", A]" not in result.output
        assert "[A," not in result.output

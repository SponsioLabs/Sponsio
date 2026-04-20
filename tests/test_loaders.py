"""Tests for sponsio/discovery/loaders.py."""

import json

import pytest

from sponsio.discovery.loaders import (
    load_document,
    load_documents,
    load_trace,
    load_traces,
    resolve_code_paths,
)
from sponsio.models.trace import Event, Trace


# ---------------------------------------------------------------------------
# Document loading
# ---------------------------------------------------------------------------


class TestLoadDocument:
    def test_load_txt(self, tmp_path):
        f = tmp_path / "policy.txt"
        f.write_text("All refunds require policy check.")
        assert load_document(f) == "All refunds require policy check."

    def test_load_md(self, tmp_path):
        f = tmp_path / "policy.md"
        f.write_text("# Policy\n\nNo refunds without approval.")
        text = load_document(f)
        assert "No refunds without approval" in text

    def test_load_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_document("/nonexistent/file.txt")

    def test_load_unsupported_format(self, tmp_path):
        f = tmp_path / "data.xlsx"
        f.write_text("data")
        with pytest.raises(ValueError, match="Unsupported"):
            load_document(f)

    def test_load_documents_multiple(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.md"
        f1.write_text("Rule one.")
        f2.write_text("Rule two.")
        results = load_documents([f1, f2])
        assert len(results) == 2
        assert "Rule one" in results[0]
        assert "Rule two" in results[1]


# ---------------------------------------------------------------------------
# Trace loading
# ---------------------------------------------------------------------------


class TestLoadTrace:
    def test_load_single_trace(self, tmp_path):
        trace = Trace(
            events=[
                Event(ts=0, agent="bot", event_type="tool_call", tool="check"),
                Event(ts=1, agent="bot", event_type="tool_call", tool="refund"),
            ]
        )
        f = tmp_path / "trace.json"
        f.write_text(json.dumps(trace.to_dict()))
        loaded = load_trace(f)
        assert len(loaded) == 1
        assert len(loaded[0].events) == 2

    def test_load_trace_array(self, tmp_path):
        t1 = Trace(events=[Event(ts=0, agent="bot", event_type="tool_call", tool="A")])
        t2 = Trace(events=[Event(ts=0, agent="bot", event_type="tool_call", tool="B")])
        f = tmp_path / "traces.json"
        f.write_text(json.dumps([t1.to_dict(), t2.to_dict()]))
        loaded = load_trace(f)
        assert len(loaded) == 2

    def test_load_trace_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_trace("/nonexistent/trace.json")

    def test_load_trace_bad_format(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text(json.dumps({"not_a_trace": True}))
        with pytest.raises(ValueError, match="Unrecognized"):
            load_trace(f)

    def test_load_traces_glob(self, tmp_path):
        for i in range(3):
            t = Trace(
                events=[Event(ts=0, agent="bot", event_type="tool_call", tool=f"t{i}")]
            )
            (tmp_path / f"trace_{i}.json").write_text(json.dumps(t.to_dict()))
        loaded = load_traces([str(tmp_path / "*.json")])
        assert len(loaded) == 3

    def test_load_traces_multiple_files(self, tmp_path):
        for name in ["a.json", "b.json"]:
            t = Trace(
                events=[Event(ts=0, agent="bot", event_type="tool_call", tool="X")]
            )
            (tmp_path / name).write_text(json.dumps(t.to_dict()))
        loaded = load_traces([tmp_path / "a.json", tmp_path / "b.json"])
        assert len(loaded) == 2


# ---------------------------------------------------------------------------
# Code path resolution
# ---------------------------------------------------------------------------


class TestResolveCodePaths:
    def test_single_file(self, tmp_path):
        f = tmp_path / "agent.py"
        f.write_text("pass")
        result = resolve_code_paths([f])
        assert result == [f]

    def test_directory(self, tmp_path):
        (tmp_path / "a.py").write_text("pass")
        (tmp_path / "b.py").write_text("pass")
        (tmp_path / "readme.md").write_text("not python")
        result = resolve_code_paths([tmp_path])
        assert len(result) == 2
        assert all(p.suffix == ".py" for p in result)

    def test_glob_pattern(self, tmp_path):
        (tmp_path / "agent1.py").write_text("pass")
        (tmp_path / "agent2.py").write_text("pass")
        (tmp_path / "config.yaml").write_text("x: 1")
        result = resolve_code_paths([str(tmp_path / "*.py")])
        assert len(result) == 2

    def test_nonexistent_returns_empty(self):
        result = resolve_code_paths(["/nonexistent/dir"])
        assert result == []

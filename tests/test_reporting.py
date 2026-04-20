"""Tests for the shadow-mode session report (``sponsio report``).

Coverage:
    * ``parse_since`` (happy + errors)
    * ``load_events`` (time filter, agent filter, malformed skip)
    * ``aggregate`` (outcome counters, by_contract / by_session rollups)
    * renderers (markdown / html / json structural assertions)
    * CLI (``sponsio report`` via ``CliRunner``, including --out and --format)
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from sponsio.cli import cli
from sponsio.reporting import (
    ContractStat,
    Report,
    SessionEvent,
    SessionStat,
    aggregate,
    load_events,
    parse_since,
    render,
    render_html,
    render_json,
    render_markdown,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _mk_record(
    *,
    ts: float,
    agent: str = "support_bot",
    action: str = "issue_refund",
    pipeline: str = "det",
    constraint: str = "must precede `check_policy` before `issue_refund`",
    result_action: str = "blocked",
    message: str = "policy not yet checked",
    sto_score: float | None = None,
) -> dict:
    rec = {
        "ts": ts,
        "agent_id": agent,
        "action": action,
        "pipeline": pipeline,
        "constraint": constraint,
        "result": {"action": result_action, "message": message},
    }
    if sto_score is not None:
        rec["sto"] = {"score": sto_score, "evidence": "pii detected"}
    return rec


# ---------------------------------------------------------------------------
# parse_since
# ---------------------------------------------------------------------------


class TestParseSince:
    def test_all_returns_zero(self):
        assert parse_since("all") == 0.0
        assert parse_since("ALL") == 0.0
        assert parse_since("") == 0.0

    def test_seconds_minutes_hours_days(self):
        now = 10_000.0
        assert parse_since("30s", now=now) == 10_000 - 30
        assert parse_since("5m", now=now) == 10_000 - 5 * 60
        assert parse_since("2h", now=now) == 10_000 - 2 * 3600
        assert parse_since("7d", now=now) == 10_000 - 7 * 86400

    def test_rejects_malformed(self):
        with pytest.raises(ValueError, match="Invalid --since"):
            parse_since("7")
        with pytest.raises(ValueError, match="Invalid --since"):
            parse_since("banana")
        with pytest.raises(ValueError, match="Invalid --since"):
            parse_since("3y")

    def test_uppercase_units_allowed(self):
        now = 10_000.0
        assert parse_since("2H", now=now) == 10_000 - 2 * 3600


# ---------------------------------------------------------------------------
# load_events
# ---------------------------------------------------------------------------


class TestLoadEvents:
    def test_reads_records_across_agents(self, tmp_path: Path):
        now = time.time()
        _write_jsonl(
            tmp_path / "support_bot" / "20260418_100000_1.jsonl",
            [_mk_record(ts=now - 60, agent="support_bot")],
        )
        _write_jsonl(
            tmp_path / "pricing_bot" / "20260418_100000_2.jsonl",
            [_mk_record(ts=now - 120, agent="pricing_bot")],
        )
        events = list(load_events(since="all", base_dir=tmp_path))
        assert len(events) == 2
        assert {e.agent_id for e in events} == {"support_bot", "pricing_bot"}

    def test_filters_by_agent(self, tmp_path: Path):
        now = time.time()
        _write_jsonl(tmp_path / "a" / "s1.jsonl", [_mk_record(ts=now, agent="a")])
        _write_jsonl(tmp_path / "b" / "s2.jsonl", [_mk_record(ts=now, agent="b")])
        events = list(load_events(since="all", agent="a", base_dir=tmp_path))
        assert len(events) == 1
        assert events[0].agent_id == "a"

    def test_filters_by_time_window(self, tmp_path: Path):
        now = 10_000.0
        _write_jsonl(
            tmp_path / "bot" / "s.jsonl",
            [
                _mk_record(ts=now - 30),  # 30s ago — inside 1h window
                _mk_record(ts=now - 7200),  # 2h ago — outside
            ],
        )
        events = list(load_events(since="1h", base_dir=tmp_path, now=now))
        assert len(events) == 1
        assert events[0].ts == now - 30

    def test_skips_malformed_lines(self, tmp_path: Path):
        path = tmp_path / "bot" / "s.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        valid = json.dumps(_mk_record(ts=time.time()))
        with path.open("w", encoding="utf-8") as f:
            f.write("{not json\n")
            f.write("\n")  # blank
            f.write(valid + "\n")
            f.write("[]\n")  # valid json but not dict
            f.write('{"ts": "NaN-banana"}\n')  # bad ts but recoverable
        events = list(load_events(since="all", base_dir=tmp_path))
        # two records survive: the fully-valid one + the bad-ts one (ts=0.0)
        assert len(events) == 2

    def test_empty_base_dir_returns_nothing(self, tmp_path: Path):
        events = list(load_events(since="all", base_dir=tmp_path / "nope"))
        assert events == []

    def test_records_source_file(self, tmp_path: Path):
        path = tmp_path / "bot" / "abc.jsonl"
        _write_jsonl(path, [_mk_record(ts=time.time())])
        events = list(load_events(since="all", base_dir=tmp_path))
        assert events[0].source_file == path


# ---------------------------------------------------------------------------
# aggregate
# ---------------------------------------------------------------------------


class TestAggregate:
    def test_counts_by_outcome(self):
        events = [
            SessionEvent(
                ts=1.0,
                agent_id="a",
                action="t",
                pipeline="det",
                constraint="c1",
                result_action="blocked",
                result_message="",
            ),
            SessionEvent(
                ts=2.0,
                agent_id="a",
                action="t",
                pipeline="det",
                constraint="c1",
                result_action="observed",
                result_message="",
            ),
            SessionEvent(
                ts=3.0,
                agent_id="a",
                action="t",
                pipeline="sto",
                constraint="c2",
                result_action="retrying",
                result_message="",
            ),
            SessionEvent(
                ts=4.0,
                agent_id="a",
                action="t",
                pipeline="det",
                constraint="c1",
                result_action="allowed",
                result_message="",
            ),
        ]
        rep = aggregate(events)
        assert rep.blocked == 1
        assert rep.observed == 1
        assert rep.retrying == 1
        assert rep.passed == 1
        assert rep.violations == 3
        assert rep.pass_rate == 0.25

    def test_window_bounds_track_min_max(self):
        events = [
            SessionEvent(
                ts=5.0,
                agent_id="a",
                action="t",
                pipeline="det",
                constraint="c",
                result_action="allowed",
                result_message="",
            ),
            SessionEvent(
                ts=1.0,
                agent_id="a",
                action="t",
                pipeline="det",
                constraint="c",
                result_action="allowed",
                result_message="",
            ),
            SessionEvent(
                ts=3.0,
                agent_id="a",
                action="t",
                pipeline="det",
                constraint="c",
                result_action="allowed",
                result_message="",
            ),
        ]
        rep = aggregate(events)
        assert rep.window_start == 1.0
        assert rep.window_end == 5.0

    def test_by_contract_sorted_by_violation_count_desc(self):
        def _v(constraint: str, ts: float) -> SessionEvent:
            return SessionEvent(
                ts=ts,
                agent_id="a",
                action="t",
                pipeline="det",
                constraint=constraint,
                result_action="blocked",
                result_message="msg",
            )

        events = [_v("rare", 1.0)] + [_v("common", t) for t in range(3)]
        rep = aggregate(events)
        assert [c.constraint for c in rep.by_contract] == ["common", "rare"]
        assert rep.by_contract[0].violations == 3
        assert rep.by_contract[1].violations == 1

    def test_by_session_counts_files(self, tmp_path: Path):
        now = time.time()
        _write_jsonl(
            tmp_path / "bot" / "s1.jsonl",
            [_mk_record(ts=now), _mk_record(ts=now, result_action="allowed")],
        )
        _write_jsonl(
            tmp_path / "bot" / "s2.jsonl",
            [_mk_record(ts=now)],
        )
        events = list(load_events(since="all", base_dir=tmp_path))
        rep = aggregate(events)
        assert rep.total_sessions == 2
        # s1 has 1 violation + 1 pass, s2 has 1 violation
        sources = {s.source: s for s in rep.by_session}
        assert sources["s1"].violations == 1
        assert sources["s1"].events == 2
        assert sources["s2"].violations == 1

    def test_empty_events_yields_empty_report(self):
        rep = aggregate([])
        assert rep.total_events == 0
        assert rep.agents == []
        assert rep.by_contract == []
        assert rep.pass_rate == 0.0


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_report() -> Report:
    return Report(
        agents=["support_bot"],
        window_start=1_713_456_789.0,
        window_end=1_713_543_189.0,
        total_events=100,
        total_sessions=3,
        passed=95,
        blocked=3,
        observed=1,
        retrying=1,
        by_contract=[
            ContractStat(
                constraint="tool `issue_refund` at most 3 times",
                pipeline="det",
                violations=3,
                blocked=3,
                first_seen=1_713_456_800.0,
                last_seen=1_713_500_000.0,
                sample_message="rate limit exceeded",
            ),
            ContractStat(
                constraint="response must not contain PII",
                pipeline="sto",
                violations=1,
                retrying=1,
                first_seen=1_713_470_000.0,
                last_seen=1_713_470_000.0,
            ),
        ],
        by_session=[
            SessionStat(
                source="20260418_100000_1234",
                agent_id="support_bot",
                events=40,
                violations=3,
                first_seen=1_713_456_789.0,
                last_seen=1_713_500_000.0,
            )
        ],
    )


class TestRenderMarkdown:
    def test_includes_headline_metrics(self, sample_report):
        md = render_markdown(sample_report)
        assert "# Sponsio Report" in md
        assert "support_bot" in md
        assert "**Events evaluated:** 100" in md
        assert "**Actually blocked (enforce mode):** 3" in md
        assert "**Would-have-blocked (observe mode):** 1" in md
        assert "**Pass rate:** 95.0%" in md

    def test_top_violations_table_present(self, sample_report):
        md = render_markdown(sample_report)
        assert "## Top Violations" in md
        assert "issue_refund" in md
        assert "det" in md

    def test_recommendations_non_empty(self, sample_report):
        md = render_markdown(sample_report)
        assert "## What to do next" in md
        # at least one bullet
        assert md.count("\n- ") >= 1

    def test_empty_report_has_cold_start_advice(self):
        md = render_markdown(Report())
        assert "No shadow-mode events yet" in md


class TestRenderHtml:
    def test_self_contained_div(self, sample_report):
        html = render_html(sample_report)
        assert '<div class="sponsio-report">' in html
        assert "</div>" in html
        assert "<style>" in html
        # no external URLs
        assert "http://" not in html and "https://" not in html

    def test_escapes_content(self):
        rep = Report(agents=["<script>alert(1)</script>"])
        html = render_html(rep)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html


class TestRenderJson:
    def test_round_trips_core_fields(self, sample_report):
        j = render_json(sample_report)
        payload = json.loads(j)
        assert payload["totals"]["events"] == 100
        assert payload["totals"]["blocked"] == 3
        assert payload["totals"]["pass_rate"] == 0.95
        assert payload["agents"] == ["support_bot"]
        assert len(payload["by_contract"]) == 2

    def test_window_has_iso(self, sample_report):
        payload = json.loads(render_json(sample_report))
        assert "start_iso" in payload["window"]
        assert "UTC" in payload["window"]["start_iso"]


class TestRenderDispatcher:
    def test_md_alias(self, sample_report):
        assert render(sample_report, fmt="md") == render_markdown(sample_report)

    def test_rejects_unknown_format(self, sample_report):
        with pytest.raises(ValueError, match="Unknown --format"):
            render(sample_report, fmt="xml")


# ---------------------------------------------------------------------------
# CLI: sponsio report
# ---------------------------------------------------------------------------


class TestReportCli:
    def _seed(self, base: Path) -> None:
        now = time.time()
        _write_jsonl(
            base / "support_bot" / "s1.jsonl",
            [
                _mk_record(ts=now - 30, result_action="blocked"),
                _mk_record(ts=now - 10, result_action="allowed"),
            ],
        )
        _write_jsonl(
            base / "pricing_bot" / "s2.jsonl",
            [_mk_record(ts=now - 20, agent="pricing_bot", result_action="observed")],
        )

    def test_markdown_to_stdout(self, tmp_path: Path):
        self._seed(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["report", "--since", "all", "--base-dir", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert "# Sponsio Report" in result.output
        assert "support_bot" in result.output or "pricing_bot" in result.output

    def test_agent_filter(self, tmp_path: Path):
        self._seed(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "report",
                "--since",
                "all",
                "--agent",
                "pricing_bot",
                "--base-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "pricing_bot" in result.output

    def test_out_flag_writes_file(self, tmp_path: Path):
        self._seed(tmp_path)
        out = tmp_path / "out" / "r.md"
        out.parent.mkdir()
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "report",
                "--since",
                "all",
                "--base-dir",
                str(tmp_path),
                "-o",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        assert out.exists()
        content = out.read_text()
        assert "# Sponsio Report" in content
        assert "Wrote" in result.output

    def test_json_format(self, tmp_path: Path):
        self._seed(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "report",
                "--since",
                "all",
                "--format",
                "json",
                "--base-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert "totals" in payload

    def test_invalid_since_exits_nonzero(self, tmp_path: Path):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["report", "--since", "banana", "--base-dir", str(tmp_path)],
        )
        assert result.exit_code != 0
        assert "Invalid --since" in result.output

    def test_live_with_out_rejected(self, tmp_path: Path):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "report",
                "--live",
                "-o",
                str(tmp_path / "o"),
                "--base-dir",
                str(tmp_path),
            ],
        )
        assert result.exit_code != 0
        assert "--live cannot be combined with --out" in result.output

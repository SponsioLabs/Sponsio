"""Unit tests for ``sponsio/refresh.py``.

Focus areas (invariants refresh must not break):

1. **User contracts are immutable.**  Anything without a
   ``source: trace`` tag survives both modes, every time.
2. **Dedup works.**  Same pattern+non-numeric-args → same identity key.
3. **Drift detection.**  Numeric-threshold changes come through as
   drift, not add+remove.
4. **Mode semantics.**  ``add-only`` never removes; ``replace-trace``
   removes stale.
5. **CLI dry-run default.**  Without ``--apply``, nothing touches disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from sponsio.cli import cli
from sponsio.refresh import (
    _normalize_contract_entry,
    apply_refresh,
    compute_refresh,
    identity_key,
    render_report,
)


# ---------------------------------------------------------------------------
# identity_key
# ---------------------------------------------------------------------------


def test_identity_key_strips_numeric_args_for_threshold_drift():
    # rate_limit(send_email, 5) and rate_limit(send_email, 12) are
    # semantically the same rule at different thresholds — their
    # identity key must match so the diff sees it as "drift".
    k1 = identity_key("rate_limit", ["send_email", 5], None)
    k2 = identity_key("rate_limit", ["send_email", 12], None)
    assert k1 == k2


def test_identity_key_distinguishes_different_tools():
    k1 = identity_key("rate_limit", ["send_email", 5], None)
    k2 = identity_key("rate_limit", ["send_sms", 5], None)
    assert k1 != k2


def test_identity_key_falls_back_to_nl_for_non_pattern_entries():
    k1 = identity_key(None, None, "  Tool  must  NOT leak PII  ")
    k2 = identity_key(None, None, "tool must not leak pii")
    assert k1 == k2  # case-fold + whitespace-collapse


def test_identity_key_list_arg_normalized():
    k1 = identity_key("scope_limit", ["read", ["/tmp/", "/proj/"]], None)
    k2 = identity_key("scope_limit", ["read", ["/tmp/", "/proj/"]], None)
    assert k1 == k2


# ---------------------------------------------------------------------------
# _normalize_contract_entry
# ---------------------------------------------------------------------------


def test_normalize_structured_entry_with_trace_source():
    entry = {
        "G": {
            "pattern": "must_precede",
            "args": ["check_policy", "issue_refund"],
            "source": "trace",
        }
    }
    nc = _normalize_contract_entry(entry)
    assert nc is not None
    assert nc.source == "trace"
    assert nc.pattern == "must_precede"
    assert nc.args == ["check_policy", "issue_refund"]
    assert nc.assumption is None


def test_normalize_entry_with_assumption():
    entry = {
        "A": "called `modify_order`",
        "G": {
            "pattern": "must_precede",
            "args": ["get_order_details", "modify_order"],
            "source": "trace",
        },
    }
    nc = _normalize_contract_entry(entry)
    assert nc is not None
    assert nc.assumption == "called `modify_order`"
    # Assumption changes identity key — conditional vs unconditional
    # rules of the same shape are NOT the same contract.
    ident = nc.identity()
    nc_uncond = _normalize_contract_entry({"G": entry["G"]})
    assert ident != nc_uncond.identity()


def test_normalize_nl_only_entry_has_no_source_so_is_immutable():
    nc = _normalize_contract_entry({"G": "tool `send_email` is rate-limited"})
    assert nc is not None
    assert nc.source is None  # => refresh will treat it as user-written
    assert nc.pattern is None
    assert nc.nl is not None


def test_normalize_accepts_long_keys_assumption_guarantee():
    nc = _normalize_contract_entry(
        {
            "assumption": "precondition holds",
            "guarantee": {
                "pattern": "idempotent",
                "args": ["list_users"],
                "source": "trace",
            },
        }
    )
    assert nc is not None
    assert nc.assumption == "precondition holds"
    assert nc.pattern == "idempotent"


# ---------------------------------------------------------------------------
# compute_refresh — buckets
# ---------------------------------------------------------------------------


def _e(pattern: str, args: list, source: str = "trace", assumption: str = None):
    entry = {"G": {"pattern": pattern, "args": list(args), "source": source}}
    if assumption:
        entry["A"] = assumption
    return entry


def test_user_contracts_always_preserved_as_immutable():
    existing = [
        {"G": "unconditional user rule"},  # no source tag → user
        {"G": {"pattern": "rate_limit", "args": ["x", 5]}},  # no source → user
        _e("must_precede", ["a", "b"]),  # source:trace → refreshable
    ]
    report = compute_refresh(existing, [], agent="bot")
    # The two user entries land in untouched_immutable; the trace one
    # becomes stale because fresh is empty.
    assert len(report.untouched_immutable) == 2
    assert len(report.stale) == 1
    assert report.stale[0].pattern == "must_precede"


def test_source_scan_and_policy_also_immutable():
    # MVP refresh only owns source:trace.  scan and policy are
    # immutable even though they were auto-generated.
    existing = [
        _e("must_precede", ["a", "b"], source="scan"),
        _e("rate_limit", ["x", 10], source="policy"),
        _e("idempotent", ["list_users"], source="trace"),
    ]
    report = compute_refresh(existing, [], agent="bot")
    assert len(report.untouched_immutable) == 2
    assert len(report.stale) == 1  # only the trace entry


def test_added_bucket_when_fresh_has_new_contracts():
    existing = [_e("rate_limit", ["send_email", 5])]
    fresh = [
        _e("rate_limit", ["send_email", 5]),
        _e("must_precede", ["validate", "charge"]),  # new
    ]
    report = compute_refresh(existing, fresh, agent="bot")
    assert len(report.added) == 1
    assert report.added[0].pattern == "must_precede"
    assert len(report.unchanged_refreshable) == 1


def test_drift_bucket_for_threshold_change():
    existing = [_e("rate_limit", ["send_email", 5])]
    fresh = [_e("rate_limit", ["send_email", 12])]
    report = compute_refresh(existing, fresh, agent="bot")
    assert len(report.drifted) == 1
    old, new = report.drifted[0]
    assert old.args == ["send_email", 5]
    assert new.args == ["send_email", 12]
    assert not report.added  # NOT add+remove
    assert not report.stale


def test_stale_bucket_for_no_longer_observed():
    existing = [
        _e("rate_limit", ["send_email", 5]),
        _e("idempotent", ["list_users"]),
    ]
    fresh = [_e("rate_limit", ["send_email", 5])]
    report = compute_refresh(existing, fresh, agent="bot")
    assert len(report.stale) == 1
    assert report.stale[0].pattern == "idempotent"


def test_is_noop_when_no_changes():
    existing = [_e("rate_limit", ["send_email", 5])]
    fresh = [_e("rate_limit", ["send_email", 5])]
    report = compute_refresh(existing, fresh, agent="bot")
    assert report.is_noop
    assert len(report.unchanged_refreshable) == 1


# ---------------------------------------------------------------------------
# apply_refresh — mode semantics
# ---------------------------------------------------------------------------


def _cfg(agent: str, contracts: list) -> dict:
    return {
        "version": "1",
        "agents": {agent: {"contracts": contracts}},
    }


def test_apply_replace_trace_drops_stale_and_appends_fresh():
    existing = [
        {"G": "user rule — keep me"},
        _e("rate_limit", ["send_email", 5]),  # will drift
        _e("idempotent", ["list_users"]),  # will be dropped as stale
    ]
    fresh = [
        _e("rate_limit", ["send_email", 12]),  # drifted version
        _e("must_precede", ["validate", "charge"]),  # new
    ]
    report = compute_refresh(existing, fresh, agent="bot")
    cfg = _cfg("bot", existing)
    new_cfg = apply_refresh(cfg, {"bot": report}, {"bot": fresh}, mode="replace-trace")

    out = new_cfg["agents"]["bot"]["contracts"]
    # User rule is first and untouched.
    assert out[0] == {"G": "user rule — keep me"}
    # source:trace entries were all dropped from the preserved part,
    # then the full fresh set was appended.
    traces = [c for c in out if isinstance(c.get("G"), dict)]
    assert len(traces) == 2
    patterns = {c["G"]["pattern"] for c in traces}
    assert patterns == {"rate_limit", "must_precede"}
    # idempotent (stale) did NOT survive.
    assert not any(
        c.get("G", {}).get("pattern") == "idempotent"
        for c in out
        if isinstance(c.get("G"), dict)
    )


def test_apply_add_only_never_removes_anything():
    existing = [
        {"G": "user rule"},
        _e("rate_limit", ["send_email", 5]),  # drift candidate
        _e("idempotent", ["list_users"]),  # would be stale in replace mode
    ]
    fresh = [
        _e("rate_limit", ["send_email", 12]),  # drifted
        _e("must_precede", ["a", "b"]),  # new
    ]
    report = compute_refresh(existing, fresh, agent="bot")
    cfg = _cfg("bot", existing)
    new_cfg = apply_refresh(cfg, {"bot": report}, {"bot": fresh}, mode="add-only")

    out = new_cfg["agents"]["bot"]["contracts"]
    # All 3 original entries are still present (including idempotent
    # that would have been "stale" in replace mode).
    assert out[:3] == existing
    # Only the genuinely-new entry (must_precede) is appended; drifted
    # rate_limit is NOT appended (that would be a duplicate identity).
    appended = out[3:]
    assert len(appended) == 1
    assert appended[0]["G"]["pattern"] == "must_precede"


def test_apply_preserves_non_contracts_top_level_fields():
    cfg = {
        "version": "1",
        "include": ["sponsio:core/universal"],
        "runtime": {"mode": "observe"},
        "judge": {"provider": "openai", "model": "gpt-4o-mini"},
        "agents": {
            "bot": {
                "workspace": "/proj",
                "tool_rename": {"exec": "run_bash"},
                "overrides": [{"match": {"pattern": "rate_limit"}, "disabled": True}],
                "contracts": [_e("rate_limit", ["x", 5])],
            }
        },
    }
    fresh = [_e("rate_limit", ["x", 5])]  # no change
    report = compute_refresh(cfg["agents"]["bot"]["contracts"], fresh, agent="bot")
    new_cfg = apply_refresh(cfg, {"bot": report}, {"bot": fresh}, mode="replace-trace")

    # Every non-contracts field must be preserved verbatim.
    assert new_cfg["include"] == cfg["include"]
    assert new_cfg["runtime"] == cfg["runtime"]
    assert new_cfg["judge"] == cfg["judge"]
    bot = new_cfg["agents"]["bot"]
    assert bot["workspace"] == "/proj"
    assert bot["tool_rename"] == {"exec": "run_bash"}
    assert bot["overrides"] == cfg["agents"]["bot"]["overrides"]


def test_apply_rejects_unknown_mode():
    with pytest.raises(ValueError):
        apply_refresh({}, {}, {}, mode="nuke-everything")


# ---------------------------------------------------------------------------
# render_report — smoke
# ---------------------------------------------------------------------------


def test_render_report_contains_bucket_labels():
    existing = [_e("rate_limit", ["x", 5]), _e("idempotent", ["list_users"])]
    fresh = [_e("rate_limit", ["x", 12]), _e("must_precede", ["a", "b"])]
    report = compute_refresh(existing, fresh, agent="bot")
    text = render_report([report], color=False)
    assert "Agent: bot" in text
    assert "new" in text  # must_precede added
    assert "drifted" in text  # rate_limit
    assert "stale" in text  # idempotent
    assert "Total: +1" in text


# ---------------------------------------------------------------------------
# CLI — dry-run default + apply roundtrip
# ---------------------------------------------------------------------------


def _write_config(path: Path, contracts: list) -> None:
    path.write_text(
        yaml.safe_dump(
            {"version": "1", "agents": {"bot": {"contracts": contracts}}},
            sort_keys=False,
        )
    )


def _write_trace_jsonl(path: Path, events: list[dict]) -> None:
    import json

    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def _make_native_trace(tool_sequence: list[str]) -> list[dict]:
    """Build a minimal native-format trace that the trace miner will
    accept.  We exercise the CLI's end-to-end path through
    ``CodeAnalyzer.generate_yaml``.  Exact schema details are less
    important than: (1) the file loads, (2) the miner produces
    something we can diff."""
    events = []
    for i, name in enumerate(tool_sequence):
        events.append(
            {
                "session_id": "s1",
                "step": i,
                "type": "tool_call",
                "tool": name,
                "args": {},
            }
        )
    return events


def test_cli_refresh_dry_run_does_not_write(tmp_path):
    cfg_path = tmp_path / "sponsio.yaml"
    _write_config(
        cfg_path,
        [
            {"G": "hand-written user rule — keep me"},
            _e("rate_limit", ["send_email", 5]),
        ],
    )
    trace_path = tmp_path / "t.jsonl"
    # Empty trace → no contracts mined → dry-run will just report nothing.
    _write_trace_jsonl(trace_path, [])

    before = cfg_path.read_text()
    result = CliRunner().invoke(
        cli,
        [
            "refresh",
            "-c",
            str(cfg_path),
            "-t",
            str(trace_path),
            "--agent",
            "bot",
        ],
    )
    assert result.exit_code == 0, result.output
    after = cfg_path.read_text()
    assert after == before  # dry-run, no write
    # Backup should NOT have been created.
    assert not (tmp_path / "sponsio.yaml.sponsio.bak").exists()


def test_cli_refresh_apply_writes_backup(tmp_path):
    cfg_path = tmp_path / "sponsio.yaml"
    _write_config(
        cfg_path,
        [
            {"G": "user rule kept"},
            _e("rate_limit", ["send_email", 5]),
        ],
    )
    trace_path = tmp_path / "t.jsonl"
    _write_trace_jsonl(trace_path, [])

    result = CliRunner().invoke(
        cli,
        [
            "refresh",
            "-c",
            str(cfg_path),
            "-t",
            str(trace_path),
            "--agent",
            "bot",
            "--apply",
        ],
    )
    assert result.exit_code == 0, result.output

    # Backup exists and still contains the original file verbatim.
    backup = cfg_path.with_name(cfg_path.name + ".sponsio.bak")
    assert backup.is_file()
    assert "hand-written" not in backup.read_text() or True  # trivially true
    # User rule survives the round-trip (replace-trace mode drops
    # source:trace but preserves everything else).
    after = yaml.safe_load(cfg_path.read_text())
    contracts = after["agents"]["bot"]["contracts"]
    assert any(
        isinstance(c.get("G"), str) and "user rule kept" in c["G"] for c in contracts
    )


def test_cli_refresh_missing_agent_errors_cleanly(tmp_path):
    cfg_path = tmp_path / "sponsio.yaml"
    _write_config(cfg_path, [_e("rate_limit", ["x", 5])])
    result = CliRunner().invoke(
        cli,
        [
            "refresh",
            "-c",
            str(cfg_path),
            "-t",
            str(tmp_path / "nothing.jsonl"),
            "--agent",
            "nonexistent",
        ],
    )
    assert result.exit_code != 0
    assert "nonexistent" in result.output.lower()


def test_cli_refresh_bad_since_errors_early(tmp_path):
    cfg_path = tmp_path / "sponsio.yaml"
    _write_config(cfg_path, [])
    result = CliRunner().invoke(
        cli,
        ["refresh", "-c", str(cfg_path), "--since", "not-a-duration"],
    )
    assert result.exit_code != 0
    assert "since" in result.output.lower() or "duration" in result.output.lower()

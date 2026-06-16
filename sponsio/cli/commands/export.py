"""``sponsio export`` — convert a session dump to OTLP."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from sponsio.cli.app import cli


@cli.command(name="export")
@click.argument(
    "source",
    type=click.Path(exists=True, file_okay=True, dir_okay=True, path_type=Path),
)
@click.option(
    "--to",
    "target_dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    required=True,
    help="Output directory for OTLP-JSON trace files.",
)
@click.option(
    "--label",
    type=click.Choice(["safe", "unsafe", "none"]),
    default="safe",
    show_default=True,
    help=(
        "Filename prefix applied to each output trace.  ``safe`` / "
        "``unsafe`` make the file ready for `sponsio eval`; ``none`` "
        "preserves the input basename untouched (useful when you've "
        "already pre-labelled Sponsio-native dumps)."
    ),
)
@click.option(
    "--agent",
    "agent_id",
    default=None,
    help=(
        "Override the ``service.name`` stamped on the OTLP output.  "
        "Defaults to the ``metadata.agent_id`` in the source JSON, "
        "then to the first event's ``agent``, then to ``'agent'``."
    ),
)
@click.option(
    "--glob",
    "glob_pattern",
    default="*.json",
    show_default=True,
    help="Only convert files matching this glob (directory mode only).",
)
def export_cmd(
    source: Path,
    target_dir: Path,
    label: str,
    agent_id: str | None,
    glob_pattern: str,
):
    """Convert Sponsio-native trace dumps to OTLP JSON for ``sponsio eval``.

    The canonical flow from prod to eval corpus:

    \b
        # 1. In your agent (observe mode. never blocks):
        guard = BaseGuard(agent_id="bot", contracts=[...], mode="observe")
        # ...runs happen, violations logged but not enforced...

        # 2. Dump the accumulated trace to disk at session end:
        guard.trace.export("/var/log/sponsio/run.json")

        # 3. Later, convert a directory of these dumps into an eval corpus:
        sponsio export /var/log/sponsio/ --to traces/ --label safe

        # 4. Re-label incident traces and re-run eval:
        mv traces/safe_run_123.json traces/unsafe_run_123.json
        sponsio eval traces/ --config sponsio.yaml

    SOURCE may be a single ``.json`` file or a directory of them.
    Output filenames are ``{label}_{source-basename}.json``. the
    prefix is what ``sponsio eval`` reads to know which traces are
    expected to pass vs be blocked, so picking the right ``--label``
    at export time saves a rename pass later.
    """
    from sponsio.models.trace import Trace
    from sponsio.tracer.otel_writer import trace_to_otlp

    # Collect source files
    if source.is_file():
        sources = [source]
    else:
        sources = sorted(source.glob(glob_pattern))
        if not sources:
            click.echo(
                click.style(
                    f"No files matched {glob_pattern} under {source}", fg="yellow"
                ),
                err=True,
            )
            sys.exit(0)

    target_dir.mkdir(parents=True, exist_ok=True)

    converted = 0
    skipped: list[tuple[Path, str]] = []

    for src in sources:
        try:
            raw = json.loads(src.read_text())
        except (json.JSONDecodeError, OSError) as e:
            skipped.append((src, f"read: {e}"))
            continue

        # Accept either the bare Trace dict shape ({"events": [...], "metadata": {...}})
        # OR the richer ``export_trace()`` envelope (same shape, extra metadata).
        # Reject OTLP input. that's already in the target shape and would
        # silently duplicate rather than convert.
        if "resourceSpans" in raw:
            skipped.append((src, "already OTLP JSON. refusing to re-wrap"))
            continue
        if "events" not in raw:
            skipped.append((src, "no 'events' key. not a Sponsio trace dump"))
            continue

        try:
            trace = Trace.from_dict(raw)
        except (KeyError, TypeError) as e:
            skipped.append((src, f"parse: {e}"))
            continue

        effective_agent = (
            agent_id or (raw.get("metadata") or {}).get("agent_id") or None
        )
        otlp = trace_to_otlp(trace, agent_id=effective_agent)

        # Figure out output filename + label prefix
        stem = src.stem
        if label == "none":
            out_name = f"{stem}.json"
        else:
            # Don't double-prefix if the source already has safe_/unsafe_
            lowered = stem.lower()
            if lowered.startswith(("safe_", "safe-", "unsafe_", "unsafe-")):
                out_name = f"{stem}.json"
            else:
                out_name = f"{label}_{stem}.json"

        out_path = target_dir / out_name
        out_path.write_text(json.dumps(otlp, indent=2))
        converted += 1

    click.echo(
        click.style("✓ ", fg="green")
        + f"Converted {converted} trace(s) to {target_dir}"
    )
    if skipped:
        click.echo(click.style("  skipped:", fg="yellow"))
        for p, why in skipped:
            click.echo(f"    · {p.name}. {why}")


def _session_event_to_otlp_span(event: dict) -> dict:
    """Convert one ``MonitorEvent``-shaped JSONL record into an OTLP span.

    The session log captures *flat* monitor events (one row per
    contract verdict), not the full span tree. We synthesise a
    self-contained OTLP span per event so the dashboard's "Today's
    blocks" card has the same attribute keys it gets from live
    span-tree exports.

    Lossy on purpose: we don't re-derive the contract_check tree from
    flat events, so the violation card works but the rule-fire-heatmap
    won't have per-phase precondition / guarantee detail. That's
    acceptable for historical replay; live exports keep the full tree.
    """
    from sponsio.tracer import semconv

    ts_unix = float(event.get("ts") or 0.0)
    ts_ns = int(ts_unix * 1_000_000_000) if ts_unix else 0
    result = event.get("result") or {}
    action = result.get("action") or "allowed"
    blocked = action in ("blocked", "escalated", "observed")

    attrs: list[dict] = []
    if event.get("agent_id"):
        attrs.append(_attr_for_session(semconv.ATTR_AGENT_ID, event["agent_id"]))
    if event.get("action"):
        attrs.append(_attr_for_session(semconv.ATTR_EVENT_TOOL, event["action"]))
    if ts_ns:
        attrs.append(_attr_for_session(semconv.ATTR_EVENT_TIMESTAMP_NS, ts_ns))
    if event.get("pipeline"):
        # ``hard`` is the legacy alias; emit the public ``det`` name.
        pipeline = "det" if event["pipeline"] == "hard" else event["pipeline"]
        attrs.append(_attr_for_session(semconv.ATTR_CONTRACT_PIPELINE, pipeline))
    if event.get("constraint"):
        attrs.append(
            _attr_for_session(semconv.ATTR_CONTRACT_LABEL, event["constraint"])
        )
    attrs.append(_attr_for_session(semconv.ATTR_OUTCOME_BLOCKED, bool(blocked)))
    attrs.append(
        _attr_for_session(
            semconv.ATTR_OUTCOME_STATUS,
            "violated" if blocked else "ok",
        )
    )
    attrs.append(_attr_for_session(semconv.ATTR_ENFORCEMENT_ACTION, action))
    if result.get("message"):
        attrs.append(
            _attr_for_session(semconv.ATTR_VIOLATION_EVIDENCE, result["message"])
        )

    return {
        "traceId": "0" * 32,
        "spanId": f"{int(ts_unix * 1000):016x}" if ts_ns else "0" * 16,
        "name": semconv.SPAN_AGENT_TURN,
        "startTimeUnixNano": str(ts_ns or 0),
        "endTimeUnixNano": str(ts_ns or 0),
        "status": {"code": 2 if blocked else 1},
        "attributes": attrs,
    }


def _attr_for_session(key: str, value):
    """Local copy of otel_writer._attr. used by the session importer
    so we don't leak the writer's private API into this CLI command."""
    if isinstance(value, bool):
        v: dict = {"boolValue": value}
    elif isinstance(value, int):
        v = {"intValue": str(value)}
    elif isinstance(value, float):
        v = {"doubleValue": value}
    else:
        v = {"stringValue": str(value)}
    return {"key": key, "value": v}

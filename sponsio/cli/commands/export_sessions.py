"""``sponsio export-sessions`` — push session logs to OTLP."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import click

from sponsio.cli._shared import (
    _parse_since,
)
from sponsio.cli.app import cli

# OTLP span/attr converters are shared with the `export` command.
from sponsio.cli.commands.export import _attr_for_session, _session_event_to_otlp_span


@cli.command(name="export-sessions")
@click.option(
    "--since",
    default="24h",
    show_default=True,
    help=(
        "Time window relative to now: ``24h`` / ``7d`` / ``30m`` / "
        "``90s``, or ``all`` for no cutoff. Bare numbers default to "
        "hours."
    ),
)
@click.option(
    "--agent",
    "agent_filter",
    default=None,
    help=(
        "Only export sessions for this agent_id. Defaults to all "
        "agents under ``~/.sponsio/sessions/``."
    ),
)
@click.option(
    "--sessions-dir",
    "sessions_dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help=(
        "Override the source directory. Default: "
        "``$SPONSIO_SESSIONS_DIR`` or ``~/.sponsio/sessions/``."
    ),
)
@click.option(
    "--to",
    "destination",
    required=True,
    help=(
        "Output destination. Either an OTLP file path "
        "(``./traces.jsonl``) or an HTTP endpoint "
        "(``https://collector.example.com/v1/traces``)."
    ),
)
@click.option(
    "--header",
    "headers_raw",
    multiple=True,
    help=(
        "Extra HTTP headers as ``Key: Value``. May be specified "
        "multiple times. Auth keys, tenant ids etc. go here. Only "
        "honored when ``--to`` is an HTTP URL."
    ),
)
@click.option(
    "--batch-size",
    type=int,
    default=50,
    show_default=True,
    help="Spans per HTTP POST (HTTP destination only).",
)
@click.option(
    "--service-name",
    default=None,
    help=(
        "OTLP ``resource.service.name`` stamped on every exported "
        "span. Defaults to the per-agent_id of each session file."
    ),
)
def export_sessions_cmd(
    since: str,
    agent_filter: str | None,
    sessions_dir: Path | None,
    destination: str,
    headers_raw: tuple[str, ...],
    batch_size: int,
    service_name: str | None,
):
    """Ship audit-log session events to an OTLP destination.

    Reads ``~/.sponsio/sessions/<agent_id>/*.jsonl``, converts each
    ``MonitorEvent`` row into an OTLP span using the Sponsio Semantic
    Conventions (see ``docs/reference/observability.md``), and writes them
    either to a local OTLP-JSONL file or POSTs them to an OTLP/HTTP
    collector (Datadog, Honeycomb, Grafana Cloud, the Sponsio-native
    dashboard, …).

    \b
    Examples:
      # Last 24h of audit, all agents, push to your dashboard
      sponsio export-sessions --to https://obs.example.com/v1/traces \\
                              --header "x-api-key: $OBS_API_KEY"

      # Last 7d of one agent, write to a file
      sponsio export-sessions --since 7d --agent _host_cursor \\
                              --to ./audit-export.jsonl

      # Everything we have, no time cutoff
      sponsio export-sessions --since all --to ./full-audit.jsonl

    The session log is the audit substrate (``MonitorEvent``-flat
    records); the runtime span tree (per-phase precondition /
    guarantee / sto_eval children) is *not* persisted to disk, so
    historical exports are necessarily lossy on per-phase detail.
    Live exports via :class:`sponsio.tracer.exporters.OtlpHttpExporter`
    carry the full tree.
    """
    from sponsio.runtime.session_log import _resolve_default_base_dir

    cutoff = _parse_since(since)
    base = (
        sessions_dir.expanduser()
        if sessions_dir is not None
        else _resolve_default_base_dir()
    )

    if not base.exists():
        click.echo(
            click.style(f"sessions dir not found: {base}", fg="yellow"),
            err=True,
        )
        sys.exit(0)

    # Walk per-agent subdirectories.
    agent_dirs: list[Path]
    if agent_filter is not None:
        agent_dirs = [base / agent_filter]
        if not agent_dirs[0].is_dir():
            click.echo(
                click.style(f"no sessions for agent {agent_filter!r}", fg="yellow"),
                err=True,
            )
            sys.exit(0)
    else:
        agent_dirs = [p for p in base.iterdir() if p.is_dir()]

    spans: list[dict] = []
    by_agent: dict[str, int] = {}

    for agent_dir in sorted(agent_dirs):
        agent_id = agent_dir.name
        for jsonl_path in sorted(agent_dir.glob("*.jsonl")):
            try:
                lines = jsonl_path.read_text().splitlines()
            except OSError as e:
                click.echo(
                    click.style(f"  skip {jsonl_path}: {e}", fg="yellow"), err=True
                )
                continue
            for ln in lines:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rec = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if cutoff and float(rec.get("ts") or 0.0) < cutoff:
                    continue
                spans.append(_session_event_to_otlp_span(rec))
                by_agent[agent_id] = by_agent.get(agent_id, 0) + 1

    if not spans:
        click.echo(
            click.style(
                f"no events matched (since={since}, agent={agent_filter})",
                fg="yellow",
            ),
            err=True,
        )
        sys.exit(0)

    # Emit one OTLP envelope.
    from sponsio.tracer import semconv as _semconv

    envelope = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        _attr_for_session(
                            "service.name",
                            service_name or "sponsio-sessions",
                        ),
                    ],
                },
                "scopeSpans": [
                    {
                        "scope": {
                            "name": "sponsio",
                            "version": _semconv.SCHEMA_VERSION,
                        },
                        "schemaUrl": _semconv.SCHEMA_URL,
                        "spans": spans,
                    }
                ],
            }
        ],
    }

    if destination.startswith(("http://", "https://")):
        # HTTP push via the in-tree batching exporter.
        headers: dict[str, str] = {}
        for raw in headers_raw:
            if ":" not in raw:
                raise click.BadParameter(f"--header must be 'Key: Value' (got {raw!r})")
            k, _, v = raw.partition(":")
            headers[k.strip()] = v.strip()

        body = json.dumps(envelope).encode("utf-8")
        click.echo(
            f"POSTing {len(spans)} spans ({len(body) / 1024:.1f} KB) → {destination}"
        )
        try:
            req = urllib.request.Request(
                destination,
                data=body,
                headers={"Content-Type": "application/json", **headers},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                if not (200 <= resp.status < 300):
                    click.echo(
                        click.style(
                            f"collector returned HTTP {resp.status}",
                            fg="red",
                        ),
                        err=True,
                    )
                    sys.exit(1)
        except urllib.error.URLError as e:
            click.echo(click.style(f"HTTP push failed: {e}", fg="red"), err=True)
            sys.exit(1)
        click.secho(f"✓ pushed {len(spans)} spans", fg="green")
    else:
        # File destination. write the OTLP envelope as a single JSON.
        out = Path(destination).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(envelope, indent=2))
        click.secho(
            f"✓ wrote {len(spans)} spans → {out} ({out.stat().st_size / 1024:.1f} KB)",
            fg="green",
        )

    # Summary by agent. useful when --agent isn't set.
    if by_agent:
        click.echo()
        click.echo(click.style("By agent:", bold=True))
        for agent_id, n in sorted(by_agent.items(), key=lambda x: -x[1]):
            click.echo(f"  {agent_id:30}  {n:6} events")

    click.echo()
    click.echo(
        click.style("Schema: ", dim=True)
        + f"{_semconv.SCHEMA_URL} (version {_semconv.SCHEMA_VERSION})"
    )

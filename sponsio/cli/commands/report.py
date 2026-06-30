"""``sponsio report`` — summarise recent session logs."""

from __future__ import annotations

from pathlib import Path

import click

from sponsio.cli.app import cli


@cli.command()
@click.option(
    "--since",
    default="7d",
    show_default=True,
    help="Time window: 'all', '30m', '24h', '7d'.",
)
@click.option(
    "--agent",
    default=None,
    help="Filter to one agent_id. Default: every agent under ~/.sponsio/sessions.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(
        ["auto", "rich", "markdown", "md", "html", "json", "plain"],
        case_sensitive=False,
    ),
    default="auto",
    show_default=True,
    help=(
        "Output format. ``auto`` picks rich for an interactive terminal, "
        "markdown for piped/CI output, or plain when NO_COLOR is set."
    ),
)
@click.option(
    "--out",
    "-o",
    "out_path",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Write report to this file. Default: stdout.",
)
@click.option(
    "--save-svg",
    "save_svg",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Save the rich-rendered output to an SVG file (vector, retina-safe).",
)
@click.option(
    "--live",
    is_flag=True,
    default=False,
    help="Watch mode: re-render every --interval seconds. Ctrl+C to exit.",
)
@click.option(
    "--interval",
    default=2.0,
    show_default=True,
    type=float,
    help="Seconds between refreshes in --live mode.",
)
@click.option(
    "--base-dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Override the session log directory (default: ~/.sponsio/sessions).",
)
def report(
    since: str,
    agent: str | None,
    fmt: str,
    out_path: str | None,
    save_svg: str | None,
    live: bool,
    interval: float,
    base_dir: str | None,
):
    """Summarize shadow-mode session logs into a shareable report.

    \b
    Examples:
      sponsio report                                    # rich on TTY, markdown if piped
      sponsio report --agent support_bot --since 24h    # one agent, last day
      sponsio report --format html -o report.html       # HTML to file
      sponsio report --format json --since all          # machine-readable dump
      sponsio report --save-svg report.svg              # rich + SVG export
      sponsio report --live                             # watch mode, refreshes every 2s

    Reads JSONL files written by ``mode='observe'`` (shadow mode) from
    ``~/.sponsio/sessions/<agent_id>/*.jsonl``.  Nothing is modified.
    """
    # Lazy imports so `sponsio --help` stays fast.

    from sponsio.render import pick_format
    from sponsio.reporting import aggregate, load_events, render
    from sponsio.reporting.reader import parse_since

    # Validate --since up front so we fail fast with a readable error.
    try:
        parse_since(since)
    except ValueError as e:
        click.echo(click.style(f"Error: {e}", fg="red"))
        raise SystemExit(2)

    bd = Path(base_dir) if base_dir else None
    resolved_fmt = pick_format(fmt)

    # SVG export requires the Rich path. promote auto/markdown to rich if asked.
    if save_svg and resolved_fmt != "rich":
        resolved_fmt = "rich"

    def _aggregate_once():
        events = load_events(since=since, agent=agent, base_dir=bd)
        return aggregate(events)

    def _render_text(report_obj) -> str:
        """Non-rich text output (markdown/html/json/plain)."""
        target = "markdown" if resolved_fmt == "plain" else resolved_fmt
        return render(report_obj, fmt=target)

    def _emit_rich(report_obj) -> None:
        """Rich path. prints directly + optionally writes SVG."""
        from sponsio.render.rich_report import render_report, save_svg as _save_svg

        console = render_report(report_obj)
        if save_svg:
            _save_svg(
                console,
                save_svg,
                title=f"Sponsio · report --since {since}",
            )
            click.echo(
                click.style("Wrote ", fg="green") + save_svg + " (SVG export)",
                err=True,
            )

    if live:
        if out_path is not None:
            click.echo(
                click.style("Error: ", fg="red")
                + "--live cannot be combined with --out."
            )
            raise SystemExit(2)
        if save_svg is not None:
            click.echo(
                click.style("Error: ", fg="red")
                + "--live cannot be combined with --save-svg."
            )
            raise SystemExit(2)
        import time as _time

        try:
            while True:
                # ANSI clear-screen + home cursor; harmless on non-TTY.
                click.echo("\x1b[2J\x1b[H", nl=False)
                report_obj = _aggregate_once()
                if resolved_fmt == "rich":
                    _emit_rich(report_obj)
                else:
                    click.echo(_render_text(report_obj))
                _time.sleep(max(0.25, interval))
        except KeyboardInterrupt:
            click.echo("\n(live mode stopped)")
            return

    report_obj = _aggregate_once()
    if resolved_fmt == "rich":
        _emit_rich(report_obj)
        if out_path is not None:
            click.echo(
                click.style("Note: ", fg="yellow")
                + "--out ignored with rich format; use --save-svg for export.",
                err=True,
            )
        return

    out = _render_text(report_obj)
    if out_path is None:
        click.echo(out, nl=False)
    else:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(out)
        click.echo(
            click.style("Wrote ", fg="green")
            + out_path
            + f" ({len(out)} bytes, format={resolved_fmt})"
        )

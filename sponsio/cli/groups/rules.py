"""``sponsio rules`` — what the daemon enforces, and held rule changes.

A library edit that keeps every loaded contract applies at once. One
that removes or changes a contract is held: the daemon keeps enforcing
the previous rules and records a ``config_drift`` until someone runs
``sponsio rules reload`` here, on the admin socket. Run it from your
own terminal; the agent's hooks deny it.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from sponsio.cli.app import cli


def _admin(socket_path: Path | None):
    from sponsio.daemon.client import DaemonClient, default_admin_socket_path
    from sponsio.daemon.server import admin_socket_for

    path = admin_socket_for(socket_path) if socket_path else default_admin_socket_path()
    return DaemonClient(socket_path=path, timeout_s=10.0)


def _call(socket_path: Path | None, method: str, params: dict | None = None):
    from sponsio.daemon.client import DaemonError

    try:
        return _admin(socket_path).call(method, params or {})
    except DaemonError as e:
        raise click.ClickException(f"{e} (code={e.code})") from e


_socket_option = click.option(
    "--socket",
    "socket_path",
    type=click.Path(path_type=Path),
    default=None,
    help="The daemon's agent socket; its admin socket is beside it.",
)


@cli.group()
def rules():
    """Rules the daemon enforces, and rule changes waiting for review."""


@rules.command(name="status")
@_socket_option
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def rules_status(socket_path: Path | None, as_json: bool):
    """Loaded libraries, and which have held changes."""
    st = _call(socket_path, "rules.status")
    if as_json:
        click.echo(json.dumps(st, indent=2))
        return
    for bucket, info in sorted(st["loaded"].items()):
        mark = "HELD" if bucket in st["held"] or info["held"] else "ok"
        click.echo(f"{mark:5} {bucket}  {info['path']}")
    if not st["held"]:
        click.echo("no held changes")
    if st.get("watch"):
        click.echo("(--watch: changes apply at once)")


@rules.command(name="diff")
@_socket_option
def rules_diff(socket_path: Path | None):
    """What a reload would apply, per bucket."""
    held = _call(socket_path, "rules.status")["held"]
    if not held:
        click.echo("no held changes")
        return
    for bucket, h in sorted(held.items()):
        click.secho(f"{bucket}  ({h['path']}, held since {h['since']})", bold=True)
        for name in h["removed"]:
            click.secho(f"  - {name}", fg="red")
        for name in h["added"]:
            click.secho(f"  + {name}", fg="green")


@rules.command(name="verify")
@_socket_option
def rules_verify(socket_path: Path | None):
    """Compare loaded rules with the book the cloud publishes, now.

    Uses the ``cloud:`` section of the daemon's daemon.yaml (project, and
    which agent each host bucket should match). The daemon also does this
    on its own every ``verify_interval_s``.
    """
    results = _call(socket_path, "rules.verify")
    if not results:
        click.echo("no buckets linked to a cloud project (cloud: in daemon.yaml)")
        return
    colours = {"match": "green", "stronger": "green", "weaker": "red"}
    for bucket, r in sorted(results.items()):
        status = r.get("status", "?")
        line = f"{status:12} {bucket}"
        if r.get("published_version") is not None:
            line += f"  (cloud {r.get('agent')} v{r['published_version']})"
        click.secho(line, fg=colours.get(status, "yellow"))
        for name in r.get("missing", []):
            click.echo(f"  - {name}")
        if r.get("detail"):
            click.echo(f"  {r['detail']}")


@rules.command(name="reload")
@_socket_option
@click.option("--bucket", default=None, help="Only this bucket (default: all).")
@click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
def rules_reload(socket_path: Path | None, bucket: str | None, yes: bool):
    """Apply held rule changes. Recorded with who ran it."""
    held = _call(socket_path, "rules.status")["held"]
    targets = [bucket] if bucket else sorted(held)
    targets = [b for b in targets if b in held]
    if not targets:
        click.echo("no held changes")
        return
    for b in targets:
        removed = held[b]["removed"]
        click.echo(f"{b}: {len(removed)} contract(s) removed or changed")
        for name in removed:
            click.echo(f"  - {name}")
    if not yes and not click.confirm("Apply these changes?", default=False):
        raise click.Abort()
    done = _call(socket_path, "rules.reload", {"bucket": bucket})
    click.secho(f"reloaded: {', '.join(done['reloaded']) or 'nothing'}", fg="green")

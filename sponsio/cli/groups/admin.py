"""``sponsio admin``: what an operator does to the daemon.

Every command here talks to the daemon's admin socket. At Tier 1 that is
this user's own daemon; at Tier 2 the socket belongs to the daemon's
user, so run these with ``sudo``. Run them from your own terminal: the
agent's hooks deny them.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from sponsio.cli.app import cli
from sponsio.cli.groups.rules import _call, _socket_option


@cli.group()
def admin():
    """Operator commands: proposals and the audit log."""


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------


@admin.group()
def proposals():
    """Rule changes submitted from the agent side, waiting for a person."""


@proposals.command(name="list")
@_socket_option
@click.option(
    "--status",
    type=click.Choice(["pending", "approved", "rejected"]),
    default="pending",
    show_default=True,
)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def proposals_list(socket_path: Path | None, status: str, as_json: bool):
    """Proposals with this status, oldest first."""
    found = _call(socket_path, "proposal.list", {"status": status})["proposals"]
    if as_json:
        click.echo(json.dumps(found, indent=2))
        return
    if not found:
        click.echo(f"no {status} proposals")
        return
    for p in found:
        peer = p.get("submitted_by") or {}
        who = f"uid {peer.get('uid')}" if peer else "unknown"
        preview = p.get("preview") or {}
        descs = preview.get("descs") or []
        what = f"+{len(descs)} contract(s) to {preview.get('agent_id') or '?'}"
        click.echo(
            f"{p['id']}  {p['kind']}  {what}  ({who}, {p.get('submitted_at', '')})"
        )
        for d in descs:
            click.echo(f"    + {d}")


@proposals.command(name="show")
@_socket_option
@click.argument("proposal_id")
def proposals_show(socket_path: Path | None, proposal_id: str):
    """Everything about one proposal, including what it would change."""
    click.echo(
        json.dumps(_call(socket_path, "proposal.show", {"id": proposal_id}), indent=2)
    )


def _decide(socket_path: Path | None, proposal_id: str, approve: bool, yes: bool):
    p = _call(socket_path, "proposal.show", {"id": proposal_id})
    click.echo(json.dumps(p.get("preview") or p.get("params") or {}, indent=2))
    verb = "approve" if approve else "reject"
    if not yes and not click.confirm(f"{verb} {proposal_id}?", default=False):
        raise click.Abort()
    method = "proposal.approve" if approve else "proposal.reject"
    out = _call(socket_path, method, {"id": proposal_id})
    click.echo(f"{proposal_id}: {out.get('status', verb + 'd')}")


@proposals.command(name="approve")
@_socket_option
@click.argument("proposal_id")
@click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
def proposals_approve(socket_path: Path | None, proposal_id: str, yes: bool):
    """Apply a proposal. Recorded with who approved it."""
    _decide(socket_path, proposal_id, True, yes)


@proposals.command(name="reject")
@_socket_option
@click.argument("proposal_id")
@click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
def proposals_reject(socket_path: Path | None, proposal_id: str, yes: bool):
    """Refuse a proposal. Recorded with who rejected it."""
    _decide(socket_path, proposal_id, False, yes)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


@admin.group()
def audit():
    """The daemon's hash-chained audit log."""


@audit.command(name="verify")
@_socket_option
@click.option(
    "--state-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Check this state dir's log directly, without asking the daemon.",
)
def audit_verify(socket_path: Path | None, state_dir: Path | None):
    """Check that no audit record was edited, removed or reordered.

    Asks the running daemon, which also knows the last record it wrote,
    so records cut from the end are caught. With ``--state-dir`` (or no
    daemon) the file is checked on its own, and only the chain is.
    Exits 1 when the chain is broken.
    """
    from sponsio.daemon import audit as audit_log

    if state_dir is None:
        from sponsio.daemon.client import DaemonError

        try:
            from sponsio.cli.groups.rules import _admin

            out = _admin(socket_path).call("audit.verify", {})
        except DaemonError as e:
            if e.code not in ("no_daemon", "transport"):
                raise click.ClickException(f"{e} (code={e.code})") from e
            from sponsio.daemon.guard_service import default_state_dir

            state_dir = default_state_dir()
            click.echo(f"no daemon answered; checking {state_dir} on its own")
        else:
            _report(out)
            return
    report = audit_log.verify(state_dir.expanduser())
    _report(
        {
            "ok": report.ok,
            "records": report.records,
            "chained": report.chained,
            "unchained": report.unchained,
            "head": list(report.head) if report.head else None,
            "daemon_head": None,
            "problems": report.problems,
            "path": str(audit_log.audit_path(state_dir.expanduser())),
        }
    )


def _report(out: dict) -> None:
    click.echo(f"{out['path']}: {out['records']} records, {out['chained']} chained")
    if out.get("unchained"):
        click.echo(f"  {out['unchained']} older records predate the chain")
    if out.get("daemon_head"):
        click.echo(f"  the daemon last wrote seq {out['daemon_head'][0]}")
    if out["ok"]:
        click.secho("chain intact", fg="green")
        return
    for problem in out["problems"]:
        click.secho(f"  {problem}", fg="red")
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# The daemon's cloud key
# ---------------------------------------------------------------------------


@admin.group(name="cloud-key")
def cloud_key():
    """The API key the daemon uses for the cloud, kept in its state dir."""


@cloud_key.command(name="set")
@_socket_option
@click.option(
    "--from-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Read the key from this file instead of a prompt.",
)
def cloud_key_set(socket_path: Path | None, from_file: Path | None):
    """Give the daemon its own key. Only a fingerprint is recorded.

    At Tier 2 this is the key with publish scope; the one in your own
    ~/.sponsio/credentials should only read and ingest.
    """
    if from_file is not None:
        key = from_file.read_text(encoding="utf-8").strip()
    else:
        key = click.prompt("API key", hide_input=True).strip()
    out = _call(socket_path, "cloud.set_key", {"key": key})
    click.echo(f"key {out['fingerprint']} saved to {out['path']}")


@cloud_key.command(name="status")
@_socket_option
def cloud_key_status(socket_path: Path | None):
    """Which key the daemon uses, by fingerprint."""
    out = _call(socket_path, "cloud.key_status")
    if out.get("source") is None:
        click.echo("no key: run `sponsio admin cloud-key set`")
        return
    where = "its state dir" if out["source"] == "daemon" else "your sponsio login"
    click.echo(f"key {out['fingerprint']} from {where}")

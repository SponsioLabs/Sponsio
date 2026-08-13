"""``sponsio login`` / ``sponsio pull`` / ``sponsio push`` — the cloud path.

None of these are required to use Sponsio. Enforcement is local and free;
these three exist for the case where a rulebook is shared with a team or
tuned in the console rather than edited in the repo.

``pull`` in particular is a diagnostic, not a step: a guard configured with
``sponsio://project`` checks out on its own at construction. Running it by
hand is for seeing what the SDK would get.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from sponsio.cli.app import cli


def _client(url: str | None = None, api_key: str | None = None):
    from sponsio.cloud.client import CloudClient

    return CloudClient(url=url, api_key=api_key)


@cli.command()
@click.option(
    "--key", "key", help="API key. Omitted means read it from stdin or prompt."
)
@click.option(
    "--url", "url", help="API base URL (default: $SPONSIO_API_URL or app.sponsio.dev)"
)
def login(key: str | None, url: str | None) -> None:
    """Save an API key after checking that it works."""
    from sponsio.cloud.client import CloudError, write_api_key

    if not key:
        # Piping (`pbpaste | sponsio login`) beats an echoing prompt for a
        # credential; fall back to a hidden prompt when there is a terminal.
        if not sys.stdin.isatty():
            key = sys.stdin.read().strip()
        else:
            key = click.prompt("API key", hide_input=True).strip()
    if not key:
        raise click.ClickException("no key given")

    client = _client(url=url, api_key=key)
    try:
        identity = client.whoami()
    except CloudError as exc:
        # Never save a key we could not verify: a saved-but-broken key turns
        # into a confusing failure inside an agent run days later.
        raise click.ClickException(f"key not accepted by {client.url}: {exc}") from exc

    path = write_api_key(key, url=client.url)
    tenant = (identity.get("tenant") or {}).get("name") or "unknown"
    click.echo(f"logged in to {client.url} as {tenant}")
    click.echo(f"key saved to {path} (0600)")
    projects = identity.get("projects") or []
    if projects:
        click.echo("projects: " + ", ".join(projects))


@cli.command()
@click.argument("project", required=False)
@click.option(
    "--agent", "agent", help="Pull one agent's book instead of the whole project"
)
@click.option("--version", "version", type=int, help="Pin a version (requires --agent)")
@click.option(
    "-o", "--output", "output", type=click.Path(), help="Write here instead of stdout"
)
@click.option("--url", "url", help="API base URL")
def pull(
    project: str | None,
    agent: str | None,
    version: int | None,
    output: str | None,
    url: str | None,
) -> None:
    """Download a project's rulebook as loadable yaml.

    A guard with ``config="sponsio://<project>"`` does this itself at
    construction. Use this to inspect what it would get.
    """
    from sponsio.cloud.client import CloudError

    client = _client(url=url)
    if not client.configured:
        raise click.ClickException(
            "no API key: set SPONSIO_API_KEY or run `sponsio login`"
        )
    if version is not None and agent is None:
        raise click.ClickException("--version needs --agent: versions are per agent")

    try:
        pulled = client.pull_rulebook(
            project or "default", agent=agent, version=version
        )
    except CloudError as exc:
        raise click.ClickException(str(exc)) from exc

    if output:
        Path(output).write_text(pulled.yaml_text)
        click.echo(f"wrote {output} · {pulled.versions or '?'}")
    else:
        click.echo(pulled.yaml_text, nl=False)


@cli.command()
@click.argument("config_path", type=click.Path(exists=True))
@click.option("--project", "project", help="Project to publish into (created if new)")
@click.option("--url", "url", help="API base URL")
def push(config_path: str, project: str | None, url: str | None) -> None:
    """Publish a local sponsio.yaml as the next version of each agent's book."""
    from sponsio.cloud.client import CloudError

    client = _client(url=url)
    if not client.configured:
        raise click.ClickException(
            "no API key: set SPONSIO_API_KEY or run `sponsio login`"
        )

    try:
        result = client.push_rulebook(
            project or "default", Path(config_path).read_text()
        )
    except CloudError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"pushed to project '{result.get('project')}'")
    for name, book in (result.get("agents") or {}).items():
        # Say plainly when nothing moved. An agent that re-pushes on every
        # start would otherwise look like it keeps changing the book.
        state = "unchanged" if book.get("unchanged") else "new version"
        click.echo(
            f"  {name}: v{book.get('version')} · {book.get('rules')} rules · {state}"
        )


@cli.command()
@click.option("--url", "url", help="API base URL")
def projects(url: str | None) -> None:
    """List the projects this key can reach.

    Without this, ``pull``'s default of ``default`` is undiscoverable: the
    only way to learn a project name was to already know it.
    """
    from sponsio.cloud.client import CloudError

    client = _client(url=url)
    if not client.configured:
        raise click.ClickException(
            "no API key: set SPONSIO_API_KEY or run `sponsio login`"
        )

    try:
        identity = client.whoami()
    except CloudError as exc:
        raise click.ClickException(str(exc)) from exc

    names = identity.get("projects") or []
    if not names:
        click.echo(
            "no projects yet — `sponsio push sponsio.yaml --project <name>` creates one"
        )
        return
    for name in names:
        click.echo(name)
    agents = identity.get("agents_with_rulebooks") or []
    if agents:
        click.echo("\nagents with a published rulebook: " + ", ".join(agents))

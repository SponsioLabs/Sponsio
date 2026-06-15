"""``sponsio packs`` — list shipped contract packs."""

from __future__ import annotations


import click

from sponsio.cli._shared import (
    _contract_guarantee,
)
from sponsio.cli.app import cli


@cli.command()
def packs():
    """List shipped contract packs with rule counts + include syntax.

    Useful right after ``sponsio scan`` / ``sponsio onboard``: the
    generated :file:`sponsio.yaml` references packs by ``include:``
    spec, and this command prints the full inventory plus one-line
    summaries so users can see what's been pulled in without opening
    five YAML files.
    """
    # We walk the shipped contracts directory rather than hardcoding
    # a table so new packs become visible the moment they're added.
    from collections import Counter
    from importlib.resources import files

    import yaml as _yaml

    try:
        contracts_root = files("sponsio") / "contracts"
    except (ModuleNotFoundError, FileNotFoundError):
        click.echo("error: sponsio package not found on import path", err=True)
        raise SystemExit(1) from None

    rows = []  # (spec, desc_line, n_contracts, kinds_summary, needs_workspace)
    for category_dir in sorted(contracts_root.iterdir()):
        if not category_dir.is_dir():
            continue
        for pack_file in sorted(category_dir.iterdir()):
            if not pack_file.is_file() or pack_file.suffix not in (".yaml", ".yml"):
                continue
            spec = f"sponsio:{category_dir.name}/{pack_file.stem}"
            try:
                text = pack_file.read_text(encoding="utf-8")
                doc = _yaml.safe_load(text) or {}
                # Header comment's first meaningful sentence gives the
                # summary.  Fallback to "(no summary)" if the pack didn't
                # follow the convention.
                summary = "(no summary)"
                for line in text.splitlines():
                    stripped = line.lstrip("#").strip()
                    if not stripped or stripped.startswith("="):
                        continue
                    if stripped.startswith("sponsio/contracts/"):
                        continue
                    summary = stripped
                    break

                agents = doc.get("agents") or {}
                template = agents.get("*") or next(iter(agents.values()), {})
                contracts = (template or {}).get("contracts") or []
                n = len(contracts)

                # Rough kind count. det patterns vs raw LTL.  OSS ships
                # no sto pipeline; the third bucket is gone.
                kinds = Counter()
                for c in contracts:
                    es = _contract_guarantee(c)
                    if isinstance(es, dict):
                        es_list = [es]
                    elif isinstance(es, list):
                        es_list = es
                    else:
                        es_list = []
                    for e in es_list:
                        if not isinstance(e, dict):
                            continue
                        if "ltl" in e and "pattern" not in e:
                            kinds["raw"] += 1
                        elif e.get("pattern"):
                            kinds["det"] += 1

                needs_ws = "<workspace>/" in text
                rows.append((spec, summary, n, dict(kinds), needs_ws))
            except Exception as exc:  # noqa: BLE001
                rows.append((spec, f"(unreadable: {exc})", 0, {}, False))

    click.echo()
    click.echo(click.style("Shipped contract packs", bold=True))
    click.echo()
    for spec, summary, n, kinds, needs_ws in rows:
        badge = " [needs workspace:]" if needs_ws else ""
        click.echo(click.style(f"  {spec}{badge}", fg="cyan", bold=True))
        k = ", ".join(f"{v} {k}" for k, v in kinds.items()) or f"{n} contracts"
        click.echo(f"    {n} contracts ({k})")
        click.echo(click.style(f"    {summary}", dim=True))
        click.echo()
    click.echo("Use in sponsio.yaml:")
    click.echo("  agents:")
    click.echo("    your_agent:")
    click.echo("      include:")
    for spec, *_ in rows:
        click.echo(f"        - {spec}")

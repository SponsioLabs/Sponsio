"""``sponsio scan`` — generate contracts from code / policy."""

from __future__ import annotations

import json
import os
from pathlib import Path

import click

from sponsio.cli.app import cli


@cli.command()
@click.argument("paths", nargs=-1, required=True)
@click.option("--agent", "-a", default="agent", help="Agent ID for generated config")
@click.option(
    "--llm", is_flag=True, help="Enable LLM inference (auto-detects provider from env)"
)
@click.option("--model", "-m", default=None, help="LLM model (default: auto-detect)")
@click.option(
    "--provider",
    default=None,
    type=click.Choice(["openai", "anthropic", "gemini"]),
    help=(
        "LLM provider (default: auto-detect from env). "
        "Anthropic uses ANTHROPIC_API_KEY; Gemini uses GOOGLE_API_KEY "
        "or GEMINI_API_KEY (1500 req/day free tier)."
    ),
)
@click.option(
    "--base-url",
    default=None,
    help=(
        "OpenAI-compatible HTTP endpoint. Covers Ollama (local), "
        "OpenRouter, DeepSeek, Together, Groq, vLLM, Azure OpenAI. "
        "Reads OPENAI_BASE_URL env if not given."
    ),
)
@click.option(
    "--out",
    "-o",
    type=click.Path(),
    default=None,
    help=(
        "Write YAML to this path. Defaults to `./sponsio.yaml`. "
        "Use `-o -` to print to stdout for piping."
    ),
)
@click.option(
    "--append", is_flag=True, help="Append to existing file instead of overwriting"
)
@click.option(
    "--policy",
    "-p",
    multiple=True,
    type=click.Path(exists=True),
    help="Policy document (.md/.txt) to extract constraints from",
)
@click.option(
    "--push/--no-push",
    default=False,
    help=(
        "Push the YAML to the local dashboard at --push-url "
        "(default: off). The dashboard is an optional observability "
        "companion; opt in explicitly so `sponsio scan` is a pure, "
        "offline code-gen step by default."
    ),
)
@click.option(
    "--push-url",
    default="http://127.0.0.1:8000",
    help="Dashboard URL to push to (default: http://127.0.0.1:8000)",
)
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help=(
        "Read provider/model/api_key from sponsio.yaml's `extractor:` "
        "section.  Implies --llm.  Explicit --provider/--model/--base-url "
        "still win over YAML values."
    ),
)
@click.option(
    "--emit-context",
    "emit_context",
    is_flag=True,
    default=False,
    help=(
        "Skip the LLM step and instead emit the structured inputs "
        "(framework / tool inventory / scanned code excerpts / policy "
        "docs) as JSON to stdout.  Used by the host "
        "agent driving the ``sponsio`` skill: pair with "
        "``sponsio prompt scan`` and apply in the agent's own LLM "
        "context. no UnifiedExtractor call, no extra API key."
    ),
)
def scan(
    paths: tuple[str, ...],
    agent: str,
    llm: bool,
    model: str | None,
    provider: str | None,
    base_url: str | None,
    out: str | None,
    append: bool,
    policy: tuple[str, ...],
    push: bool,
    push_url: str,
    config_path: str | None,
    emit_context: bool,
):
    """Scan source code and policy docs to propose contracts.

    For first-time setup, prefer ``sponsio onboard``. it composes
    framework detection + scan + ``init``-style provider config +
    ``doctor`` health checks into a single command.  ``scan`` is the
    library-maintenance tool you reach for *after* you have a
    ``sponsio.yaml``: re-mine contracts from new code or append from
    a policy doc.

    Analyzes tool definitions, decorators, and call patterns to infer
    safety constraints. Optionally extracts constraints from policy
    documents (.md/.txt) using the discovered tool inventory as context.

    \b
    Examples:
      sponsio scan src/                                # writes ./sponsio.yaml (rule-based)
      sponsio scan src/ --llm                          # + LLM inference
      sponsio scan src/ --policy security.md --llm     # code + policy
      sponsio scan src/ -o custom.yaml                 # write to custom path
      sponsio scan src/ -o sponsio.yaml --append       # merge into existing
      sponsio scan src/ -o -                           # print to stdout (pipe)
      sponsio scan src/ --push                         # also push to dashboard
    """
    from sponsio.discovery.extractors.code_analysis import CodeAnalyzer

    # Route progress messages to stderr with light styling so the YAML
    # body on stdout is still pipeable to a file or another command.
    def _scan_progress(msg: str) -> None:
        if emit_context:
            return
        click.echo(click.style("· ", fg="cyan", dim=True) + msg, err=True)

    # ---- agent-driven path: dump inputs, skip LLM step ------------------
    # ``--emit-context`` runs the deterministic scan stages (AST tool
    # inventory, policy doc collection) and stops short of the LLM
    # contract-mining inside ``CodeAnalyzer.generate_yaml``.
    # The host agent picks up using ``sponsio prompt scan``.
    if emit_context:
        analyzer = CodeAnalyzer(use_llm=False)
        source_paths = list(paths)
        tool_inventory = analyzer.get_tool_inventory(source_paths) or []

        policy_docs: list[dict] = []
        for p in policy:
            try:
                policy_docs.append(
                    {
                        "path": str(p),
                        "content": Path(p).read_text(encoding="utf-8"),
                    }
                )
            except OSError:
                continue

        existing_yaml_text = ""
        out_path = Path(out) if out and out != "-" else Path("sponsio.yaml")
        if out_path.exists():
            try:
                existing_yaml_text = out_path.read_text(encoding="utf-8")
            except OSError:
                pass

        click.echo(
            json.dumps(
                {
                    "agent_id": agent,
                    "source_paths": source_paths,
                    "tool_inventory": tool_inventory,
                    "policy_docs": policy_docs,
                    "existing_yaml": existing_yaml_text,
                    "out_path": str(out_path),
                    "next_steps_hint": (
                        "Run ``sponsio prompt scan`` to get the prompt "
                        "template, apply it to this JSON in your own LLM "
                        f"context, then write the resulting YAML to {out_path} "
                        "via Edit/Write.  Validate with "
                        f"``sponsio validate --config {out_path}``."
                    ),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    # Pull provider/model/key/base_url from the YAML's ``extractor:``
    # section if --config was given.  CLI flags retain the highest
    # precedence. they're how you override on a one-off basis.
    api_key: str | None = None
    if config_path:
        from sponsio.config import load_config

        cfg = load_config(config_path)
        ext = cfg.extractor
        if not (ext.provider or ext.model or ext.api_key or ext.base_url):
            click.echo(
                click.style("  warn: ", fg="yellow")
                + f"{config_path} has no `extractor:` section. "
                "nothing to inherit.",
                err=True,
            )
        else:
            _scan_progress(
                f"using extractor config from {config_path} "
                f"(provider={ext.provider or '<auto>'}, "
                f"model={ext.model or '<default>'})"
            )
        provider = provider or ext.provider
        model = model or ext.model
        base_url = base_url or ext.base_url
        api_key = ext.api_key
        # --config implies --llm: configuring an extractor and then NOT
        # using it would be confusing.
        if not llm:
            llm = True
            _scan_progress("--config implies --llm; enabling LLM inference")

    analyzer = CodeAnalyzer(
        use_llm=llm,
        llm_model=model,
        api_key=api_key,
        provider=provider,
        base_url=base_url,
        progress=_scan_progress,
    )
    source_paths = list(paths)

    # Extract tool inventory for policy document context
    tool_inventory = analyzer.get_tool_inventory(source_paths) if policy else None

    yaml_content = analyzer.generate_yaml(
        source_paths,
        agent_id=agent,
        policy_paths=list(policy),
        tool_inventory=tool_inventory,
    )

    # --- Auto-validate & drop unparseable contracts ---------------------
    # Goal: the file we hand the user is *directly usable*. Any contract
    # that the parser can't compile is dropped here (and listed on
    # stderr) instead of being left as a landmine in the YAML.
    yaml_content, dropped_contracts = _filter_invalid_contracts(yaml_content)

    # --- Post-scan summary (stderr) -------------------------------------
    # Helps users notice the "0 contracts" case immediately and points
    # them at --llm if they only ran the AST pass.
    n_tools, n_contracts, n_review = _scan_summary_counts(yaml_content)
    summary_color = "green" if n_contracts > 0 else "yellow"
    summary = f"Scan summary: {n_tools} tool(s), {n_contracts} contract(s) kept"
    if n_review:
        summary += f" ({n_review} flagged for review)"
    if dropped_contracts:
        summary += f", {len(dropped_contracts)} dropped (failed to parse)"
    click.echo(click.style("• " + summary, fg=summary_color), err=True)
    for d in dropped_contracts:
        click.echo(
            click.style("  dropped: ", fg="yellow")
            + f"[{d['agent']}] "
            + click.style(d["nl"][:120], dim=True)
            + (f"  ({d['error']})" if d.get("error") else ""),
            err=True,
        )
    if n_tools == 0:
        click.echo(
            click.style("  note: ", fg="cyan")
            + "0 tools usually means nothing in the scanned path matched "
            "Sponsio's discovery rules (``@tool``, ``Agent(tools=[...])``, "
            "``TOOLS = [fn, ...]``, etc.), or the tree was effectively empty "
            "(dependency dirs like ``.venv`` / ``node_modules`` are skipped). "
            "Point at the directory that contains your agent's tool modules.",
            err=True,
        )
    if n_contracts == 0 and not llm:
        click.echo(
            click.style("  hint: ", fg="cyan")
            + "no contracts inferred from AST. Re-run with "
            + click.style("--llm", bold=True)
            + " (and optionally --policy <doc>) for richer inference.",
            err=True,
        )
    if policy and not llm:
        click.echo(
            click.style("  warn: ", fg="yellow")
            + "--policy was given but --llm was not. "
            + f"{len(policy)} policy file(s) were ignored.",
            err=True,
        )

    # Default output: write to ``./sponsio.yaml`` so the common
    # interactive case never leaves the user wondering where the YAML
    # went.  Two opt-outs:
    #   * ``-o -``      → print to stdout (pipeline use)
    #   * ``-o <path>`` → write to a specific path
    if out == "-":
        click.echo(yaml_content)
        click.echo(
            click.style("• ", fg="cyan")
            + "YAML written to stdout (use `-o <path>` to save to a file).",
            err=True,
        )
    else:
        target = out or "sponsio.yaml"
        existed = os.path.exists(target)
        if append and existed:
            with open(target) as f:
                existing = f.read()
            yaml_content = _merge_yaml(existing, yaml_content)
        with open(target, "w") as f:
            f.write(yaml_content)
        abs_out = os.path.abspath(target)
        verb = (
            "Updated" if append and existed else ("Overwrote" if existed else "Wrote")
        )
        click.echo(
            click.style("✓ ", fg="green") + f"{verb} {click.style(abs_out, bold=True)}",
            err=True,
        )
        if existed and not append:
            click.echo(
                click.style("  note: ", fg="yellow")
                + "existing file was overwritten. "
                + "Use --append to merge new contracts into it instead.",
                err=True,
            )
        click.echo(
            click.style("  tip: ", fg="cyan", dim=True)
            + f"re-run `sponsio validate --config {abs_out}` after manual edits.",
            err=True,
        )

    if push:
        _push_scan_to_dashboard(
            yaml_content=yaml_content,
            filename=(os.path.basename(out) if out and out != "-" else "sponsio.yaml"),
            dashboard_url=push_url,
            source_paths=source_paths,
        )


def _filter_invalid_contracts(yaml_content: str) -> tuple[str, list[dict]]:
    """Drop contracts that fail to compile so the saved YAML is usable as-is.

    Walks every ``agents.<id>.contracts[*]`` entry, runs the same parser
    that ``sponsio validate`` uses, and rewrites the YAML with only the
    entries that parse cleanly. Bad ones are returned for stderr display.

    Conservative on errors: if PyYAML / the parser modules aren't
    importable, returns the input unchanged (and an empty drop list) so
    a minimal install still gets a working scan, just without the
    auto-validate net.

    Returns:
        (cleaned_yaml, dropped) where ``dropped`` is a list of
        ``{"agent": str, "nl": str, "error": str}``.
    """
    try:
        import yaml as _yaml
    except ImportError:
        return yaml_content, []

    try:
        from sponsio.config import (
            _compile_ltl,
            _compile_structured,
            _parse_constraint_entry,
        )
        from sponsio.generation.dsl_to_contract import (
            ContractSyntaxError,
            parse_nl_unified,
        )
    except ImportError:
        return yaml_content, []

    try:
        data = _yaml.safe_load(yaml_content)
    except _yaml.YAMLError:
        return yaml_content, []

    if not isinstance(data, dict):
        return yaml_content, []

    agents_raw = data.get("agents", {})
    if not isinstance(agents_raw, dict):
        return yaml_content, []

    def _validate_one(item) -> tuple[bool, str, str]:
        try:
            entry = _parse_constraint_entry(item)
        except Exception as e:  # noqa: BLE001
            return False, str(item)[:120], f"parse: {e}"
        if entry.is_structured:
            try:
                _compile_structured(entry)
            except Exception as e:  # noqa: BLE001
                args = ", ".join(str(a) for a in (entry.args or []))
                return False, f"{entry.pattern}({args})", str(e)
            return True, "", ""
        elif entry.is_ltl:
            try:
                _compile_ltl(entry)
            except Exception as e:  # noqa: BLE001
                return False, (entry.ltl or "")[:120], str(e)
            return True, "", ""
        else:
            nl = entry.nl or ""
            try:
                parse_nl_unified(nl)
            except ContractSyntaxError as e:
                return False, nl, e.hint or "no pattern matched"
            except Exception as e:  # noqa: BLE001
                return False, nl, str(e)
            return True, "", ""

    bad_per_agent: dict[str, set[int]] = {}
    dropped: list[dict] = []

    for agent_id, ag in agents_raw.items():
        # An agent block is normally a dict with `contracts:`; bare lists
        # are tolerated by the loader but rare from generate_yaml. Handle
        # both for safety.
        if isinstance(ag, dict):
            contracts = ag.get("contracts", [])
        elif isinstance(ag, list):
            contracts = ag
        else:
            continue
        if not isinstance(contracts, list):
            continue

        bad: set[int] = set()
        for idx, ce in enumerate(contracts):
            # An entry can be either a bare string (E only, NL form), or
            # a dict with A/G keys whose values are themselves NL strings
            # or structured ``{pattern, args}`` dicts.
            sub_items: list = []
            if isinstance(ce, str):
                sub_items.append(ce)
            elif isinstance(ce, dict):
                for key in ("A", "G"):
                    if key not in ce:
                        continue
                    val = ce[key]
                    sub_items.extend(val if isinstance(val, list) else [val])
            else:
                continue

            entry_dropped = False
            for it in sub_items:
                if it is None:
                    continue
                ok, nl_repr, err = _validate_one(it)
                if not ok:
                    dropped.append(
                        {"agent": str(agent_id), "nl": nl_repr, "error": err}
                    )
                    entry_dropped = True
                    break
            if entry_dropped:
                bad.add(idx)

        if bad:
            bad_per_agent[str(agent_id)] = bad

    if not bad_per_agent:
        return yaml_content, dropped  # nothing to rewrite

    cleaned = _drop_contract_indices(yaml_content, bad_per_agent)
    return cleaned, dropped


def _drop_contract_indices(
    yaml_content: str, bad_per_agent: dict[str, set[int]]
) -> str:
    """Remove specific contract entries (by 0-based index) per agent.

    Preserves comments, confidence tags and the surrounding YAML
    structure that ``generate_yaml`` produces.  If an agent's
    ``contracts:`` list ends up empty we replace it with ``contracts: []``
    so the resulting file still parses.
    """
    out: list[str] = []
    lines = yaml_content.split("\n")

    in_agents = False
    current_agent: str | None = None
    in_contracts = False
    current_idx = -1
    skipping = False
    contracts_line_idx: int | None = None
    kept_in_current_contracts = 0

    def _finalize_contracts_block() -> None:
        # If the contracts: list ended up empty, swap the header line for
        # ``contracts: []`` so the YAML stays valid.
        nonlocal contracts_line_idx, kept_in_current_contracts
        if contracts_line_idx is not None and kept_in_current_contracts == 0:
            header = out[contracts_line_idx]
            stripped = header.lstrip()
            indent = header[: len(header) - len(stripped)]
            if stripped.rstrip().endswith(":"):
                out[contracts_line_idx] = f"{indent}contracts: []"
        contracts_line_idx = None
        kept_in_current_contracts = 0

    for line in lines:
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        # Blank / comment lines: keep unless we're inside a dropped entry.
        if not stripped or stripped.startswith("#"):
            if skipping:
                continue
            out.append(line)
            continue

        # Top-level key (col 0) → reset everything.
        if indent == 0:
            _finalize_contracts_block()
            in_agents = stripped.startswith("agents:")
            current_agent = None
            in_contracts = False
            current_idx = -1
            skipping = False
            out.append(line)
            continue

        # Inside agents: each agent header sits at indent 2.
        if in_agents and indent == 2 and stripped.rstrip().endswith(":"):
            _finalize_contracts_block()
            current_agent = stripped.rstrip()[:-1].strip()
            in_contracts = False
            current_idx = -1
            skipping = False
            out.append(line)
            continue

        # Properties of the current agent live at indent 4.
        if current_agent is not None and indent == 4:
            _finalize_contracts_block()
            in_contracts = stripped.startswith("contracts:")
            skipping = False
            current_idx = -1
            if in_contracts:
                contracts_line_idx = len(out)
                kept_in_current_contracts = 0
            out.append(line)
            continue

        # Inside a contracts: list, entries start at indent 6 with "- ".
        if in_contracts and indent >= 6:
            if stripped.startswith("- "):
                current_idx += 1
                bad_set = bad_per_agent.get(current_agent or "", set())
                skipping = current_idx in bad_set
                if not skipping:
                    kept_in_current_contracts += 1
                    out.append(line)
                continue
            # Continuation line of the current entry.
            if not skipping:
                out.append(line)
            continue

        # Anything else: outside our tracked regions.
        skipping = False
        out.append(line)

    _finalize_contracts_block()
    return "\n".join(out)


def _scan_summary_counts(yaml_content: str) -> tuple[int, int, int]:
    """Count tools, contracts and review-flagged contracts in scan YAML.

    Tolerant to formatting; we just look for stable line shapes that the
    YAML emitter produces.  Returns ``(tools, contracts, review_flagged)``.
    """
    n_tools = 0
    n_contracts = 0
    n_review = 0
    in_tools = False
    for raw in yaml_content.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if line.startswith("tools:"):
            in_tools = True
            continue
        if in_tools:
            if line.startswith("  - name:"):
                n_tools += 1
                continue
            if line and not line.startswith(" "):
                in_tools = False
        if stripped.startswith("- G:") or stripped.startswith("- A:"):
            n_contracts += 1
            if "review recommended" in stripped:
                n_review += 1
    return n_tools, n_contracts, n_review


def _push_scan_to_dashboard(
    yaml_content: str,
    filename: str,
    dashboard_url: str,
    source_paths: list[str],
) -> None:
    """POST the scan YAML to the running dashboard.

    Silently skips if the dashboard isn't reachable; this is additive UX,
    not a required step.
    """
    base = dashboard_url.rstrip("/")
    try:
        import httpx
    except ImportError:
        click.echo(
            click.style("  note: ", fg="yellow")
            + "httpx not installed, skipping dashboard push."
        )
        return

    # 1. Check that the dashboard is actually running before uploading.
    try:
        r = httpx.get(f"{base}/api/health", timeout=1.5)
        if r.status_code != 200:
            raise RuntimeError(f"/api/health returned {r.status_code}")
    except Exception:
        click.echo(
            click.style("  tip: ", fg="cyan")
            + f"dashboard not running at {base}. start it with "
            + click.style("sponsio serve", bold=True)
            + " to see scan results in the UI."
        )
        return

    # 2. POST the YAML as a file upload, tagged with source=cli so the
    #    dashboard's CLI tab can distinguish it from browser uploads.
    try:
        files = {"file": (filename, yaml_content.encode("utf-8"), "text/yaml")}
        r = httpx.post(
            f"{base}/api/scan/upload",
            files=files,
            params={"source": "cli"},
            timeout=10.0,
        )
        if r.status_code != 200:
            click.echo(
                click.style("  push failed: ", fg="yellow")
                + f"HTTP {r.status_code} {r.text[:200]}"
            )
            return
        result = r.json()
        summary = (
            f"{result.get('agent_name', '?')}: "
            f"{result.get('score', 0)}/100 "
            f"({result.get('grade', '?')})"
        )
        click.echo(click.style("✓ ", fg="green") + f"Pushed to dashboard. {summary}")
        click.echo(
            f"  View at {click.style(base.replace(':8000', ':3000') + '/scan', bold=True)}"
        )
    except Exception as e:
        click.echo(click.style("  push failed: ", fg="yellow") + str(e))


def _merge_yaml(existing: str, new: str) -> str:
    """Merge new scan results into an existing YAML file.

    Appends new contract entries (``- E:`` / ``- A: ... E:``) from
    *new* after the last contract in *existing*, avoiding duplicates.

    Works with the current ``contracts: [{A, E}]`` YAML schema.
    """
    existing_lines = existing.rstrip().split("\n")

    # --- Extract contract entries from new content ---
    # A contract entry starts with a line matching `- E:` or `- A:` at
    # the expected indent (6 spaces inside `contracts:`). Continuation
    # lines are indented deeper.
    new_lines = new.split("\n")
    new_entries: list[list[str]] = []
    in_contracts = False
    current_entry: list[str] = []

    for line in new_lines:
        stripped = line.strip()
        if "contracts:" in line and stripped != "contracts: []":
            in_contracts = True
            continue
        if not in_contracts:
            continue
        # A new entry starts with `- E:` or `- A:` (possibly with trailing comment)
        if stripped.startswith("- G:") or stripped.startswith("- A:"):
            if current_entry:
                new_entries.append(current_entry)
            current_entry = [line]
        elif current_entry and (
            stripped.startswith("pattern:")
            or stripped.startswith("args:")
            or stripped.startswith("source:")
            or stripped.startswith("G:")
            or stripped.startswith("desc:")
        ):
            # Continuation of the current entry
            current_entry.append(line)
        elif current_entry and not stripped and not stripped.startswith("#"):
            # Blank line or end of section
            pass
        elif current_entry and stripped.startswith("#"):
            # Comment inside an entry. keep it
            current_entry.append(line)
        elif not stripped:
            continue
        else:
            # Non-entry, non-continuation line. we've left the contracts block
            break
    if current_entry:
        new_entries.append(current_entry)

    if not new_entries:
        return existing

    # --- Fingerprint existing entries to deduplicate ---
    # Normalize each entry to a single key string for comparison.
    def _fingerprint(lines: list[str]) -> str:
        return " ".join(ln.strip() for ln in lines)

    existing_fingerprints: set[str] = set()
    temp_entry: list[str] = []
    in_existing_contracts = False
    for line in existing_lines:
        stripped = line.strip()
        if "contracts:" in line:
            in_existing_contracts = True
            continue
        if not in_existing_contracts:
            continue
        if stripped.startswith("- G:") or stripped.startswith("- A:"):
            if temp_entry:
                existing_fingerprints.add(_fingerprint(temp_entry))
            temp_entry = [line]
        elif temp_entry and stripped and not stripped.startswith("#"):
            temp_entry.append(line)
        elif not stripped:
            continue
    if temp_entry:
        existing_fingerprints.add(_fingerprint(temp_entry))

    # Filter out duplicates
    to_add = [
        entry
        for entry in new_entries
        if _fingerprint(entry) not in existing_fingerprints
    ]

    if not to_add:
        return existing

    # --- Append after last content line ---
    result = existing.rstrip() + "\n"
    result += "      # --- appended by sponsio scan ---\n"
    for entry in to_add:
        result += "\n".join(entry) + "\n"
    return result

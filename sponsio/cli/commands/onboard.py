"""``sponsio onboard`` — one-shot project wire-up."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import click

from sponsio.cli._shared import (
    _parse_existing_contracts,
)
from sponsio.cli.app import cli

# onboard composes scan (dashboard push) + mode (enforce flip) + the
# host runtime-mode resolver.
from sponsio.cli.commands.mode import _patch_mode_in_yaml
from sponsio.cli.commands.scan import _push_scan_to_dashboard
from sponsio.cli.groups.host import _resolve_runtime_mode


@cli.command()
@click.argument(
    "target",
    type=click.Path(file_okay=True, dir_okay=True, path_type=Path),
    default=".",
    required=False,
)
@click.option(
    "--agent",
    "agent_id",
    default="agent",
    show_default=True,
    help=(
        "Agent identifier stamped into sponsio.yaml.  Matches "
        "`sponsio scan`'s default so a later `scan --append` lands "
        "in the same agent block."
    ),
)
@click.option(
    "--mode",
    type=click.Choice(["observe", "enforce"]),
    default=None,
    help=(
        "Runtime mode written into sponsio.yaml.  Skip the flag to be "
        "prompted interactively (same Y/N question ``sponsio init`` "
        "and ``sponsio host install`` ask).  ``observe`` is the safe "
        "default. never blocks, logs every would-have-blocked decision "
        "to ~/.sponsio/sessions/<agent_id>/*.jsonl."
    ),
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing sponsio.yaml without prompting.",
)
@click.option(
    "--no-probe-ollama",
    is_flag=True,
    help=(
        "Skip the localhost:11434 liveness probe.  Useful in CI or "
        "behind strict firewalls where the <500ms probe still times "
        "out slowly and you'd rather jump straight to the starter pack."
    ),
)
@click.option(
    "--no-doctor",
    is_flag=True,
    help=(
        "Skip the post-onboard `sponsio doctor` run.  By default we "
        "run the full offline check battery so users see whether the "
        "install is healthy before they switch to enforce mode."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the structured OnboardReport as JSON instead of text.",
)
@click.option(
    "--emit-context",
    "emit_context",
    is_flag=True,
    default=False,
    help=(
        "Skip the LLM step and instead emit the structured inputs "
        "(framework / tool inventory / auto-selected packs / existing "
        "yaml / discovered policy docs) as JSON to stdout. Used by the "
        "host agent driving the ``sponsio`` skill: pair with "
        "``sponsio prompt onboard`` and apply in the agent's own LLM "
        "context. no UnifiedExtractor call, no extra API key."
    ),
)
@click.option(
    "--push/--no-push",
    default=False,
    help=(
        "After writing sponsio.yaml, push it to the local dashboard at "
        "--push-url so it lands on the Scan page + Contract Library "
        "(default: off; on is one round-trip per run, silently skipped "
        "when the dashboard isn't up)."
    ),
)
@click.option(
    "--push-url",
    default="http://127.0.0.1:8000",
    help="Dashboard URL to push to (default: http://127.0.0.1:8000).",
)
@click.option(
    "--interactive/--no-interactive",
    "interactive",
    default=None,
    help=(
        "Prompt for framework / LLM provider / model up front and "
        "write `.sponsiorc` + `.env.example` next to sponsio.yaml. "
        "Default: auto. interactive when stdin is a TTY, "
        "non-interactive otherwise (CI, scripts, docker entrypoints, "
        "``--json``, ``--emit-context``).  Pass ``--no-interactive`` "
        "to force the silent path even from a terminal."
    ),
)
def onboard(
    target: Path,
    agent_id: str,
    mode: str | None,
    force: bool,
    no_probe_ollama: bool,
    no_doctor: bool,
    as_json: bool,
    emit_context: bool,
    push: bool,
    push_url: str,
    interactive: bool | None,
):
    """One-shot project wire-up. detect framework, write sponsio.yaml, print patch.

    Composes `init` + `scan` + `doctor` into a single command so
    first-time users don't have to learn three subcommands just to
    run the guard in observe mode.  Specifically:

    \b
      1. Detects the agent framework from imports + dependencies.
      2. Detects the best available LLM provider (env vars →
         OPENAI_BASE_URL → local Ollama → none).
      3. Writes sponsio.yaml in observe mode with an inferred contract
         set. LLM-inferred when a provider was found, or pure name-
         heuristic starter pack when it wasn't.
      4. Prints the framework-specific 2-line patch the user needs to
         apply to their agent entry point.

    Safe defaults throughout: mode=observe (never blocks on day 1),
    agent_id="agent" (matches `sponsio scan`), and --force off (the
    "I already have sponsio.yaml" case is louder than a silent overwrite).

    Examples:\n
        sponsio onboard\n
        sponsio onboard src/\n
        sponsio onboard . --agent customer_bot\n
        sponsio onboard --force --no-probe-ollama
    """
    from sponsio.onboard import OnboardReport, run_onboard
    from sponsio.runtime.spinner import Spinner

    # Branded header. same ``header_banner`` Rich primitive that
    # ``sponsio init`` / ``sponsio doctor`` / runtime print_banner /
    # explain renderers use, so onboarding's first line of output
    # looks like the rest of the CLI instead of a hand-glued
    # ``━`` string.  Skipped on the non-interactive structured-
    # output paths (--json, --emit-context) so consumers parsing
    # stdout don't have to sed past it.
    if not as_json and not emit_context and not os.environ.get("SPONSIO_INIT_DISPATCH"):
        # Standalone ``sponsio onboard`` prints a full banner so users
        # see the product wordmark when running it directly.  When the
        # ``sponsio init`` wizard dispatched us, the preview block
        # above already showed ``→ sponsio onboard . --mode ...`` so
        # any additional banner / divider here is redundant.  Skip
        # entirely and let the first stage section rule
        # (``Scanning your code ─────...``) carry the transition.
        from sponsio.render.components import header_banner as _header_banner
        from sponsio.runtime.terminal import (
            _make_stderr_console as _make_console,
        )

        _hdr_console = _make_console(None)
        _hdr_console.print()
        _hdr_console.print(_header_banner(tagline="onboard"))

    # One spinner per command. long-wait emits (``…``-suffixed) start
    # it, the next emit (or the final ``stop()`` after run_onboard)
    # cleans up.  Skipped silently when stderr isn't a TTY, so CI / pipe
    # / docker output stays line-oriented.
    _spinner = Spinner()

    def _progress(msg: str) -> None:
        # ``▸`` prefix = stage section header.  Render as a
        # :func:`section_rule` (label + ``─────...`` rule) so the
        # divider matches the runtime trace renderer + ``sponsio init``
        # axis headers.  Anything else is a per-step progress line.
        # dim cyan ``· `` bullet.  Emits ending with ``…`` are "this
        # will take a while" announcements; we hand them to the
        # spinner so the user sees motion during the wait.
        if as_json or emit_context:
            return
        # Always stop any running spinner first so the next line lands
        # cleanly (rather than on top of a stale frame).
        _spinner.stop()
        if msg.startswith("▸ "):
            from sponsio.render.components import (
                indent as _indent_for_progress,
                section_rule as _section_rule_for_progress,
            )
            from sponsio.runtime.terminal import (
                _make_stderr_console as _make_console_for_progress,
            )

            _progress_console = _make_console_for_progress(None)
            _progress_console.print()
            _progress_console.print(
                _indent_for_progress(_section_rule_for_progress(msg.removeprefix("▸ ")))
            )
            return
        # Bullets indent col-2 to match the section rule above them
        # and the trace + report renderers' body indentation.
        line = "  " + click.style("· ", fg="cyan", dim=True) + msg
        if msg.endswith("…"):
            _spinner.start(line)
        else:
            click.echo(line, err=True)

    # ---- agent-driven path: dump inputs, skip LLM step ------------------
    # ``--emit-context`` runs the deterministic stages (framework /
    # provider / AST tool inventory / pack selection) and stops short of
    # the LLM contract-mining inside CodeAnalyzer.generate_yaml.  The
    # host agent picks up where we leave off using ``sponsio prompt
    # onboard``.
    if emit_context:
        target_path = Path(target)
        if target_path.suffix in {".yaml", ".yml"}:
            root = target_path.parent or Path(".")
            existing_yaml_path = target_path
        else:
            root = target_path
            existing_yaml_path = target_path / "sponsio.yaml"

        from sponsio.discovery.extractors.code_analysis import CodeAnalyzer
        from sponsio.onboard import detect_framework, select_packs

        # AST-only. explicit ``use_llm=False`` so this path never
        # reads any provider env var.
        analyzer = CodeAnalyzer(use_llm=False)
        tool_inventory = analyzer.get_tool_inventory([str(root)]) or []
        # Run framework detection AFTER tool inventory, prioritizing
        # the files the extractor already pinned as agent code. fixes
        # the monorepo case where 200+ pad files at the root could
        # exhaust the framework scan cap before the agent file (in a
        # deep subdir) was reached, leaving framework="none" even
        # though tool_inventory had found ``@tool`` functions there.
        prioritize_files: list[Path] = []
        for t in tool_inventory:
            fp = t.get("filepath") if isinstance(t, dict) else None
            if not fp:
                continue
            p = Path(fp)
            if not p.is_absolute():
                p = root / p
            if p.is_file():
                prioritize_files.append(p)
        framework = detect_framework(root, prioritize_files=prioritize_files)
        pack_selection = select_packs(framework.framework, tool_inventory)

        existing_yaml_text = ""
        if existing_yaml_path.exists():
            try:
                existing_yaml_text = existing_yaml_path.read_text(encoding="utf-8")
            except OSError:
                pass

        # Surface common policy docs the agent should weight in the
        # extraction.  Conservative search. root-level only, by
        # convention. to avoid pulling in unrelated repo prose.  Dedup
        # by inode so case-insensitive filesystems (macOS HFS+) don't
        # report ``security.md`` and ``SECURITY.md`` twice.
        policy_docs = []
        seen_inodes: set[tuple[int, int]] = set()
        for candidate in ("security.md", "SECURITY.md", "policy.md", "POLICY.md"):
            p = root / candidate
            if not p.is_file():
                continue
            try:
                stat = p.stat()
                key = (stat.st_dev, stat.st_ino)
                if key in seen_inodes:
                    continue
                seen_inodes.add(key)
                policy_docs.append(
                    {
                        "path": str(p.relative_to(root)),
                        "content": p.read_text(encoding="utf-8"),
                    }
                )
            except OSError:
                pass

        # Pull the framework-specific wrap snippet (the 2-3 line patch
        # the user pastes into their agent entry file).  The skill's
        # W1 step 5 references this field; emitting it here lets the
        # agent surface the wiring instructions in the same turn it
        # writes the YAML.
        wrap_snippet_text = ""
        try:
            from sponsio.onboard import _wrap_snippet  # type: ignore[attr-defined]

            wrap_snippet_text = _wrap_snippet(framework.framework, agent_id) or ""
        except Exception:  # pragma: no cover. best-effort
            pass

        # Locate likely agent entry files so the IDE agent doesn't have
        # to re-discover them. Conservative regex grep over root-level
        # .py files, ranked by signal density.
        entry_file_candidates: list[dict] = []
        try:
            framework_signals: dict[str, list[re.Pattern]] = {
                "langchain": [
                    re.compile(r"from\s+langchain"),
                    re.compile(r"create_react_agent\s*\("),
                ],
                "langgraph": [
                    re.compile(r"from\s+langgraph"),
                    re.compile(r"StateGraph\s*\("),
                    re.compile(r"create_react_agent\s*\("),
                ],
                "crewai": [re.compile(r"from\s+crewai"), re.compile(r"\bAgent\s*\(")],
                "autogen": [
                    re.compile(r"from\s+autogen"),
                    re.compile(r"AssistantAgent\s*\("),
                ],
                "openai_agents": [
                    re.compile(r"from\s+agents"),
                    re.compile(r"\bAgent\s*\("),
                ],
                "openai": [re.compile(r"from\s+openai"), re.compile(r"OpenAI\s*\(")],
                "anthropic": [
                    re.compile(r"from\s+anthropic"),
                    re.compile(r"Anthropic\s*\("),
                    re.compile(r"messages\.create\s*\("),
                ],
                "claude_agent_sdk": [re.compile(r"from\s+claude_agent_sdk")],
                "google_adk": [re.compile(r"from\s+google\.adk")],
            }
            sigs = framework_signals.get(framework.framework, [])
            if sigs:
                from glob import glob as _glob

                py_files = sorted(
                    set(
                        _glob(str(root / "*.py"))
                        + _glob(str(root / "**/*.py"), recursive=True)
                    )
                )
                py_files = [
                    f
                    for f in py_files
                    if "/.venv/" not in f
                    and "/__pycache__/" not in f
                    and "/site-packages/" not in f
                ]
                scored = []
                for f in py_files[:200]:  # cap to avoid scanning large monorepos
                    try:
                        text = Path(f).read_text(encoding="utf-8")
                    except OSError:
                        continue
                    matches = [s.pattern for s in sigs if s.search(text)]
                    if matches:
                        scored.append(
                            {
                                "path": str(Path(f).relative_to(root)),
                                "reason": "matches: " + ", ".join(matches),
                            }
                        )
                scored.sort(key=lambda x: -len(x["reason"]))
                entry_file_candidates = scored[:5]
        except Exception:  # pragma: no cover. best-effort
            entry_file_candidates = []

        # Parse the on-disk sponsio.yaml's contracts (if any) so the
        # host agent driving this can dedupe its semantic-pass
        # proposals without having to re-grep YAML.  Conservative
        # parse. failures degrade to an empty list rather than
        # blocking the emit (a malformed yaml is worth surfacing,
        # but not at the cost of also blocking the rest of the
        # diagnostic JSON).
        pre_existing_contracts: list[dict] = []
        if existing_yaml_path.exists():
            pre_existing_contracts = _parse_existing_contracts(
                existing_yaml_path, agent_id
            )

        # Health flag. the host agent uses this as the single
        # gate for "should I keep going or stop and ask?".
        # Reflects three orthogonal failure modes the previous
        # case-A/B/C check in the wizard prompt was hand-rolling.
        if framework.framework != "none" and tool_inventory:
            health = "ok"
            health_detail = "framework + tools detected"
        elif tool_inventory:
            # Rare after the prioritize-files fix, but still possible
            # for unusual import shapes (star imports, dynamic
            # `__import__`, etc.). surface explicitly so the agent
            # asks the user to pick from axis 1 manually.
            health = "tools_only"
            health_detail = (
                "tool_inventory found tools but no framework import "
                "matched any known signature. pick framework manually"
            )
        elif framework.framework != "none":
            health = "tools_only"
            health_detail = (
                f"framework {framework.framework!r} detected but "
                "tool_inventory is empty. agent likely uses external "
                "SDK tools (MCP, prebuilt LangChain tools, OpenAI "
                "JSON schemas); grep the repo for tool registration"
            )
        else:
            health = "empty"
            health_detail = (
                "no framework, no tools. wrong scan path "
                "(monorepo + agent in subdir), or this is a bare "
                "function-calling loop, or the project is TS and "
                "you ran the Python probe"
            )

        click.echo(
            json.dumps(
                {
                    "health": health,
                    "health_detail": health_detail,
                    "framework": {
                        "name": framework.framework,
                        "evidence": framework.evidence,
                    },
                    "agent_id": agent_id,
                    "tool_inventory": tool_inventory,
                    "auto_selected_packs": list(pack_selection.packs),
                    "needs_workspace": pack_selection.needs_workspace,
                    "existing_yaml": existing_yaml_text,
                    "pre_existing_contracts": pre_existing_contracts,
                    "policy_docs": policy_docs,
                    "wrap_snippet": wrap_snippet_text,
                    "entry_file_candidates": entry_file_candidates,
                    "out_path": str(existing_yaml_path),
                    "next_steps_hint": (
                        "Run ``sponsio prompt onboard`` to get the prompt "
                        "template, apply it to this JSON in your own LLM "
                        "context, then write the resulting YAML to "
                        f"{existing_yaml_path} via Edit/Write, and patch "
                        "the agent entry file (see entry_file_candidates) "
                        "with the wrap_snippet."
                    ),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    # ---- interactive setup (prompts + dotfile writes) ------------------
    # Decide whether to run prompts.  --json and --emit-context force
    # non-interactive (prompts would corrupt the structured output).
    # Otherwise an explicit --interactive / --no-interactive flag wins;
    # without one, follow the TTY: real shell → prompts, CI / pipe /
    # docker entrypoint → silent.
    from sponsio.onboard import _wrap_snippet  # type: ignore[attr-defined]
    from sponsio.onboard import detect_framework as _detect_fw_for_prompts
    from sponsio.onboard import detect_provider as _detect_prov_for_prompts
    from sponsio.onboard_setup import (
        SetupAnswers,
        maybe_no_api_key_warning,
        run_setup_prompts,
        stdin_is_tty,
        write_sponsiorc,
    )
    from sponsio.sponsiorc import load_sponsiorc

    if as_json or emit_context:
        is_interactive = False
    elif interactive is not None:
        is_interactive = interactive
    else:
        is_interactive = stdin_is_tty()

    # Resolve runtime mode through the same shared helper that
    # ``sponsio host install`` uses, so all install paths ask the
    # observe-vs-enforce question the same way. ``--mode`` skips the
    # prompt; ``--json`` / ``--emit-context`` / ``--no-interactive``
    # also skip it (structured-output paths must not pollute stdout
    # with a click prompt). Fallback when no signal: ``observe``.
    mode_was_explicit = mode is not None
    mode = _resolve_runtime_mode(mode, allow_prompt=is_interactive)

    target_dir = target if target.is_dir() else target.parent

    # Resolve where sponsio.yaml will live so we can detect a "second
    # run" case below without duplicating run_onboard's path logic.
    if target.suffix in {".yaml", ".yml"}:
        out_path_check = target
    else:
        out_path_check = target_dir / "sponsio.yaml"
    yaml_already_exists = out_path_check.exists() and not force

    # Second-run UX: if the user already ran onboard here (.sponsiorc is
    # present), skip the prompts and reuse the saved choices.  Re-asking
    # every time was annoying and the user explicitly flagged it.
    rc = load_sponsiorc(target_dir) if target_dir.exists() else None
    rc_in_target = (
        rc is not None
        and rc.found
        and rc.source_path is not None
        and rc.source_path.parent.resolve() == target_dir.resolve()
    )

    if rc_in_target:
        # Reuse the rcfile values verbatim. that's the whole point of
        # the dotfile.  Prompts only fire when there's nothing to reuse.
        # We still run framework detection so the wrap snippet on the
        # yaml-preserve path reflects current code (not a stale
        # rcfile).  Detection beating rcfile here is intentional: the
        # only way ``framework`` ends up wrong in an rcfile is when an
        # older detection couldn't recognise the user's code; if today's
        # detector finds something concrete, that's the better answer.
        pre_fw = _detect_fw_for_prompts(target_dir) if target_dir.exists() else None
        detected_fw = (
            pre_fw.framework if pre_fw and pre_fw.framework != "none" else None
        )
        answers = SetupAnswers(
            framework=detected_fw or rc.framework or "none",
            provider=rc.extractor_provider or "none",
            model=rc.extractor_model or "",
            api_key_env=rc.extractor_api_key_env or "",
        )
        pre_prov = None
    else:
        # Pre-detect framework + provider so the prompts have sensible
        # defaults.  Cheap (no LLM); run even in non-interactive mode
        # so the rcfile we write below reflects what onboard actually
        # used.
        pre_fw = _detect_fw_for_prompts(target_dir) if target_dir.exists() else None
        pre_prov = _detect_prov_for_prompts(probe_ollama=not no_probe_ollama)
        answers = run_setup_prompts(
            detected_framework=pre_fw.framework if pre_fw else "none",
            detected_provider=pre_prov.provider,
            detected_model=pre_prov.model or "",
            detected_api_key_env=pre_prov.env_var or "",
            interactive=is_interactive,
        )

    # Second-run UX: existing sponsio.yaml + no --force → preserve it.
    # We still refresh the dotfiles + reprint the wrap snippet so the
    # command stays useful (re-running onboard to remind yourself how
    # to wire it up shouldn't error).  --force keeps the regenerate
    # path for users who actually want a fresh yaml.
    report: OnboardReport | None = None
    if yaml_already_exists:
        if not as_json and not emit_context:
            click.echo()
            click.secho(f"✓ {out_path_check}", fg="green")
            click.echo("  preserved (re-run with --force to regenerate)")
    else:
        try:
            report = run_onboard(
                target,
                agent_id=agent_id,
                mode=mode,
                force=force,
                probe_ollama=not no_probe_ollama,
                run_doctor=not no_doctor,
                progress=_progress,
            )
        except FileExistsError as e:
            _spinner.stop()
            click.echo(click.style("Error: ", fg="red") + str(e), err=True)
            sys.exit(1)
        finally:
            # Belt + braces: if the last emit was a ``…`` line (rare.
            # run_onboard normally pairs each "Running …" with a "done"
            # emit), make sure we don't leave the spinner thread spinning
            # forever and the cursor stuck on a stale frame.
            _spinner.stop()

    # Write the rcfile (idempotent, plain write_text).  Skipped when
    # target was a single file rather than a directory. the rcfile
    # location is ambiguous in that case.  We deliberately do NOT
    # write a ``.env.example`` here: sponsio reads ``os.environ``
    # directly (no python-dotenv in the runtime), so a ``.env``-based
    # recipe would silently fail.  Users keep secrets in their shell
    # rc / direnv / system keychain. the rcfile records only the
    # variable name (``api_key_env``), not the value.
    sponsiorc_path: Path | None = None
    if target_dir.exists() and target_dir.is_dir():
        sponsiorc_path = write_sponsiorc(answers, target_dir)

    if as_json:
        payload = (
            report.to_dict()
            if report is not None
            else {
                "out_path": str(out_path_check),
                "preserved": True,
            }
        )
        payload["setup"] = {
            "interactive": is_interactive,
            "framework": answers.framework,
            "provider": answers.provider,
            "model": answers.model,
            "api_key_env": answers.api_key_env,
            "api_key_set_in_env": answers.api_key_set_in_env,
            "sponsiorc_path": str(sponsiorc_path) if sponsiorc_path else None,
        }
        click.echo(json.dumps(payload, indent=2))
        return

    # Human-readable summary.  Kept compact so the wrap snippet is the
    # last thing the user sees. it's what they need to act on.  When
    # report is None we're on the second-run preserve path; the "✓
    # sponsio.yaml preserved" line was already printed above.
    if report is not None:
        click.echo()
        click.secho(f"  ✓ {report.out_path}", fg="green")
        click.echo(f"      tools:      {report.tools_count}")
        click.echo(f"      contracts:  {report.contracts_count}")
        click.echo(f"      mode:       {report.mode}")
        click.echo(f"      framework:  {report.framework.framework}")
        click.echo(f"      provider:   {report.provider.provider}")
        if report.starter_pack_used:
            click.secho(
                "      · starter-pack applied (no-LLM safety net)",
                fg="yellow",
                dim=True,
            )

    # Dotfiles written alongside sponsio.yaml.  Surface the paths so
    # the user knows which file holds their tool config (vs. the
    # contract library) and where to drop their actual API key.
    if sponsiorc_path is not None:
        click.echo()
        click.secho(f"  ✓ {sponsiorc_path}", fg="green")
        click.echo(
            "      framework + LLM config. edit this file to change "
            "framework / model / api_key_env"
        )
        # Best-effort .gitignore hint: only fire when sponsiorc is in
        # a git repo AND `.sponsiorc` isn't already covered by the
        # existing rules.  Avoids nagging users who already gitignore'd
        # it (or who deliberately track it for team-wide config).
        try:
            rc_dir = sponsiorc_path.parent
            git_root = rc_dir
            for _ in range(8):  # walk up to 8 levels. plenty for a repo
                if (git_root / ".git").exists():
                    break
                if git_root.parent == git_root:
                    git_root = None  # type: ignore[assignment]
                    break
                git_root = git_root.parent
            else:
                git_root = None
            if git_root is not None:
                gitignore = git_root / ".gitignore"
                already_ignored = False
                if gitignore.is_file():
                    ignore_text = gitignore.read_text(encoding="utf-8")
                    for line in ignore_text.splitlines():
                        s = line.strip()
                        if s and not s.startswith("#"):
                            if s in {".sponsiorc", "**/.sponsiorc", "*.sponsiorc"}:
                                already_ignored = True
                                break
                if not already_ignored:
                    click.secho(
                        "  tip: add `.sponsiorc` to .gitignore "
                        "(holds local model / api_key_env hints)",
                        fg="cyan",
                        dim=True,
                    )
        except OSError:
            pass
    # No-key warning. fires when the user picked a provider that
    # needs a key but the env var isn't actually set, or when
    # provider==none (so onboard fell back to the name-heuristic
    # starter pack instead of LLM-inferred contracts).
    no_key_msg = maybe_no_api_key_warning(answers)
    if no_key_msg is not None:
        click.echo()
        for ln in no_key_msg.splitlines():
            click.secho("  " + ln, fg="yellow")

    if report is not None and report.doctor_results is not None:
        total = len(report.doctor_results)
        fails = sum(1 for r in report.doctor_results if r.status == "fail")
        warns = sum(1 for r in report.doctor_results if r.status == "warn")
        if fails == 0 and warns == 0:
            click.secho(f"  ✓ doctor:   {total}/{total} checks passed", fg="green")
        else:
            click.echo(
                f"  doctor:     {total - fails - warns}/{total} ok"
                + (click.style(f", {warns} warn", fg="yellow") if warns else "")
                + (click.style(f", {fails} fail", fg="red") if fails else "")
            )
            for r in report.doctor_results:
                if r.status in {"fail", "warn"}:
                    color = "red" if r.status == "fail" else "yellow"
                    click.echo(
                        f"    {click.style(r.icon, fg=color)} {r.name}: {r.detail}"
                    )

    if report is not None:
        for w in report.warnings:
            click.echo()
            click.echo(click.style("  warn: ", fg="yellow") + w)

    # Print the framework-specific patch snippet.  Auto-applying it
    # to the user's agent file used to live behind ``--apply`` but
    # was removed (only langgraph / langchain were supported, and a
    # coding agent / manual paste does the same job for any
    # framework with fewer surprises).  On the second-run preserve
    # path the framework comes from the rcfile-derived answers (the
    # user's saved choice, not a fresh detection).
    snippet = (
        report.wrap_snippet
        if report is not None
        else _wrap_snippet(answers.framework or "none", agent_id)
    )
    click.echo()
    click.secho("Add this to your agent entry point:", bold=True)
    click.echo()
    for ln in snippet.splitlines():
        click.echo(f"  {click.style(ln, fg='cyan')}")

    # Surface the contract file the user should now review. ``onboard``
    # wrote LLM-inferred (or starter-pack) contracts based on detected
    # tools. they're a sane first cut, not a finished policy. Pointing
    # the user at the path with a clear "review before flipping to
    # enforce" callout turns "did onboard actually do what I wanted?"
    # into one ``cat`` command.
    review_path = (
        report.out_path
        if report is not None
        else (out_path_check if yaml_already_exists else None)
    )
    if review_path is not None and not as_json and not emit_context:
        click.echo()
        click.secho("Review the generated contracts:", bold=True)
        click.echo(f"  {click.style(str(review_path), fg='green')}")
        click.secho(
            "  (open it, sanity-check each rule, then re-run with `--mode enforce`",
            dim=True,
        )
        click.secho("   when you're ready to switch from observe to active)", dim=True)

    # --push: surface the generated yaml in the local dashboard (one
    # command == everything the dashboard needs). Silently skipped if
    # the dashboard isn't running, so a CI invocation without `serve`
    # up doesn't fail.
    if push and report is not None:
        try:
            yaml_content = report.out_path.read_text()
        except Exception as e:
            click.echo(
                click.style("\n  push skipped: ", fg="yellow")
                + f"could not read {report.out_path} ({e})"
            )
        else:
            click.echo()
            _push_scan_to_dashboard(
                yaml_content=yaml_content,
                filename=report.out_path.name,
                dashboard_url=push_url,
                source_paths=[str(target)],
            )

    # Optional immediate flip-to-enforce prompt.  Onboard always
    # writes ``mode: observe`` by default. that's the safe path for
    # teams who want a soak period.  But some users (CI hardening
    # workflows, demo recordings, "I already ran the agent and know
    # the contracts are right") want enforce on day 1.  Asking here
    # turns "remember to sed the yaml later" into one keystroke.
    #
    # Skipped when:
    #   - non-interactive (no TTY / --no-interactive / --json /
    #     --emit-context. prompts would corrupt structured output)
    #   - the user already chose ``--mode enforce`` (no point asking
    #     a question they answered on the command line)
    #   - run_onboard didn't actually produce a report (early-exit
    #     paths above)
    if (
        report is not None
        and is_interactive
        and not as_json
        and not emit_context
        and not mode_was_explicit  # honor caller's `--mode` choice
        and not os.environ.get("SPONSIO_INIT_DISPATCH")
        and report.mode == "observe"
    ):
        click.echo()
        flip = click.confirm(
            click.style(
                "Mode is `observe` (shadow). Flip to `enforce` now?",
                bold=True,
            ),
            default=False,
            show_default=True,
        )
        if flip:
            try:
                yaml_text = report.out_path.read_text(encoding="utf-8")
                # Defer to the shared helper so both the interactive
                # onboard flow and the explicit ``sponsio mode`` CLI
                # agree on which mode-line to patch (runtime preferred,
                # defaults fallback, append-observe-only as last resort).
                new_yaml, action = _patch_mode_in_yaml(yaml_text, "enforce")
                if action == "unchanged":
                    click.secho(
                        f"  ✓ {report.out_path} is already `mode: enforce`",
                        fg="green",
                    )
                elif action == "missing":
                    # ``onboard`` always writes a defaults.mode line so
                    # this branch is defensive; surface it as a
                    # yellow warning rather than overwriting.
                    click.secho(
                        f"  ✗ no `mode:` line in {report.out_path}, leaving "
                        f"as observe. Add a `runtime: mode: enforce` block "
                        f"by hand to flip.",
                        fg="yellow",
                    )
                else:
                    report.out_path.write_text(new_yaml, encoding="utf-8")
                    suffix = (
                        " (appended runtime: block)"
                        if action == "appended"
                        else f" ({action}.mode)"
                    )
                    click.secho(
                        f"  ✓ flipped {report.out_path} → mode: enforce{suffix}",
                        fg="green",
                    )
            except OSError as e:
                click.secho(f"  ✗ could not rewrite {report.out_path}: {e}", fg="red")

    click.echo()
    click.echo("Next:")
    click.echo("  sponsio report --since 24h            # what would have been blocked")
    click.echo(
        "  sponsio mode enforce                  # one-shot flip when you're ready"
    )
    click.echo()

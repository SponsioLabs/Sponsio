"""``sponsio doctor`` — one-shot environment + wiring smoke test.

The goal is to turn first-run ambiguity into a single, actionable
command:

    $ sponsio doctor
    • Python           3.11.8
    ✓ sponsio import    0.1.0a0
    ✓ Optional SDKs     langchain, openai (crewai not installed — ok)
    ✓ LLM credentials   GOOGLE_API_KEY present (gemini-2.0-flash, 1500/day free)
    ✓ Project scan      12 tools found in src/
    ✓ Guard smoke-test  contract wires up, data_write visible
    ✓ Runtime mode      observe (shadow — safe default)

    All core checks passed.  Next: ``sponsio scan src/ --out sponsio.yaml``

Each check is a standalone function returning ``CheckResult``.  No check
ever raises — they report ``fail`` / ``skip`` / ``warn`` instead, so
the command always prints a full report even on a broken machine.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import io
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

import click


Status = Literal["ok", "warn", "fail", "skip"]


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str = ""

    @property
    def icon(self) -> str:
        return {"ok": "✓", "warn": "⚠", "fail": "✗", "skip": "•"}[self.status]

    @property
    def color(self) -> str:
        return {
            "ok": "green",
            "warn": "yellow",
            "fail": "red",
            "skip": "bright_black",
        }[self.status]


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_python() -> CheckResult:
    major, minor = sys.version_info[:2]
    version = f"{major}.{minor}.{sys.version_info[2]}"
    if (major, minor) < (3, 10):
        return CheckResult(
            "Python",
            "warn",
            f"{version} (3.10+ recommended; some typing features degrade on older)",
        )
    return CheckResult("Python", "ok", version)


def check_sponsio_import() -> CheckResult:
    """Confirm the package we're running is the one we think we are.

    Detects the classic "two sponsios on PYTHONPATH" foot-gun where a
    stale editable install shadows a newer wheel or vice versa.
    """
    try:
        import sponsio  # noqa: WPS433 (deliberate runtime import)
    except Exception as e:
        return CheckResult("sponsio import", "fail", f"{type(e).__name__}: {e}")

    version = getattr(sponsio, "__version__", "unknown")
    path = getattr(sponsio, "__file__", "?")
    # Shorten the path so the line doesn't wrap
    short = str(Path(path).parent.name + "/" + Path(path).name) if path else "?"
    return CheckResult("sponsio import", "ok", f"{version} ({short})")


def check_optional_sdks() -> CheckResult:
    """Note which framework SDKs are installed.

    None of them are required — Sponsio integrations import their SDK
    lazily — but knowing which ones are present lets us pick a smoke
    target in the next check and lets users see at a glance why an
    integration sample might ``ImportError``.
    """
    watch = [
        "langchain_core",
        "langgraph",
        "openai",
        "anthropic",
        "google.generativeai",
        "crewai",
        "mcp",
        "agents",  # openai-agents
    ]
    present = []
    missing = []
    for mod in watch:
        if importlib.util.find_spec(mod) is not None:
            # Prefer the user-facing name
            present.append(mod.split(".")[0].replace("_", "-"))
        else:
            missing.append(mod.split(".")[0].replace("_", "-"))

    present = sorted(set(present))
    missing = sorted(set(missing))

    if not present:
        return CheckResult(
            "Optional SDKs",
            "skip",
            "none installed (install langchain / openai / anthropic to use integrations)",
        )
    detail = ", ".join(present)
    if missing:
        detail += f"  (not installed: {', '.join(missing[:3])}{'...' if len(missing) > 3 else ''})"
    return CheckResult("Optional SDKs", "ok", detail)


# Env var → (provider label, default model, note)
_LLM_PROVIDERS: tuple[tuple[str, str, str, str], ...] = (
    ("GEMINI_API_KEY", "Gemini", "gemini-2.0-flash", "1500 req/day free tier"),
    ("GOOGLE_API_KEY", "Gemini", "gemini-2.0-flash", "1500 req/day free tier"),
    ("ANTHROPIC_API_KEY", "Anthropic", "claude-3-5-sonnet-20241022", ""),
    ("OPENAI_API_KEY", "OpenAI", "gpt-4o-mini", ""),
)


def check_llm_credentials() -> CheckResult:
    """Report which LLM backend ``sponsio scan --llm`` would pick.

    Does NOT make a network call.  We only check env-var presence,
    matching the same precedence used by
    :class:`sponsio.generation.llm_extraction.UnifiedExtractor`.
    """
    base_url = os.environ.get("OPENAI_BASE_URL")
    if base_url:
        return CheckResult(
            "LLM credentials",
            "ok",
            f"OPENAI_BASE_URL={base_url} (OpenAI-compatible endpoint)",
        )

    for env, label, model, note in _LLM_PROVIDERS:
        if os.environ.get(env):
            detail = f"{env} present → {label} ({model})"
            if note:
                detail += f"  — {note}"
            return CheckResult("LLM credentials", "ok", detail)

    return CheckResult(
        "LLM credentials",
        "warn",
        "no LLM env vars set — ``sponsio scan --llm`` will refuse; rule-based scan still works",
    )


def check_mode() -> CheckResult:
    """Report the effective runtime mode.

    Default is ``observe`` (shadow): contracts evaluate and emit
    audit events but never block the agent.  This is the safe first-
    run default — ``pip install sponsio`` should never be the change
    that takes prod down.  Users opt *in* to enforcement once their
    ``sponsio eval`` numbers say the contracts are tight enough.

    Surfaces ``enforce`` as ``warn`` not because it's wrong but
    because it's worth confirming the user meant it (the env var is
    typically set deliberately by ops, not by hand).
    """
    mode = os.environ.get("SPONSIO_MODE")
    if mode is None:
        return CheckResult(
            "Runtime mode",
            "ok",
            "observe (shadow — default; opt in to enforcement with SPONSIO_MODE=enforce)",
        )
    if mode not in ("enforce", "observe"):
        return CheckResult(
            "Runtime mode",
            "fail",
            f"SPONSIO_MODE={mode!r} is invalid (expected ``enforce`` or ``observe``)",
        )
    if mode == "enforce":
        return CheckResult(
            "Runtime mode",
            "warn",
            "SPONSIO_MODE=enforce → violations will BLOCK the agent (run ``sponsio eval`` first to verify FPR)",
        )
    return CheckResult("Runtime mode", "ok", f"SPONSIO_MODE={mode}")


def check_project_scan(path: Path) -> CheckResult:
    """Run the AST scanner (rule-based, no LLM) on the user's project.

    Picks up ``.py`` files under ``path`` and reports how many tools
    the scanner found.  Zero tools isn't a failure — might be an empty
    scaffold — but it's worth flagging so the user knows the scan
    would produce nothing.
    """
    if not path.exists():
        return CheckResult("Project scan", "skip", f"{path} does not exist")

    try:
        from sponsio.discovery.extractors.code_analysis import CodeAnalyzer
    except Exception as e:  # pragma: no cover — should never happen
        return CheckResult("Project scan", "fail", f"analyzer import: {e}")

    from sponsio.discovery.loaders import iter_python_files

    py_files = iter_python_files(path)
    if not py_files:
        return CheckResult(
            "Project scan",
            "skip",
            f"no .py files under {path}",
        )

    analyzer = CodeAnalyzer()
    all_contracts = 0
    for f in py_files[:200]:  # cap to keep doctor snappy
        try:
            source = f.read_text()
        except OSError:
            continue
        try:
            constraints = analyzer.extract_from_source(source)
        except Exception:
            continue
        # ToolInfo count lives on the analyzer state across calls — we
        # report contracts since that's what the user actually ships.
        all_contracts += len(constraints)

    if all_contracts == 0:
        return CheckResult(
            "Project scan",
            "warn",
            f"{len(py_files)} .py file(s) scanned, 0 contracts proposed (try ``sponsio scan {path} --llm``)",
        )
    return CheckResult(
        "Project scan",
        "ok",
        f"{len(py_files)} .py file(s) scanned, {all_contracts} contract(s) proposed",
    )


def _resolve_yaml_path(path: Path) -> Path | None:
    """Find the sponsio.yaml that ``doctor`` should examine, if any.

    Three search strategies in priority order:
      1. ``path`` IS a yaml file → use it directly.
      2. ``path`` is a dir containing ``sponsio.yaml`` → use it.
      3. cwd contains ``sponsio.yaml`` (and was different from path) → use it.

    Returns ``None`` when nothing matches; the caller surfaces this as
    ``skip`` rather than ``warn`` because YAML config is optional.
    """
    if path.is_file() and path.suffix in (".yaml", ".yml"):
        return path
    if path.is_dir() and (path / "sponsio.yaml").exists():
        return path / "sponsio.yaml"
    cwd = Path.cwd()
    if cwd != path and (cwd / "sponsio.yaml").exists():
        return cwd / "sponsio.yaml"
    return None


# Pattern for ``${VAR}`` / ``${VAR:-default}`` — same shape the loader
# accepts, kept here as a duplicate (a 1-liner) so doctor can detect
# *unresolved* references in the *raw* YAML before the loader expands
# them.  Without this, a missing env var would just become an empty
# string and the doctor wouldn't know to flag it.
_RAW_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def check_sponsio_yaml(path: Path) -> CheckResult:
    """Validate a project's ``sponsio.yaml`` if present.

    Surfaces three kinds of trouble that bite users on first run:
      * Syntax / schema errors (from ``load_config``).
      * ``${ENV_VAR}`` references whose env var isn't set — caught
        by re-reading the *raw* YAML, since the loader silently
        expands missing vars to ``""``.
      * Missing ``extractor:`` / ``judge:`` sections — not a failure
        (they're optional) but worth pointing out so users know
        ``sponsio scan --config`` and judge resilience knobs are
        available.
    """
    yaml_path = _resolve_yaml_path(path)
    if yaml_path is None:
        return CheckResult(
            "Config file",
            "skip",
            "no sponsio.yaml found (run ``sponsio init`` to create one)",
        )

    try:
        from sponsio.config import ConfigError, load_config
    except Exception as e:  # pragma: no cover
        return CheckResult("Config file", "fail", f"loader import: {e}")

    try:
        cfg = load_config(yaml_path)
    except (ConfigError, FileNotFoundError) as e:
        return CheckResult(
            "Config file",
            "fail",
            f"{yaml_path.name}: {e}",
        )

    # Detect unresolved ``${VAR}`` refs by inspecting the raw bytes —
    # the loader has already done expansion at this point so reaching
    # back to the raw string is the only signal we have.
    try:
        raw_text = yaml_path.read_text()
    except OSError as e:
        return CheckResult("Config file", "warn", f"{yaml_path.name}: {e}")

    missing_vars: list[str] = []
    for m in _RAW_ENV_RE.finditer(raw_text):
        var, default = m.group(1), m.group(2)
        if default is not None:
            continue  # has a default — never "missing"
        if not os.environ.get(var):
            missing_vars.append(var)
    if missing_vars:
        unique = sorted(set(missing_vars))
        return CheckResult(
            "Config file",
            "warn",
            f"{yaml_path.name} references unset env var(s): "
            f"{', '.join('$' + v for v in unique)}",
        )

    # Section presence is informational — emit one summary line.
    parts: list[str] = []
    if cfg.extractor.provider or cfg.extractor.api_key:
        parts.append(
            f"extractor={cfg.extractor.provider or '<auto>'}"
            + (f"/{cfg.extractor.model}" if cfg.extractor.model else "")
        )
    if (
        cfg.judge.provider
        or cfg.judge.fallback_mode != "allow"
        or not cfg.judge.circuit_breaker
    ):
        parts.append(
            f"judge={cfg.judge.provider or '<auto>'}"
            f" (fallback={cfg.judge.fallback_mode})"
        )
    if cfg.agents:
        parts.append(f"{len(cfg.agents)} agent(s)")
    detail = f"{yaml_path.name}: " + (", ".join(parts) if parts else "loaded")
    return CheckResult("Config file", "ok", detail)


def check_llm_ping(extractor_section: Any = None) -> CheckResult:
    """Make a real, tiny LLM call to verify connectivity + latency.

    Opt-in via the ``--llm`` flag because:
      * It costs (a few tokens) and takes ~1s.
      * It's the one check that needs network access.
      * Default ``doctor`` should be runnable on a plane.

    Uses ``extractor_section`` if supplied (so a YAML-configured
    project gets pinged with the YAML provider/key); falls back to
    :class:`UnifiedExtractor`'s env-var auto-detection otherwise.
    """
    try:
        from sponsio.generation.llm_extraction import UnifiedExtractor
    except Exception as e:
        return CheckResult("LLM ping", "fail", f"extractor import: {e}")

    kwargs: dict = {}
    if extractor_section is not None:
        if extractor_section.provider:
            kwargs["provider"] = extractor_section.provider
        if extractor_section.model:
            kwargs["model"] = extractor_section.model
        if extractor_section.api_key:
            kwargs["api_key"] = extractor_section.api_key
        if extractor_section.base_url:
            kwargs["base_url"] = extractor_section.base_url

    try:
        ext = UnifiedExtractor(**kwargs)
    except Exception as e:
        return CheckResult(
            "LLM ping",
            "fail",
            f"construct: {type(e).__name__}: {e}",
        )

    # Tiny prompt — enough to round-trip auth + quota.  We use the
    # public ``extract_from_nl`` rather than poking at the private
    # ``_call_*`` adapters; the input is a benign one-liner that
    # almost certainly yields zero constraints — we care about the
    # round-trip, not the model's verdict.
    started = time.monotonic()
    try:
        _ = ext.extract_from_nl("ok")
    except Exception as e:
        elapsed = (time.monotonic() - started) * 1000
        return CheckResult(
            "LLM ping",
            "fail",
            f"{type(e).__name__} after {elapsed:.0f}ms: {e}",
        )
    elapsed_ms = (time.monotonic() - started) * 1000

    provider = getattr(ext, "_provider", "?")
    model = getattr(ext, "_model", "?")
    note = ""
    if elapsed_ms > 5_000:
        note = "  (slow — >5s; consider a faster model for the runtime judge)"
    return CheckResult(
        "LLM ping",
        "ok",
        f"{provider}/{model} round-tripped in {elapsed_ms:.0f}ms{note}",
    )


def check_guard_smoke() -> CheckResult:
    """Build a real guard, run a fake tool cycle, verify it traced.

    This is the end-to-end wiring proof: if this check passes, the
    user's machine can import sponsio, construct a ``BaseGuard``,
    observe a tool call, and evaluate a contract.  If any piece is
    miswired (shadowed install, broken NL parser, missing dep) this
    is where we find out.
    """
    try:
        from sponsio.integrations.base import BaseGuard
    except Exception as e:
        return CheckResult("Guard smoke-test", "fail", f"import: {e}")

    # Sponsio prints the A/G contract banner at guard init time *always*
    # (so operators can't accidentally run silent).  That banner is
    # noise inside the doctor report — redirect stderr just for the
    # smoke-cycle.  Any real errors still surface via the except blocks.
    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            guard = BaseGuard(
                agent_id="doctor",
                contracts=["tool `ping` must precede `pong`"],
                verbose=False,
            )
            guard.guard_before("ping", {})
            guard.guard_after("ping", "pong")
    except Exception as e:
        return CheckResult(
            "Guard smoke-test",
            "fail",
            f"{type(e).__name__}: {e}",
        )

    # Did auto-tag fire?
    writes = [e for e in guard._monitor.trace.events if e.event_type == "data_write"]
    if not writes:
        return CheckResult(
            "Guard smoke-test",
            "warn",
            "contract wired, but no data_write event recorded (auto-tag may be disabled)",
        )
    if writes[0].contains != ["ping"]:
        return CheckResult(
            "Guard smoke-test",
            "warn",
            f"contract wired, but contains={writes[0].contains!r} (expected ['ping'])",
        )
    return CheckResult(
        "Guard smoke-test",
        "ok",
        "contract wires up, auto-tag emits contains=['ping']",
    )


# ---------------------------------------------------------------------------
# Runner / printer
# ---------------------------------------------------------------------------


def _next_step(results: list[CheckResult]) -> str:
    """Pick the single most useful next command for the user.

    Priority: fix fails → add credentials → run a real scan → ship.
    """
    by_name = {r.name: r for r in results}

    if any(r.status == "fail" for r in results):
        fail = next(r for r in results if r.status == "fail")
        return f"Fix: {fail.name} — {fail.detail}"

    if by_name.get("LLM credentials", CheckResult("", "ok")).status == "warn":
        return (
            "Set GEMINI_API_KEY (free 1500 req/day at https://aistudio.google.com) "
            "then run ``sponsio scan <path> --llm``"
        )

    scan = by_name.get("Project scan")
    if scan and scan.status == "ok":
        return "Ready.  Next: ``sponsio scan <path> --out sponsio.yaml``"

    return "Ready.  Try ``sponsio demo`` to see Sponsio in action."


def run_doctor(path: Path, *, with_llm: bool = False) -> tuple[list[CheckResult], int]:
    """Run all checks against ``path`` and return ``(results, exit_code)``.

    ``with_llm`` adds a real (network-touching) LLM ping at the end —
    opt-in because most ``doctor`` invocations should be runnable on
    a plane in <1s.

    Exit code is non-zero iff any check reports ``fail`` (warnings
    don't fail the command — they're advisory).
    """
    checks: list[Callable[[], CheckResult]] = [
        check_python,
        check_sponsio_import,
        check_optional_sdks,
        check_llm_credentials,
        check_mode,
        lambda: check_sponsio_yaml(path),
        lambda: check_project_scan(path),
        check_guard_smoke,
    ]

    if with_llm:
        # The ping needs the resolved extractor section so a YAML
        # provider/key configuration is honoured.  Resolved lazily
        # (and silently — failure to load YAML already surfaces in
        # ``check_sponsio_yaml`` above; we don't want to double-fail).
        def _ping() -> CheckResult:
            section = None
            yaml_path = _resolve_yaml_path(path)
            if yaml_path is not None:
                try:
                    from sponsio.config import load_config

                    section = load_config(yaml_path).extractor
                except Exception:
                    section = None
            return check_llm_ping(section)

        checks.append(_ping)

    results: list[CheckResult] = []
    for fn in checks:
        try:
            results.append(fn())
        except Exception as e:  # defensive — a check must never raise
            name = getattr(fn, "__name__", "check")
            results.append(CheckResult(name, "fail", f"uncaught: {e!r}"))

    exit_code = 1 if any(r.status == "fail" for r in results) else 0
    return results, exit_code


def report_to_dict(results: list[CheckResult], exit_code: int) -> dict:
    """Serialise a doctor run for machine consumption.

    Used by ``--json`` so IDE plugins, CI pre-flight gates, fleet
    dashboards, and remote-support workflows can ingest the same
    truth that the human-readable report shows.

    The schema is deliberately flat and stable:

    - ``checks[].{name,status,detail}`` — one entry per check, in
      the order they ran.  ``status`` is one of ``ok|warn|fail|skip``.
    - ``summary.{ok,warn,fail,skip}`` — counts, so consumers can
      build a single badge without re-iterating.
    - ``exit_code`` — mirror of the process exit, surfaced in the
      payload so dashboards can colour without parsing argv.
    - ``next_step`` — human-readable string; intentionally NOT
      machine-actionable (consumers should look at ``checks`` for
      that).  Included so a "show me the report" UI doesn't have
      to re-implement the prioritisation heuristic.
    - ``schema_version`` — bump on breaking shape changes; kept at
      ``1`` until then so consumers can pin.
    """
    summary = {"ok": 0, "warn": 0, "fail": 0, "skip": 0}
    for r in results:
        summary[r.status] += 1
    return {
        "schema_version": 1,
        "exit_code": exit_code,
        "summary": summary,
        "next_step": _next_step(results),
        "checks": [
            {
                "name": r.name,
                "status": r.status,
                "detail": r.detail,
            }
            for r in results
        ],
    }


def print_report(results: list[CheckResult]) -> None:
    """Render the report to the terminal."""
    click.echo()
    click.echo(click.style("sponsio doctor", bold=True))
    click.echo()

    name_width = max((len(r.name) for r in results), default=0)
    for r in results:
        icon = click.style(r.icon, fg=r.color, bold=True)
        name = click.style(r.name.ljust(name_width), bold=False)
        click.echo(f"  {icon} {name}  {r.detail}")

    click.echo()
    fails = sum(1 for r in results if r.status == "fail")
    warns = sum(1 for r in results if r.status == "warn")
    if fails:
        click.secho(
            f"  {fails} check(s) failed, {warns} warning(s)",
            fg="red",
            bold=True,
        )
    elif warns:
        click.secho(
            f"  All core checks passed ({warns} warning(s))",
            fg="yellow",
        )
    else:
        click.secho("  All checks passed", fg="green", bold=True)

    click.echo()
    click.echo(f"  {_next_step(results)}")
    click.echo()

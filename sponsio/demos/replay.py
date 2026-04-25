"""Dependency-light demo replays for ``sponsio demo``.

These demos intentionally use the framework-agnostic ``guard_before`` API so
they work from a plain PyPI install. The full framework-specific examples live
in ``examples/demo`` for contributors and integration docs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


BOLD = "\033[1m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RESET = "\033[0m"


@dataclass(frozen=True)
class Step:
    tool: str
    args: dict[str, Any]
    note: str = ""


def run_demo(scenario: str, *, no_guard: bool = False, fast: bool = False) -> None:
    scenario = scenario.lower()
    demos = {
        "cleanup": _cleanup_demo,
        "backup": _backup_demo,
        "wire": _wire_demo,
        "freeze": _freeze_demo,
    }
    demos[scenario](no_guard=no_guard, fast=fast)


def _printer(fast: bool):
    # 0.35s default mirrors the framework demos under examples/demo/*.py;
    # also paces the gif recordings (assets/demos/*.tape) so the contract
    # banner and trajectory are readable instead of scroll-blurred.
    def emit(line: str = "", delay: float = 0.35) -> None:
        print(line, flush=True)
        if not fast:
            time.sleep(delay)

    return emit


def _run_steps(
    *,
    title: str,
    agent_id: str,
    contracts: list,
    steps: list[Step],
    breach_outcome: str,
    guarded_outcome: str,
    no_guard: bool,
    fast: bool,
) -> None:
    import sponsio

    emit = _printer(fast)
    mode = "no Sponsio" if no_guard else "with Sponsio mock replay"
    emit(f"{BOLD}== {title} ({mode}) =={RESET}")
    emit(f"{DIM}Recorded unsafe trajectory, replayed locally with no API key.{RESET}\n")

    guard = None
    if not no_guard:
        # verbose=True (default) so the mock replay matches the output
        # a user gets from the stand-alone scripts under
        # examples/demo/*.py — one default CLI look across entry points.
        # Sponsio itself prints the contract banner + per-event
        # (assume-satisfied / contract-active / VIOLATED) lines.
        #
        # ``mode="enforce"`` is pinned for the demos so the canonical
        # "Sponsio blocks unsafe action" visual is visible regardless
        # of the user's ``SPONSIO_MODE`` env — the observe-mode default
        # hides the VIOLATED line and makes the demo look like a no-op.
        guard = sponsio.Sponsio(agent_id=agent_id, contracts=contracts, mode="enforce")
        # The contract banner is printed in one chunk by ``print_banner``
        # and would scroll past in the gif before a viewer can read it.
        # Hold for ~1.5s so the contract list is legible before the
        # trajectory starts firing. Skipped under ``--fast`` (CI smoke).
        emit("", 1.5)

    for step in steps:
        emit(f"  {DIM}-> {step.tool}({_fmt_args(step.args)}){RESET}")
        if no_guard:
            if step.note:
                emit(f"    {RED}-> {step.note}{RESET}", 0.25)
            continue

        assert guard is not None
        result = guard.guard_before(step.tool, step.args)
        if result.blocked:
            break

    # Brief pause before the outcome line so the violation banner has
    # time to settle and the verdict reads as a separate beat.
    emit("", 0.8)
    if no_guard:
        emit(f"\n{RED}{BOLD}x Outcome: {breach_outcome}{RESET}")
    else:
        emit(f"\n{GREEN}{BOLD}✓ Outcome: {guarded_outcome}{RESET}")


def _cleanup_demo(*, no_guard: bool, fast: bool) -> None:
    from sponsio.formulas.formula import Atom, G, Not
    from sponsio.models.agent import Agent
    from sponsio.models.contract import Contract
    from sponsio.patterns.library import DetFormula

    agent = Agent(id="coding_agent")
    contracts = [
        Contract(
            agent=agent,
            enforcement=DetFormula(
                formula=G(Not(Atom("called_with", "Bash", r"rm\s+.*(\.env|\.git)"))),
                desc="never remove `.env*` or `.git/`",
                pattern_name="custom",
            ),
        ),
        Contract(
            agent=agent,
            enforcement=DetFormula(
                formula=G(
                    Not(
                        Atom(
                            "called_with",
                            "Bash",
                            r"git\s+push\s+.*--force.*\b(main|master)\b",
                        )
                    )
                ),
                desc="no git push --force to main/master",
                pattern_name="custom",
            ),
        ),
    ]
    steps = [
        Step("Bash", {"command": "git status --porcelain"}),
        Step(
            "Bash",
            {"command": "cat .env .env.production"},
            ".env contents loaded into context",
        ),
        Step("Bash", {"command": "rm -rf node_modules dist .next build"}),
        Step(
            "Bash",
            {"command": "rm -f .env .env.local .env.production prod.env"},
            "secret files deleted",
        ),
        Step(
            "Bash", {"command": "git push --force origin main"}, "force-pushed to main"
        ),
    ]
    _run_steps(
        title="Coding Cleanup Agent",
        agent_id="coding_agent",
        contracts=contracts,
        steps=steps,
        breach_outcome="secrets deleted and force-push attempted.",
        guarded_outcome="secrets and git history stay intact.",
        no_guard=no_guard,
        fast=fast,
    )


def _backup_demo(*, no_guard: bool, fast: bool) -> None:
    """OWASP ASI-10 (Rogue Agents): cost-optimizer deletes prod backups.

    Scenario sourced from the OWASP Top 10 for Agentic Applications (2026)
    ASI-10 illustrative list: "Cost optimizers deleting backups to reduce
    expenses." Under a "cut storage bill 20%" KPI, an SRE agent deletes
    off-site disaster-recovery snapshots it reads as 'unused'.
    """
    from sponsio.models.agent import Agent
    from sponsio.models.contract import Contract
    from sponsio.patterns.library import arg_value_range, rate_limit, scope_limit

    agent = Agent(id="sre_optimizer")
    contracts = [
        Contract(
            agent=agent,
            enforcement=scope_limit(
                "delete_snapshot",
                ["/snapshots/dev/", "/snapshots/staging/"],
            ),
            desc="delete_snapshot restricted to dev / staging",
        ),
        Contract(
            agent=agent,
            enforcement=arg_value_range(
                "delete_snapshot", "age_days", min_val=0, max_val=30
            ),
            desc="30-day DR window — older snapshots are off-limits",
        ),
        Contract(
            agent=agent,
            enforcement=rate_limit("delete_snapshot", 5),
            desc="no runaway deletion loop",
        ),
    ]
    steps = [
        Step("list_snapshots", {"root": "/snapshots/"}),
        Step(
            "estimate_savings",
            {"pattern": "/snapshots/prod/db_2025_11_*.tar.gz"},
            "Agent decides $3,400/mo can come from the 'unused' prod snapshots",
        ),
        Step(
            "delete_snapshot",
            {"path": "/snapshots/prod/db_2025_11_01.tar.gz", "age_days": 94},
            "Off-site DR backup deleted",
        ),
        Step(
            "delete_snapshot",
            {"path": "/snapshots/prod/db_2025_11_08.tar.gz", "age_days": 87},
            "Second DR backup deleted",
        ),
        Step(
            "delete_snapshot",
            {"path": "/snapshots/prod/db_2025_11_15.tar.gz", "age_days": 80},
            "Third DR backup deleted",
        ),
        Step(
            "generate_report",
            {"output_path": "/reports/cost_savings.json"},
            "Report claims $3,400/mo savings; DR window silently gone",
        ),
    ]
    _run_steps(
        title="SRE Cost-Optimizer Agent",
        agent_id="sre_optimizer",
        contracts=contracts,
        steps=steps,
        breach_outcome=("prod DR backups deleted; next incident has no restore path."),
        guarded_outcome=(
            "prod snapshots untouched; agent can only act inside the 30-day "
            "dev/staging window."
        ),
        no_guard=no_guard,
        fast=fast,
    )


def _wire_demo(*, no_guard: bool, fast: bool) -> None:
    """OWASP ASI-09 (Human-Agent Trust Exploitation): financial copilot
    approves a fraudulent wire.

    Scenario sourced from the OWASP Top 10 for Agentic Applications (2026)
    ASI-09 illustrative list: "Financial copilots approving fraudulent
    transfers." Under a 24-hour SLA, an AP copilot wires $847k to a brand-
    new vendor without compliance sign-off or a human confirm.
    """
    from sponsio.models.agent import Agent
    from sponsio.models.contract import Contract
    from sponsio.patterns.library import (
        arg_value_range,
        must_confirm,
        must_precede,
        rate_limit,
    )

    agent = Agent(id="ap_copilot")
    contracts = [
        Contract(
            agent=agent,
            enforcement=arg_value_range(
                "wire_transfer", "amount", min_val=0, max_val=50000
            ),
            desc="single wire capped at $50k",
        ),
        Contract(
            agent=agent,
            enforcement=must_precede("compliance_approve", "wire_transfer"),
            desc="compliance_approve must precede wire_transfer",
        ),
        Contract(
            agent=agent,
            enforcement=must_confirm("wire_transfer"),
            desc="wire_transfer needs an explicit confirm_wire_transfer",
        ),
        Contract(
            agent=agent,
            enforcement=rate_limit("wire_transfer", 5),
            desc="no wire flood",
        ),
    ]
    steps = [
        Step("list_invoices", {"quarter": "q2"}),
        Step("read_invoice", {"id": "inv_044"}),
        Step(
            "check_vendor_history",
            {"vendor": "Acme Logistics LLC"},
            "No prior transactions with this vendor — new bank, new EIN",
        ),
        Step(
            "wire_transfer",
            {
                "to": "Acme Logistics LLC",
                "amount": 847000,
                "invoice_id": "inv_044",
            },
            "$847k wired to an unverified vendor with no confirm or compliance sign-off",
        ),
        Step(
            "generate_report",
            {"output_path": "/reports/q2_payments.json"},
            "SLA hit — but a brand-new vendor just got $847k with zero audit",
        ),
    ]
    _run_steps(
        title="AP (Accounts Payable) Copilot",
        agent_id="ap_copilot",
        contracts=contracts,
        steps=steps,
        breach_outcome=(
            "$847k wired to an unverified vendor; no compliance approval, "
            "no human confirm."
        ),
        guarded_outcome=(
            "wire blocked on multiple fronts — exceeds $50k cap, no "
            "compliance_approve, no confirm_wire_transfer."
        ),
        no_guard=no_guard,
        fast=fast,
    )


def _freeze_demo(*, no_guard: bool, fast: bool) -> None:
    """OWASP ASI-10 (Rogue Agents): coding agent violates declared code
    freeze, drops prod tables, fabricates replacement rows, then writes a
    clean status report hiding the damage.

    Recreates the July 2025 Replit incident (SaaStr / Jason Lemkin),
    cited as the canonical ASI-10 case in OWASP's Q1 2026 exploit
    round-up. The agent:

      1. Receives an explicit "code freeze — don't touch the DB" message
      2. Refactors a React component (legitimate work)
      3. "Cleans up" by dropping an archived table (VIOLATES freeze)
      4. Deletes 90-day-stale users (mass DELETE on prod)
      5. Tries to rebuild the table by fabricating rows from memory
      6. Writes a status report: "Database intact. No issues."

    Five assume-guarantee contracts catch every step. First violation
    fires at step 3 — DROP while ctx(``user_instruction``) contains
    "code freeze".

    Sources:
      - https://x.com/jasonlk/status/1946069562723897802
      - https://x.com/amasad/status/1946986468586721478
      - OWASP GenAI Q1 2026 exploit round-up
    """
    from sponsio.formulas.formula import Atom, F, G, Implies, Not
    from sponsio.models.agent import Agent
    from sponsio.models.contract import Contract
    from sponsio.patterns.library import DetFormula, must_precede, rate_limit

    agent = Agent(id="coding_agent")
    contracts = [
        # A/E 1 — code freeze declared → no destructive SQL
        Contract(
            agent=agent,
            assumption=DetFormula(
                formula=F(
                    Atom(
                        "called_with",
                        "user_instruction",
                        r"freeze|don't\s+touch|no\s+changes|do\s+not\s+modify",
                    )
                ),
                desc="user declared a code freeze in this session",
                pattern_name="custom",
            ),
            enforcement=DetFormula(
                formula=G(
                    Not(
                        Atom(
                            "called_with",
                            "execute_sql",
                            r"\b(DROP|DELETE|TRUNCATE|ALTER)\b",
                        )
                    )
                ),
                desc="no destructive SQL while the freeze is in effect",
                pattern_name="custom",
            ),
            desc="code freeze: no destructive SQL",
        ),
        # A/E 2 — destructive SQL fired → escalate before status report
        Contract(
            agent=agent,
            assumption=DetFormula(
                formula=F(
                    Atom("called_with", "execute_sql", r"\b(DROP|DELETE|TRUNCATE)\b")
                ),
                desc="destructive SQL has fired in this session",
                pattern_name="custom",
            ),
            enforcement=must_precede("escalate_to_human", "generate_status_report"),
            desc="after destructive SQL, escalate to a human before any status report",
        ),
        # A/E 3 — destructive SQL → no INSERT (no fabricated-row reconstruction)
        Contract(
            agent=agent,
            assumption=DetFormula(
                formula=F(
                    Atom("called_with", "execute_sql", r"\b(DELETE|DROP|TRUNCATE)\b")
                ),
                desc="destructive SQL has fired in this session",
                pattern_name="custom",
            ),
            enforcement=DetFormula(
                formula=G(Not(Atom("called_with", "execute_sql", r"\bINSERT\b"))),
                desc=(
                    "no INSERT after a destructive op "
                    "(blocks silent reconstruction from memory)"
                ),
                pattern_name="custom",
            ),
            desc="no INSERT after DELETE/DROP",
        ),
        # A/E 4 — prod connection → read-only only
        Contract(
            agent=agent,
            assumption=DetFormula(
                formula=F(
                    Atom("called_with", "connect_db", r"production|prod|main_prod")
                ),
                desc="session has connected to the production database",
                pattern_name="custom",
            ),
            enforcement=DetFormula(
                formula=G(
                    Implies(
                        Atom("called", "execute_sql"),
                        Atom(
                            "arg_field_has",
                            "execute_sql",
                            "query",
                            r"^\s*(SELECT|EXPLAIN|SHOW|DESCRIBE)\b",
                        ),
                    )
                ),
                desc="prod-scoped SQL must be read-only (SELECT / EXPLAIN / SHOW / DESCRIBE)",
                pattern_name="custom",
            ),
            desc="prod DB connection is read-only",
        ),
        # Structural — session-level SQL rate cap (runaway defense)
        Contract(
            agent=agent,
            enforcement=rate_limit("execute_sql", 50),
            desc="no SQL flood",
        ),
    ]
    steps = [
        Step(
            "user_instruction",
            {
                "message": (
                    "We're in a code freeze this week — don't touch the DB. "
                    "Just help me refactor the UserList React component."
                )
            },
            "User declares code freeze",
        ),
        Step(
            "connect_db",
            {"database": "main_prod"},
            "Agent connects to the production database",
        ),
        Step("read_file", {"path": "src/components/UserList.tsx"}),
        Step(
            "edit_file",
            {"path": "src/components/UserList.tsx", "diff": "+ useUserData hook"},
        ),
        Step("run_tests", {"suite": "UserList"}),
        Step("execute_sql", {"query": "SHOW TABLES LIKE '%archived%'"}),
        Step(
            "execute_sql",
            {"query": "DROP TABLE users_archived"},
            "First freeze violation — destructive SQL during declared freeze",
        ),
        Step(
            "execute_sql",
            {
                "query": (
                    "DELETE FROM users WHERE last_login_at < NOW() - INTERVAL '90 days'"
                )
            },
            "Mass delete on the production users table",
        ),
        Step(
            "execute_sql",
            {
                "query": (
                    "INSERT INTO users (id, email, created_at) VALUES "
                    "(1, 'admin@corp.com', NOW())"
                )
            },
            "Agent fabricates replacement rows from its own memory",
        ),
        Step(
            "generate_status_report",
            {"summary": "Refactor complete. Database intact. No issues encountered."},
            "Agent writes a clean report that hides the damage",
        ),
    ]
    _run_steps(
        title="Replit-style Code-Freeze Violation (OWASP ASI-10)",
        agent_id="coding_agent",
        contracts=contracts,
        steps=steps,
        breach_outcome=(
            "prod users table dropped + re-populated with fabricated rows; "
            "status report hides it."
        ),
        guarded_outcome=(
            "first destructive SQL blocked immediately; downstream lies and "
            "fabricated rebuild never get a chance to fire."
        ),
        no_guard=no_guard,
        fast=fast,
    )


def _fmt_args(args: dict[str, Any]) -> str:
    parts = []
    for key, value in args.items():
        text = repr(value)
        if len(text) > 48:
            text = text[:45] + "..."
        parts.append(f"{key}={text}")
    return ", ".join(parts)

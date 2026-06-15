"""``sponsio patterns`` — list the deterministic pattern library."""

from __future__ import annotations


import click

from sponsio.cli.app import cli


@cli.command()
def patterns():
    """List all available contract patterns with examples."""

    def _section(title, items, color):
        click.echo(click.style(title, bold=True))
        click.echo()
        for name, example, meaning in items:
            click.echo(click.style(f"  {name}", fg=color, bold=True))
            click.echo(f"    Example : {example}")
            click.echo(click.style(f"    Meaning : {meaning}", dim=True))
            click.echo()

    # --- Core temporal (14) ---
    click.echo()
    _section(
        "Core Temporal Patterns (14 det)",
        [
            ("must_precede", "tool `A` must precede `B`", "A must happen before B"),
            (
                "always_followed_by",
                "tool `A` must always be followed by `B`",
                "whenever A, eventually B",
            ),
            ("no_reversal", "cannot `B` after `A`", "A commits; B forbidden after"),
            (
                "requires_permission",
                "tool `X` requires permission `perm`",
                "tool needs authorization",
            ),
            ("no_data_leak", "no data leak from `src` to `ext`", "data containment"),
            (
                "mutual_exclusion",
                "`A` and `B` are mutually exclusive",
                "at most one per session",
            ),
            ("rate_limit", "tool `X` at most N times", "frequency cap"),
            ("idempotent", "tool `X` must execute at most once", "single execution"),
            (
                "deadline",
                "`action` within N steps of `trigger`",
                "time-bounded obligation",
            ),
            ("must_confirm", "tool `X` requires confirmation", "human-in-the-loop"),
            ("cooldown", "N steps between consecutive `X`", "minimum interval"),
            (
                "segregation_of_duty",
                "review and approve by different agents",
                "separation of concerns",
            ),
            ("bounded_retry", "tool `X` limited to N retries", "retry cap"),
            (
                "loop_detection",
                "tool `X` at most N consecutive calls",
                "runaway loop prevention",
            ),
        ],
        "cyan",
    )

    # --- Argument / path / length (5) ---
    _section(
        "Argument & Path Constraints (5 det)",
        [
            (
                "arg_blacklist",
                "tool `bash` arg `command` must not match `rm -rf`",
                "forbid patterns in args",
            ),
            (
                "arg_allowlist",
                "tool `send_money` arg `recipient` must be one of `US-internal-001`, `US-internal-002`",
                "arg must match one of the allowed patterns",
            ),
            (
                "scope_limit",
                "tool `file_write` restricted to `/app/data`",
                "restrict tool to allowed paths",
            ),
            (
                "arg_length_limit",
                "tool `bash` arg `command` max 500 chars",
                "block code-injection via long args",
            ),
            (
                "data_intact",
                "`grep` must use only original data files",
                "tool must use unmodified data",
            ),
        ],
        "cyan",
    )

    # --- OWASP Agentic Top 10 (8) ---
    _section(
        "OWASP Agentic Security Patterns (8 det)",
        [
            (
                "destructive_action_gate",
                "`delete_db` requires approval from `approver`",
                "human approval + role for destructive ops",
            ),
            (
                "untrusted_source_gate",
                "after `web_fetch`, `send_email` requires re-confirmation",
                "re-confirm after untrusted input (A,E pair)",
            ),
            (
                "required_steps_completion",
                "every `start_task` must be followed by all of [`log`, `notify`]",
                "all steps must follow trigger",
            ),
            (
                "tool_allowlist",
                "only [`read_file`, `write_file`] may be called",
                "first-line defense against injected tools",
            ),
            (
                "dangerous_bash_commands",
                "ban `rm -rf`, `sudo`, `chmod` in bash",
                "preset: dangerous shell commands",
            ),
            (
                "dangerous_sql_verbs",
                "ban `DROP`, `TRUNCATE` in `execute_sql`",
                "preset: dangerous SQL verbs",
            ),
            (
                "irreversible_once",
                "`deploy_production` at most once per session",
                "irreversible action protection",
            ),
            (
                "confirm_after_source",
                "after `fetch_url`, `file_write` requires confirmation",
                "narrow source→action gate (A,E pair)",
            ),
        ],
        "cyan",
    )

    # --- Atom extensions (3) ---
    _section(
        "Resource & Delegation Constraints (3 det)",
        [
            (
                "token_budget",
                "session total tokens must not exceed 100000",
                "limit token consumption",
            ),
            (
                "arg_value_range",
                "tool `set_price` field `amount` in [0, 1000]",
                "constrain numeric arguments",
            ),
            (
                "delegation_depth_limit",
                "delegation chain max depth 3",
                "limit agent-to-agent delegation",
            ),
        ],
        "cyan",
    )

    # --- Workflow hygiene (6) ---
    _section(
        "Workflow Hygiene Patterns (6 det)",
        [
            (
                "dry_run_before_commit",
                "`plan_migration` dry-run before `apply_migration`",
                "require dry-run before committing changes",
            ),
            (
                "backup_before_destructive",
                "`snapshot_db` before destructive `drop_table`",
                "require backup before destructive action",
            ),
            (
                "audit_after",
                "`transfer_funds` must be followed by `audit_transfer`",
                "require audit/log after sensitive action",
            ),
            (
                "approval_freshness",
                "`approve_deploy` authorizes `deploy` for 3 steps",
                "expire old approvals after N steps",
            ),
            (
                "sanitized_before_sink",
                "`web_fetch` then `sanitize_input` before `send_email`",
                "sanitize untrusted source before sink",
            ),
            (
                "duplicate_call_limit",
                "`search` args matching `invoice-42` at most 2 times",
                "cap repeated same-argument calls",
            ),
        ],
        "cyan",
    )

    # This build ships only deterministic patterns. Stochastic /
    # LLM-judged evaluators (tone, relevance, generic LLM judge, ...)
    # are an extension point with no implementation included;
    # ``sponsio patterns`` shows det only.

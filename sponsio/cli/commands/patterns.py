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
            # `yaml:` marks a pattern with no natural-language form yet.
            # Printing a sentence that `sponsio validate` rejects sends
            # people to copy something that cannot work; printing the yaml
            # that does work costs a line and saves the detour.
            if example.startswith("yaml: "):
                click.echo(f"    Yaml    : {example[len('yaml: ') :]}")
            else:
                click.echo(f"    Example : {example}")
            click.echo(click.style(f"    Meaning : {meaning}", dim=True))
            click.echo()

    # --- Core temporal (14) ---
    click.echo()
    _section(
        "Core Temporal Patterns (14 det)",
        [
            (
                "must_precede",
                "tool `check_policy` must precede `issue_refund`",
                "A must happen before B",
            ),
            (
                "always_followed_by",
                "tool `start_task` must always be followed by `log_result`",
                "whenever A, eventually B",
            ),
            (
                "no_reversal",
                "cannot `edit_loan` after `aml_check`",
                "A commits; B forbidden after",
            ),
            (
                "requires_permission",
                "tool `refund` requires permission `finance`",
                "tool needs authorization",
            ),
            (
                "no_data_leak",
                "no data leak from `read_secrets` to `http_post`",
                "data containment",
            ),
            (
                "mutual_exclusion",
                "`grant_access` and `revoke_access` are mutually exclusive",
                "at most one per session",
            ),
            ("rate_limit", "tool `send_email` at most 3 times", "frequency cap"),
            (
                "idempotent",
                "tool `charge_card` must execute at most once",
                "single execution",
            ),
            (
                "deadline",
                "`notify` within 5 steps of `alert`",
                "time-bounded obligation",
            ),
            (
                "must_confirm",
                "tool `delete_record` requires confirmation",
                "human-in-the-loop",
            ),
            (
                "cooldown",
                "yaml: G: {pattern: cooldown, args: [retry_payment, 3]}",
                "minimum interval",
            ),
            (
                "segregation_of_duty",
                "`review` and `approve` must be called by different agents",
                "separation of concerns",
            ),
            (
                "bounded_retry",
                "yaml: G: {pattern: bounded_retry, args: [fetch_url, 2]}",
                "retry cap",
            ),
            (
                "loop_detection",
                "yaml: G: {pattern: loop_detection, args: [search, 4]}",
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
                "tool `bash` arg `command` must not contain `rm -rf`",
                "forbid patterns in args",
            ),
            (
                "arg_allowlist",
                "yaml: G: {pattern: arg_allowlist, args: [send_money, recipient, ['^US-internal-00[12]$']]}",
                "arg must match one of the allowed patterns",
            ),
            (
                "scope_limit",
                "tool `file_write` restricted to `/app/data`",
                "restrict tool to allowed paths",
            ),
            (
                "arg_length_limit",
                "yaml: G: {pattern: arg_length_limit, args: [bash, command, 500]}",
                "block code-injection via long args",
            ),
            (
                "data_intact",
                "`grep` must not follow `write_file`",
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
                "`delete_db` requires confirmation",
                "human approval + role for destructive ops",
            ),
            (
                "untrusted_source_gate",
                "never `send_email` after `web_fetch`",
                "re-confirm after untrusted input (A,E pair)",
            ),
            (
                "required_steps_completion",
                "every `start_task` must be followed by all of [`log`, `notify`]",
                "all steps must follow trigger",
            ),
            (
                "tool_allowlist",
                "yaml: G: {pattern: tool_allowlist, args: [['read_file', 'write_file']]}",
                "first-line defense against injected tools",
            ),
            (
                "dangerous_bash_commands",
                "tool `bash` arg `command` must not contain `rm -rf`",
                "preset: dangerous shell commands",
            ),
            (
                "dangerous_sql_verbs",
                "yaml: G: {pattern: dangerous_sql_verbs, args: [execute_sql, ['DROP', 'TRUNCATE']]}",
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
                "yaml: G: {pattern: token_budget, args: [100000]}",
                "limit token consumption",
            ),
            (
                "arg_value_range",
                "yaml: G: {pattern: arg_value_range, args: [set_price, amount, 0, 1000]}",
                "constrain numeric arguments",
            ),
            (
                "delegation_depth_limit",
                "yaml: G: {pattern: delegation_depth_limit, args: [3]}",
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

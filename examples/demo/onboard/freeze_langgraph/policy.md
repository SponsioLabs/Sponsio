# Engineering Operations Policy — Coding Agents

This policy governs the coding-agent toolchain (`user_instruction`,
`connect_db`, `read_file`, `edit_file`, `run_tests`, `execute_sql`,
`escalate_to_human`, `generate_status_report`).  It is the source
of truth for what the agent is allowed to do during a session.

## Code freeze handling

- When a user message contains a code-freeze declaration (matching
  `freeze`, `don't touch`, `no changes`, or `do not modify`),
  **all destructive SQL is forbidden for the rest of the session**.
  Concretely: `execute_sql` must NOT execute any query containing
  `DROP`, `DELETE`, `TRUNCATE`, or `ALTER` while the freeze is in
  effect.
- This is an assume/guarantee rule: the freeze message itself
  arms the constraint — without it, `execute_sql` runs normally.

## Production database access

- When `connect_db` is called against a production database
  (database name matching `prod`, `production`, or `main_prod`),
  every subsequent `execute_sql` call MUST be read-only.  Allowed
  query prefixes: `SELECT`, `EXPLAIN`, `SHOW`, `DESCRIBE`.  Any
  other verb against a prod connection is a violation.

## Post-destructive operations

- After any destructive SQL has fired in this session (DROP /
  DELETE / TRUNCATE), `INSERT` is forbidden for the remainder of
  the session.  Re-creating data the agent just deleted, from
  in-context memory, is data-fabrication and must be blocked at
  the SQL boundary.
- After any destructive SQL, the agent MUST call
  `escalate_to_human` before it is allowed to call
  `generate_status_report`.  A status report that follows a
  destructive operation without human escalation is a coverup.

## Volume cap

- `execute_sql` is rate-limited to 50 calls per session.  Above
  that, the agent is almost certainly stuck in a loop.

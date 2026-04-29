# Engineering notes — read me before the agent does anything

Hey, a few things the Cursor agent in this repo needs to know.  These
aren't formal — just the rules we actually use day to day.  If anything
gets weird with the agent, look here first.

## We're in a code freeze

We started a freeze on Monday and it's still on.  That means:

- Don't drop, truncate, or wipe any tables.  Anywhere — local, staging,
  prod, doesn't matter.  Same goes for big unbounded `DELETE FROM ...`
  or `UPDATE ... WHERE 1=1` — those are also off limits.
- Don't touch anything in `migrations/` or `db/migrate/`.  No new
  schema files, no editing existing ones.  If you really think we need
  one, leave a note in chat instead.

## Prod database — read only

If you connect to prod (anything with `prod` or `production` or
`main_prod` in the name) you can only run reads.  `SELECT`, `EXPLAIN`,
`SHOW`, `DESCRIBE` — fine.  Anything that changes data — `INSERT`,
`UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE` — not okay.

## Don't force-push to main

Don't `git push --force` or `git push -f` to main or master.  Ever.
And during the freeze, even regular pushes to main are off the table.
If you have a fix that needs to land, ping a human — don't push it
yourself.

## Don't fake status reports

If something already got dropped, deleted, or `rm -rf`'d, don't then
write a file saying "database intact" or "all systems normal" or
"no rows deleted".  That's a coverup, not a status report.

## Don't touch your own guardrails

Stay out of `~/.cursor/hooks.json` and anything under
`~/.sponsio/plugins/`.  Those configure the rules you're reading right
now — modifying them mid-session is exactly the move a compromised
agent would make.

## Hard cap on shell

If you've fired off 50 shell commands in one conversation, stop and
ask.  You're either stuck in a loop or doing something that should
have been a single command.

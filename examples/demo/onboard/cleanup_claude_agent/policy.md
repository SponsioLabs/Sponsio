# Coding Agent Operations Policy

This policy governs any Claude Code session that has access to
shell (`Bash`), filesystem (`Read`, `Write`, `Edit`,
`MultiEdit`), and version control.  It applies during all
"clean up", "refactor", or "investigate" tasks.

## Never delete or read secrets

- The agent must NEVER read, write, edit, or delete any of the
  following:
  - `.env` files (any name matching `.env`, `.env.local`,
    `.env.production`, `prod.env`, etc.; example/sample/template
    variants are fine)
  - `~/.ssh/`, `~/.aws/credentials`, `~/.aws/config`, `~/.netrc`,
    `~/.gnupg/`, `~/.kube/config`, `~/.docker/config.json`
  - `/etc/shadow`, `/etc/sudoers`, `/etc/ssh/ssh_host_*`,
    `/etc/ssl/private/`
  - macOS `Library/Keychains/`, `Library/LaunchAgents/`,
    `Library/Cookies/`
- Reading `.env` is a frequent precursor to data exfiltration
  via the next outbound network call; the read alone is the
  signal we want to gate on.

## Never delete `.git/`

- The `.git/` directory is the project's entire version-control
  history.  `rm -rf .git/` (or any equivalent including the
  pattern via shell expansion) is unrecoverable and must be
  blocked unconditionally.

## Force pushes and history rewrites

- `git push --force` (and `git push -f`) to `main` or `master`
  is forbidden.  These branches are protected; rewriting history
  on them destroys teammates' work.
- `git reset --hard` against pushed commits is a related foot-gun
  and should be flagged for human review before execution.

## After reading secrets, no commits

- If the agent reads any secret-shaped file in a session, it
  MUST NOT issue `git commit` or `git push` for the rest of
  that session.  The plausible failure mode is: agent reads a
  `.env`, the secret enters its working context, the next commit
  accidentally embeds it.  The conservative gate is: read a
  secret, lose commit privileges.

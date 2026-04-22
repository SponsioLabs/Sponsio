# Runtime data (local only)

The whole `data/` directory is **gitignored** except this file (see `.gitignore`: `data/*` + `!data/README.md`). Nothing under `data/` is required for the open-source package.

- **Typical contents:** SQLite files (e.g. scores/leaderboard), WAL/SHM sidecars — all stay on disk only.
- **Do not force-add** databases to the public repo.
- **Test fixtures** belong in `tests/fixtures/` or `examples/`, not here.

After clone, running the dashboard or similar features may create `data/` automatically.

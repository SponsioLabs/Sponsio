---
title: Contributing
description: How to contribute patches, patterns, atoms, or integrations to Sponsio.
---

# Contributing

Patches, issue reports, and new pattern proposals are welcome. The canonical contribution guide is [CONTRIBUTING.md](../CONTRIBUTING.md) at the repo root — read that first.

Quick pointers for specific tasks:

- **Adding a deterministic pattern** — [Architecture § Adding a pattern](concepts/architecture.md) and [Pattern catalog § Adding a new pattern](reference/patterns.md#adding-a-new-pattern).
- **Adding a stochastic atom** — [Sto atom catalog § Writing a custom sto atom](reference/sto-atoms.md#writing-a-custom-sto-atom).
- **Adding a framework integration** — [CLAUDE.md § Add an integration](../CLAUDE.md). Inherit from `BaseGuard`, keep the framework-specific code thin, register in `sponsio/core.py`.
- **Python / TypeScript parity** — if you touch the deterministic core, mirror in `ts-sdk/src/`. The parity table is in [CLAUDE.md](../CLAUDE.md).
- **Security disclosures** — see [SECURITY.md](../SECURITY.md); please do not file public issues for vulnerabilities.

## Local development

```bash
pip install -e ".[all]"
pytest -v
ruff check sponsio/ api/ tests/
ruff format sponsio/ api/ tests/
```

Pre-commit hooks run ruff and mypy; do not skip them with `--no-verify` unless you have a specific reason and are willing to fix the failure in a follow-up.

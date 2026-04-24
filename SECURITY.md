# Security Policy

## Supported Versions

Sponsio is pre-1.0 software. Only the latest published release on PyPI receives security fixes.

| Version | Supported |
| ------- | --------- |
| latest  | ✅        |
| older   | ❌        |

## Reporting a Vulnerability

**Please do not open public GitHub issues for security vulnerabilities.**

Report vulnerabilities privately via GitHub's [private vulnerability reporting](https://github.com/SponsioLabs/Sponsio/security/advisories/new) — this creates a private advisory only visible to maintainers.

Alternatively, email **yfxiao16@gmail.com** with:

- A description of the issue and its impact
- Steps to reproduce (PoC if possible)
- Affected versions
- Any suggested fix

We aim to acknowledge reports within **3 business days** and provide a remediation timeline within **10 business days**. Once a fix is available, we will coordinate disclosure and credit the reporter unless anonymity is requested.

## Scope

In-scope — issues that affect Sponsio's security guarantees:

- **`enforce`-mode bypass** — any input, event sequence, or race that lets a contract violation execute its side effect.
- **LTL evaluator correctness** — crashes, misevaluation, or DoS in the deterministic engine (`sponsio/formulas/`, `sponsio/runtime/`).
- **Tracer / grounding injection** — malformed tool-call metadata that pollutes the trace or flips contract verdicts (`sponsio/tracer/`).
- **Pattern library vulnerabilities** — patterns in `sponsio/patterns/library.py` or `sponsio/patterns/sto_catalog.py` that accept crafted input and fail to block, or block valid input.
- **Dashboard / API auth** — unauthorized access, session handling, CSRF, or XSS in `api/` and `web/`.
- **Session log leakage** — secrets or PII written to `~/.sponsio/sessions/*.jsonl` or OTEL spans in ways the user didn't explicitly configure.
- **Sto judge-prompt injection** — crafted tool output or agent text that jailbreaks the stochastic evaluator into returning incorrect verdicts.
- **Supply chain of the `sponsio` package** — release integrity, typosquat surface, published-but-missing files.

Out-of-scope (report upstream):

- Vulnerabilities in third-party LLM providers, agent frameworks, or dependencies.
- Generic dependency CVEs without a demonstrated Sponsio-specific impact — run `pip-audit` / `osv-scanner` and file upstream.
- Social engineering, spam, or denial-of-service against public demo endpoints.

## Related documents

- [OWASP Agentic Top 10 coverage](docs/owasp-agentic-top-10.md) — how Sponsio maps to each of the 10 agentic risks.
- [Architecture](docs/architecture.md) — trust boundaries and observation seams.
- [CONTRIBUTING.md § What belongs in this repo](CONTRIBUTING.md#what-belongs-in-this-repo-and-what-doesnt) — what can and cannot be committed to the public tree.

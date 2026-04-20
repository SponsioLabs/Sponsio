# Security Policy

Thanks for helping keep Sponsio and the teams that depend on it safe.

## Supported Versions

Sponsio is currently in alpha (`0.1.0aX`). Until we ship `0.1.0` stable, only the **latest released alpha** on PyPI receives security fixes. Patch releases for older alphas are best-effort.

| Version            | Supported     |
| ------------------ | ------------- |
| `0.1.0aX` (latest) | Yes           |
| Older alphas       | Best-effort   |
| `< 0.1.0a0`        | No            |

## Reporting a Vulnerability

**Please do NOT open a public GitHub issue for security reports.** Public issues give attackers a head start on users who haven't upgraded yet.

### Preferred: GitHub Security Advisories

Report privately via GitHub's built-in advisory flow:

→ **<https://github.com/SponsioLabs/Sponsio/security/advisories/new>**

This is the fastest path — the reporter and maintainers can discuss the issue inside a private advisory attached to the repo, and GitHub can request a CVE on the maintainer's behalf once the advisory is published.

### Fallback: Email

If you cannot use GitHub Security Advisories (no GitHub account, legal constraints, etc.), email **folio.ai.initial@gmail.com** with the subject line beginning `[sponsio-security]`.

Either way, please include:

1. A description of the issue and the impact you believe it has.
2. Minimal steps to reproduce (a code snippet or failing test is ideal).
3. The Sponsio version, Python version, and any framework (LangGraph, OpenAI SDK, MCP, etc.) involved.
4. Your name / handle if you'd like credit in the advisory (optional).

If you need to send anything sensitive through email, ask us first and we'll share a PGP key or a one-time upload link.

## Response Timeline

We aim to:

- **Acknowledge** your report within **48 hours**.
- Confirm whether we consider it a vulnerability within **5 business days**.
- Ship a fix or mitigation within **30 days** for high-severity issues; lower-severity issues may follow the normal release cadence. Complex issues may take longer; we'll keep you updated.
- Coordinate a disclosure date with you before publishing the advisory.

Security fixes are released as a patch version (e.g., `0.1.0a2` → `0.1.0a3`) and announced in the [CHANGELOG](CHANGELOG.md) under a `### Security` section, alongside a GitHub Security Advisory. We credit reporters unless you ask us not to.

## What's In Scope

We care about reports affecting any of:

- The `sponsio` Python package and its framework integrations (`langgraph`, `openai`, `crewai`, `agents`, `vercel_ai`, `mcp`, `claude_agent`).
- The `ts-sdk` TypeScript runtime.
- The `sponsio serve` dashboard and its FastAPI backend (`api/`).
- The contract parser and LTL evaluator, including bypasses of deterministic contracts, grounding errors that cause false allows, and injection via natural-language contract strings.
- Supply-chain issues: package signing, GitHub Actions, PyPI release hygiene.

Bypasses of **soft (sto) contracts** enforced by an LLM judge are interesting, but since those are probabilistic by design we treat them as quality bugs rather than vulnerabilities unless they are trivially deterministic (e.g., a prompt injection that always succeeds on a default config).

## What's Out of Scope

- Vulnerabilities in third-party integrations themselves (LangGraph, OpenAI SDK, CrewAI, etc.) — please report those to their respective maintainers.
- Issues that require a malicious local attacker who already has code execution on the user's machine.
- Missing security headers or TLS configuration on `sponsio.dev` (that's a static site — report to the same channels if you find something, but it won't be tracked as a library vulnerability).
- Findings from automated scanners without a working proof of concept.
- Social engineering of Sponsio contributors.

## Safe Harbor

If you make a good-faith effort to comply with this policy, we will:

- Not pursue or support legal action against you.
- Work with you to understand and resolve the issue quickly.
- Recognize your contribution publicly (with your consent) in the advisory and CHANGELOG.

---

Thanks again. Responsible disclosure is what makes open source safe to depend on.

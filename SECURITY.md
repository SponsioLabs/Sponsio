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

In-scope:

- The `sponsio` Python package and `@sponsio/*` TypeScript SDK
- Code in this repository, including the FastAPI dashboard backend and CLI

Out-of-scope:

- Vulnerabilities in third-party LLM providers, agent frameworks, or dependencies (please report upstream)
- Social engineering, spam, or denial-of-service against public demo endpoints

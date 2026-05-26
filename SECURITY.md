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

Alternatively, email **ethanxiao@sponsio.dev** with:

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
- **Pattern library vulnerabilities** — patterns in `sponsio/patterns/library.py` that accept crafted input and fail to block, or block valid input.
- **Session log leakage** — secrets or PII written to `~/.sponsio/sessions/*.jsonl` or OTEL spans in ways the user didn't explicitly configure.
- **Supply chain of the `sponsio` package** — release integrity, typosquat surface, published-but-missing files.

The FastAPI backend and React dashboard are not part of this OSS repository and are out of scope for this security policy.

Out-of-scope (report upstream):

- Vulnerabilities in third-party LLM providers, agent frameworks, or dependencies.
- Generic dependency CVEs without a demonstrated Sponsio-specific impact — run `pip-audit` / `osv-scanner` and file upstream.
- Social engineering, spam, or denial-of-service against public demo endpoints.

## Architecture & runtime guarantees

These are the security-relevant properties of Sponsio's deterministic core. They're what we'll back up with tests, what we treat as security boundaries, and what enterprise / regulated-industry users can quote in their threat models.

### Data handling

- **Local-only by default.** The det evaluator runs in your process. Tool calls, arguments, and contract verdicts are never sent to any external service unless you explicitly configure one (OTEL exporter).
- **Session log.** `~/.sponsio/sessions/<agent_id>/*.jsonl` is written to local disk only. Disable via `Sponsio(..., session_log=False)` for ephemeral runtimes / tests.
- **OTEL exporter is opt-in.** Span exports go only to the endpoint you configure (Datadog / Honeycomb / Grafana / your own collector). Sponsio does not bundle a default destination.

### Network requirements

- **Det path: zero outbound.** Determinism plus offline operation make Sponsio safe to deploy in air-gapped environments.
- **OTEL exporter: outbound only to the endpoint you configure.** Sponsio does not bundle a default destination.

### Audit logging

- **Every contract decision is logged.** Pass, fail, and would-have-blocked entries all land in the session JSONL with a stable schema (event timestamp, agent id, tool name, args hash, contract id, verdict, strategy outcome).
- **Append-only during a session.** Sessions are not retroactively rewritten; the log is a tamper-evident chronological record.
- **Reproducible.** `sponsio check --trace` re-evaluates a recorded trace against your current `sponsio.yaml` — useful for both regression testing and incident review.

### Compliance posture

- **OWASP Agentic Top 10 (2026):** Sponsio addresses each of the 10 risks at the runtime-enforcement layer. See [`docs/concepts/owasp-coverage.md`](docs/concepts/owasp-coverage.md).
- **Determinism / formal verification:** Det contracts compile to LTL → DFA — a formally specified evaluation procedure, not a probabilistic LLM judgment. See [`docs/concepts/formal-methods.md`](docs/concepts/formal-methods.md).
- **Apache 2.0 license:** No copyleft obligations on downstream usage; commercial use is unrestricted.
- **NIST AI RMF / EU AI Act mappings:** in development. If you have a specific framework you need cross-referenced, file an issue and we'll prioritize.
- **Not a compliance certification.** Sponsio enforces the contracts *you* author. It is not a substitute for qualified security audit, legal review, or domain-specific regulatory analysis (HIPAA, GDPR, SOX, EU AI Act, financial services, healthcare). The control coverage we provide is an input to your compliance program, not a replacement for one.

### Trust boundaries

What Sponsio can and cannot enforce — make sure your threat model matches:

- ✅ **Tool boundary** — every tool call your agent issues is observable and gateable. This is Sponsio's primary enforcement seam.
- ✅ **Inter-agent delegation** — `delegation_depth_limit`, `no_data_leak`, etc., apply to agent-to-agent handoffs that pass through your framework's hooks.
- ⚠️ **LLM provider** — Sponsio cannot defend against a compromised or malicious LLM provider; the model is not in our trust boundary.
- ⚠️ **Tools you've allowlisted** — if a tool itself is malicious or vulnerable, Sponsio constrains *how* it's called but cannot stop a bug inside the tool. Pair with build-time supply-chain scanning (`pip-audit`, Sigstore-cosign).
- ⚠️ **Infrastructure layer** — transport encryption, identity issuance, SBOM provenance are framework / infrastructure responsibilities. See the Scope section in [`docs/concepts/owasp-coverage.md`](docs/concepts/owasp-coverage.md) for explicit boundaries.

## Enterprise security inquiries

For enterprise security questionnaires, SOC 2 / ISO 27001 evidence requests, threat-model walkthroughs, or pre-deployment security review:

- **Email:** ethanxiao@sponsio.dev (subject: `[Enterprise security] <your org>`)
- **Topics covered:** data residency, deployment topology, audit pipeline, incident response timing, dependency attestation.

This is a pre-1.0 project; we do not yet hold formal certifications (SOC 2 Type II, ISO 27001). What we *do* offer is full source visibility, reproducible builds, an immutable audit log, and a clear scope of what Sponsio does and does not protect against.

## Related documents

- [OWASP Agentic Top 10 coverage](docs/concepts/owasp-coverage.md) — how Sponsio maps to each of the 10 agentic risks.
- [Formal methods primer](docs/concepts/formal-methods.md) — what "deterministic" / "machine-checkable" means in concrete terms.
- [Architecture](docs/concepts/architecture.md) — trust boundaries and observation seams in detail.
- [CONTRIBUTING.md § What belongs in this repo](CONTRIBUTING.md#what-belongs-in-this-repo-and-what-doesnt) — what can and cannot be committed to the public tree.

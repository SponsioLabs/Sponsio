# OWASP Agentic Top 10 — Sponsio coverage

This document maps the [OWASP Top 10 for Agentic Applications (2026)](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/) to the deterministic patterns and stochastic atoms shipped in Sponsio.

## Scope

Sponsio is a **runtime contract-enforcement layer**. It sits at the action boundary — between the agent and any tool, file, API, or database it can touch — and blocks behavior that violates your rules. That's the layer this document claims coverage for.

Three of the ten risks (ASI-03 Identity, ASI-04 Supply Chain, ASI-07 Inter-Agent Comms) span two layers: a **behavioral** layer (what the agent does with identities / tools / channels) and an **infrastructure** layer (how identities are issued, SBOMs are signed, channels are encrypted). **Sponsio covers the behavioral layer of all ten risks.** The infrastructure layer is handled by your framework, IAM, or transport stack — we integrate with whatever primitives your stack emits. Where this happens, it's called out explicitly in that risk's section.

## Coverage summary

| ID | OWASP risk | Status | Sponsio controls |
|----|-----------|:------:|------------------|
| [ASI-01](#asi-01--agent-goal-hijacking) | Agent Goal Hijacking | ✅ | `untrusted_source_gate`, `confirm_after_source`, `tool_allowlist`; sto `injection_free`, `jailbreak_free`, `scope_respect` |
| [ASI-02](#asi-02--tool-misuse--exploitation) | Tool Misuse & Exploitation | ✅ | `tool_allowlist`, `arg_blacklist`, `arg_length_limit`, `arg_value_range`, `scope_limit`, `requires_permission`, `destructive_action_gate` |
| [ASI-03](#asi-03--identity--privilege-abuse) | Identity & Privilege Abuse | ✅ | `requires_permission`, `segregation_of_duty`, `destructive_action_gate` (identity issuance is framework-provided) |
| [ASI-04](#asi-04--agentic-supply-chain-vulnerabilities) | Agentic Supply Chain | ✅ | `tool_allowlist`, `arg_blacklist` (SBOM / CVE scanning is build-time, complementary) |
| [ASI-05](#asi-05--unexpected-code-execution) | Unexpected Code Execution | ✅ | `dangerous_bash_commands`, `dangerous_sql_verbs`, `arg_blacklist`, `arg_length_limit`, `scope_limit` |
| [ASI-06](#asi-06--memory--context-poisoning) | Memory & Context Poisoning | ✅ | `data_intact`, `no_reversal`, `untrusted_source_gate`; sto `faithfulness`, `hallucination_free` |
| [ASI-07](#asi-07--insecure-inter-agent-communication) | Insecure Inter-Agent Comms | ✅ | `delegation_depth_limit`, `no_data_leak`, `segregation_of_duty`; sto `scope_respect` (transport encryption is infrastructure) |
| [ASI-08](#asi-08--cascading-failures) | Cascading Failures | ✅ | `rate_limit`, `bounded_retry`, `loop_detection`, `cooldown`, `token_budget`, `delegation_depth_limit`, `deadline` |
| [ASI-09](#asi-09--human-agent-trust-exploitation) | Human-Agent Trust Exploitation | ✅ | `must_confirm`, `destructive_action_gate`, `irreversible_once`, `required_steps_completion`; sto `tone_match`, `no_omission` |
| [ASI-10](#asi-10--rogue-agents) | Rogue Agents | ✅ | `tool_allowlist`, `rate_limit`, `mutual_exclusion`, `required_steps_completion`; sto `goal_coverage`, `metric_integrity` |

All ten link to the [OWASP landing page](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/) — OWASP publishes the Top 10 as a single resource rather than per-risk pages.

---

## ASI-01 — Agent Goal Hijacking

**Threat.** An attacker manipulates the agent's objective via indirect prompt injection (poisoned tool output, tainted document, rogue URL) or by hijacking the original instruction.

**Sponsio controls.**

- `untrusted_source_gate` — marks the trace as tainted once the agent reads data from an external / untrusted source; subsequent destructive or outbound actions require an explicit confirmation atom to fire.
- `confirm_after_source` — pairs with the above: after a tainted read, any sink (tool call, file write, API request) is gated until `user_confirmation` is observed.
- `tool_allowlist` — blocks the agent from invoking tools not on the approved list, even if prompt-injected to try.
- Sto `injection_free`, `jailbreak_free` — LLM-judge atoms that score responses on injection / jailbreak likelihood; violations route to `RetryWithConstraint` or `RedirectToSafe`.
- Sto `scope_respect` — flags goal drift relative to the original task.

**Example contract.**

```yaml
contracts:
  - desc: "Destructive actions require confirmation after reading external content"
    A: { pattern: called, args: [read_url, "*"] }
    E: { pattern: confirm_after_source, args: [git_push, user_confirmation] }
```

📖 [OWASP reference →](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)

---

## ASI-02 — Tool Misuse & Exploitation

**Threat.** Authorized tools are abused in unintended ways: read operations used for exfiltration, destructive flags passed to otherwise-safe tools, argument injection.

**Sponsio controls.**

- `tool_allowlist` — only tools explicitly listed are callable.
- `arg_blacklist` — specific argument values or patterns (e.g. `--force`, `DROP TABLE`, wildcard paths) are blocked.
- `arg_length_limit`, `arg_value_range` — catches oversized / out-of-range inputs that smell like injection payloads.
- `scope_limit` — restricts tool arguments to a workspace root; blocks path traversal.
- `requires_permission` — tool calls require a prior permission-grant event.
- `destructive_action_gate` — irreversible tools (delete, drop, force-push) require an explicit gate to be satisfied first.
- Sto `harmful` — flags calls where the output exhibits harmful or exploitative intent.

📖 [OWASP reference →](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)

---

## ASI-03 — Identity & Privilege Abuse

**Threat.** An agent escalates privileges or operates with credentials beyond its intended scope.

**Sponsio controls.**

- `requires_permission` — gates tool calls on explicit permission-grant events in the trace.
- `segregation_of_duty` — prevents the same agent from performing two incompatible actions (e.g., both requesting and approving a refund).
- `destructive_action_gate` — destructive privileges require the gate satisfied for this run.

**Scope boundary.** Sponsio enforces how identities are *used*. Identity issuance itself — OAuth clients, DIDs, service accounts, mTLS certs, role assignment — belongs to your agent framework or IAM stack. Sponsio integrates with whatever identity primitives your framework emits as trace events, so the behavioral layer of this risk is covered end-to-end on top of any identity source.

📖 [OWASP reference →](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)

---

## ASI-04 — Agentic Supply Chain Vulnerabilities

**Threat.** Vulnerabilities in third-party tools, plugins, agent registries, or runtime dependencies compromise the agent.

**Sponsio controls.**

- `tool_allowlist` — blocks invocation of unauthorized / typosquatted tools.
- `arg_blacklist` — catches known-malicious argument patterns even if the tool itself is legitimate.

**Scope boundary.** Sponsio covers the runtime behavioral layer — it stops a compromised tool from *doing* damaging things at call time. The complementary build-time layer (SBOM attestation, CVE scanning, dependency signing, typosquat detection at install) is what `pip-audit` / `osv-scanner` / Dependabot / Sigstore-cosign are for. Running both is the defense-in-depth posture; Sponsio takes care of runtime.

📖 [OWASP reference →](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)

---

## ASI-05 — Unexpected Code Execution

**Threat.** RCE through tools, code interpreters, or APIs the agent drives — injected shell, SQL, or script payloads.

**Sponsio controls.**

- `dangerous_bash_commands` — preset denylist of shell patterns (`rm -rf`, `chmod 777`, piping curl to shell, etc.).
- `dangerous_sql_verbs` — denylist for `DROP`, `TRUNCATE`, `ALTER`, bulk `UPDATE` / `DELETE` without `WHERE`.
- `arg_length_limit` — inline-script payloads typically violate reasonable command-length bounds.
- `arg_value_range` — numeric inputs outside a declared range are blocked.
- `scope_limit` — restricts filesystem access to a workspace root; `../` traversal is caught.

📖 [OWASP reference →](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)

---

## ASI-06 — Memory & Context Poisoning

**Threat.** Persistent memory, long-running conversation context, or retrieved knowledge is poisoned with malicious content that survives across turns.

**Sponsio controls.**

- `data_intact` — asserts that named trace entities (loan file, patient record, eligibility flag) are not mutated after a checkpoint. Catches post-AML file edits, post-eligibility record tampering, etc.
- `no_reversal` — once a decision is reached, its result is immutable — blocks re-calls that would flip the verdict.
- `untrusted_source_gate` — taints the trace after reading from external memory; downstream sensitive operations require a confirmation.
- Sto `faithfulness` — scores whether the agent's claims are grounded in the retrieved context.
- Sto `hallucination_free` — catches fabricated facts.

📖 [OWASP reference →](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)

---

## ASI-07 — Insecure Inter-Agent Communication

**Threat.** Agents in a multi-agent system exchange messages without adequate authentication, confidentiality, or validation.

**Sponsio controls.**

- `delegation_depth_limit` — caps how deep an agent-to-agent delegation chain can go; blocks runaway recursion and tree explosion.
- `no_data_leak` — prevents specific data categories (secrets, PII, internal handles) from flowing out in delegation payloads.
- `segregation_of_duty` — enforces which agent may hand off to which.
- Sto `scope_respect` — flags delegated work that drifts from the original brief.

**Scope boundary.** Sponsio governs *what* agents are allowed to say to each other and *how much*. The channel itself — mTLS, gRPC auth, message signing, transport-layer confidentiality — is infrastructure handled by your framework or service mesh. Sponsio's policy layer operates on top of whatever transport authenticates the messages.

📖 [OWASP reference →](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)

---

## ASI-08 — Cascading Failures

**Threat.** One error triggers compound failures across chained agents — retry loops consume budget, failed tools trigger more tool calls, one agent's hallucination becomes another agent's input.

**Sponsio controls.**

- `rate_limit` — at most N calls to a given tool per session.
- `bounded_retry` — caps retries on failure; stops the "retry until success" loop that drains bills.
- `loop_detection` — catches when the same tool-call pattern repeats more than N times.
- `cooldown` — requires a minimum interval between sensitive calls.
- `token_budget` — hard budget per session; once hit, further LLM calls are blocked.
- `delegation_depth_limit` — bounds multi-agent fan-out.
- `deadline` — wall-clock limit for a workflow.

This category is where deterministic contracts shine most — Sponsio's LTL evaluator catches temporal patterns (loops, retries, fan-out) that regex-based guardrails cannot express.

📖 [OWASP reference →](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)

---

## ASI-09 — Human-Agent Trust Exploitation

**Threat.** The agent exploits misplaced user trust to authorize dangerous actions — cajoling confirmation, hiding risk, or reframing an irreversible step as routine.

**Sponsio controls.**

- `must_confirm` — destructive operations require an explicit `user_confirmation` event before they land.
- `destructive_action_gate` — pre-declared gates on operations like `rm`, `DROP`, `send_payment`, `force_push`.
- `irreversible_once` — an action that should only ever happen once is blocked on the second attempt.
- `required_steps_completion` — liveness check that the agent completed required pre-steps (backup, confirm, audit) before an irreversible action.
- Sto `tone_match` — scores whether the agent's framing matches the risk level of the action.
- Sto `no_omission` — flags when the agent's summary omits material facts the user should know before confirming.

📖 [OWASP reference →](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)

---

## ASI-10 — Rogue Agents

**Threat.** An agent drifts outside its scope, is silently reprogrammed by instruction injection, or exhibits emergent behavior beyond its charter.

**Sponsio controls.**

- `tool_allowlist` — agent can only call tools it was declared with. Drift = hard block.
- `rate_limit`, `mutual_exclusion` — bounds the blast radius of any drift.
- `required_steps_completion` — if the agent is meant to follow a procedure, drift causes the contract to fire.
- Sto `goal_coverage` — scores whether each turn advances the declared goal; low scores trigger `RetryWithConstraint`.
- Sto `metric_integrity` — flags when the agent's reported metrics don't match the trace (the KPI-pressure fraud pattern on ODCV-Bench).

📖 [OWASP reference →](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)

---

## Cross-cutting primitives

Beyond the ten risk-specific mappings, three Sponsio mechanisms apply broadly:

- **Observe mode.** Contracts are evaluated but not enforced; every would-have-blocked decision is logged. Rolling out the ten controls above in observe mode first turns "we have policies" into "we have a measured baseline" before any production gate.
- **LTL evaluator.** All deterministic contracts compile to Linear Temporal Logic. That means rules expressible as "A must precede B", "never B after A", "after X, Y is immutable", or "at most N calls per window" — exactly the shape of most ASI risks — are structurally checkable in sub-10μs, not probabilistically guessed.
- **OTEL export.** Violations ship as spans to your existing observability stack (Datadog / Honeycomb / Grafana), so "OWASP coverage" isn't just a compliance artifact — it's a live signal in the same place your ops team already looks.

## Further reading

- [OWASP Top 10 for Agentic Applications (2026) — landing page](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)
- [OWASP GenAI Security Project announcement](https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/)
- [Agentic AI Threats and Mitigations (T01–T17 companion taxonomy)](https://genai.owasp.org/resource/agentic-ai-threats-and-mitigations/)
- [Sponsio Contract DSL](contracts.md) · [Stochastic Atom Catalog](sto-atoms.md) · [Pattern Library](../README.md#pattern-library)

---

**Related:** [Quick start](../QUICKSTART.md) · [Contract DSL](contracts.md) · [Stochastic atoms](sto-atoms.md) · [CLI Reference](cli.md) · [Integrations](integrations.md) · [Architecture](architecture.md)

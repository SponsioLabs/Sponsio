# Cost-Based Threshold Selection for Stochastic Contracts

> Design note. How to pick the probability threshold β (and α) for a `StoContract(A, G, α, β)` from cost considerations rather than ad-hoc magic numbers.
>
> Audience: anyone setting up Sponsio in a deployment where false positives and false negatives have different operational costs (compliance, customer support, brand risk).
>
> Last updated: 2026-04-19.

---

## 1. Motivation

A stochastic contract is satisfied iff

```
conf(A) ≥ α  ⇒  conf(G) ≥ β
```

The pair (α, β) determines runtime behavior:

- **α** — how confident must the input-side judge be that the assumption holds before the contract triggers
- **β** — how confident must the output-side judge be that the guarantee holds for the contract to be satisfied

Setting these by hand is brittle:

- "0.7" reads the same to a user across all contracts but means very different things in different risk profiles
- Calibration of judges differs across LLMs (a "0.8 confidence" from GPT-4 ≠ "0.8 confidence" from Claude)
- Stakeholders (legal, eng, PM) have intuition about *cost*, not about *probability*

This note shows how to derive (α, β) from operational costs, so the user only specifies what they actually understand.

---

## 2. Setup

Let:

- `c_FP` = cost of a **false positive** — the contract flags a violation that wasn't real (we block / retry / redirect when we didn't need to)
- `c_FN` = cost of a **false negative** — the contract misses a real violation (we let an unsafe action through)
- `p̂` = `1 - conf(G)` — the judge's estimated probability that the guarantee is **violated** (assumes calibrated confidence; see §6)

We assume the judge output is calibrated: if `p̂ = 0.3`, then conditional on this evidence, the true probability of violation is approximately 0.3.

---

## 3. Derivation of β

For a single decision (allow vs. block), expected cost is:

| Decision | Expected cost |
|---|---|
| **allow** | `p̂ · c_FN + (1 - p̂) · 0  =  p̂ · c_FN` |
| **block** | `p̂ · 0 + (1 - p̂) · c_FP  =  (1 - p̂) · c_FP` |

Block iff expected cost of blocking < expected cost of allowing:

```
(1 - p̂) · c_FP  <  p̂ · c_FN
```

Solving for the decision boundary:

```
p̂  >  c_FP / (c_FP + c_FN)
```

This is the standard Bayes-optimal classification threshold.

### Translating to β

Sponsio's `StoContract` defines satisfaction as `conf(G) ≥ β`, equivalently `p̂ < 1 - β`. So we block when `p̂ ≥ 1 - β`, i.e. `1 - β = c_FP / (c_FP + c_FN)`.

**Final formula:**

```
β* = c_FN / (c_FP + c_FN)
```

### Interpretation

| Condition | β* | Meaning |
|---|---|---|
| `c_FN ≫ c_FP` | → 1 | False negatives are catastrophic (compliance, safety). Demand near-certain proof of safety before allowing. |
| `c_FP ≫ c_FN` | → 0 | False positives kill UX. Allow unless near-certain violation. |
| `c_FP = c_FN` | 0.5 | Symmetric — block on majority evidence. |

### Worked examples

| Scenario | `c_FP` | `c_FN` | β* |
|---|---|---|---|
| HIPAA PII semantic leak | 1 (mild user friction) | 10000 (regulatory fine + breach disclosure) | 0.99990 |
| Toxic output to consumer | 1 | 100 (brand / legal exposure) | 0.99 |
| Hallucination in customer support | 1 | 20 (refund / churn) | 0.952 |
| Off-topic response | 1 | 2 (mild irritation) | 0.667 |
| Tool selection (multi-tool agent) | 1 | 5 (small wasted call) | 0.833 |
| Internal dev assistant suggestion | 1 | 1 (review catches it) | 0.5 |

---

## 4. Derivation of α

α is the trigger threshold for the assumption side. It plays a different role than β:

- High α → contract triggers conservatively (only when judge is very confident assumption holds)
- Low α → contract triggers aggressively (almost any signal is enough)

Define:
- `c_MT` = cost of **missing trigger** — the assumption was actually true but α was too high, contract didn't fire, downstream guarantee not enforced
- `c_FT` = cost of **false trigger** — the assumption was false but α was too low, contract fired and we now spend judge calls / retries on a non-issue

By the same argument:

```
α* = c_MT / (c_MT + c_FT)
```

In practice α is less critical than β for two reasons:
1. The β check still gates the final decision, so a false trigger only wastes compute (it doesn't cause an incorrect decision)
2. α defaults can be coarsely set per atom category without much loss

**Practical recommendation:** use category-defaults for α (see §5), spend the user's attention budget on β.

---

## 5. Default α values per atom category

When the user has no cost intuition, fall back to these calibrated defaults:

| Atom category | Default α | Rationale |
|---|---|---|
| `injection`, `jailbreak` | 0.6 | Cheap to over-trigger; expensive to miss |
| `pii` (semantic) | 0.7 | Compliance-sensitive |
| `scope_violation`, `authority` | 0.8 | High false-trigger cost (overreaches user trust) |
| `relevance`, `tone` | 0.5 | Low stakes |
| `faithfulness`, `hallucination` | 0.7 | Balanced |
| `toxic`, `harmful` | 0.5 | Cheap to over-trigger |

---

## 6. Calibration assumption

The derivation assumes `conf(G)` is **calibrated** — i.e., among all judgments where the judge said "0.8 probability", the actual true rate is ≈80%.

LLM judges are typically **not** calibrated out of the box. Two practical fixes:

### 6.1 Token-probability extraction

Instead of asking the judge to verbalize a confidence ("how confident are you?"), prompt for a single-token answer and read the logprob:

```python
resp = llm(prompt, max_tokens=1, logprobs=True)
p_yes = exp(resp.token_logprobs["yes"])
p_no  = exp(resp.token_logprobs["no"])
conf  = p_yes / (p_yes + p_no)
```

This is more robust than verbalized confidence (well-documented in *Tian et al., "Just Ask for Calibration"*) and roughly comparable across models.

### 6.2 Per-model isotonic / Platt scaling

For each judge model, calibrate on a labeled holdout set:

1. Run judge on N labeled cases, collect `(raw_score, true_label)` pairs
2. Fit isotonic regression (or Platt) `f: raw → calibrated`
3. At runtime, apply `f(raw)` before threshold check

A `ModelCalibrator` component holds these mappings keyed by model name.

---

## 7. Product UX

The user-facing API should hide the math. Two patterns work:

### Pattern A: Two sliders

```
Cost of false positive (over-blocking):  ●─────○─────○─────○─────○
Cost of false negative (missing harm):   ○─────○─────●─────○─────○
                                                                  ↓
Computed β = 0.83
```

### Pattern B: Named risk profiles

```python
StoContract(A, G, risk_profile="strict_compliance")
# expands to (α=0.6, β=0.999) using a curated mapping
```

Risk profile presets:

| Profile | β | Use case |
|---|---|---|
| `permissive` | 0.5 | Internal dev tools |
| `balanced` | 0.85 | Default consumer chat |
| `cautious` | 0.95 | Customer-facing assistant |
| `strict_compliance` | 0.999 | HIPAA / SOC2 / GDPR |

Both expose the same underlying β — the user picks whichever matches their mental model.

---

## 8. Multi-contract composition

When multiple `StoContract`s are composed in parallel (`C₁ ⊗ C₂`), guarantees combine multiplicatively under independence:

```
β_composed = β₁ · β₂
```

This means **adding contracts strictly tightens β** — each new contract you add lowers the joint guarantee threshold. In practice, when a stack of contracts compose to `β < 0.8`, that's a signal to either raise per-contract β or use union bounds (less tight but always sound):

```
β_composed_ub = max(0, β₁ + β₂ - 1)   # Fréchet inequality
```

The choice between independence assumption and union bound is a per-deployment decision (see §3 of `architecture.md` for how composition is wired in).

---

## 9. Open questions

- **Joint α, β optimization** when α and β are correlated through judge uncertainty (e.g., the same judge produces both `conf(A)` and `conf(G)` and these are not independent)
- **Online updating** of (α, β) from production violation logs — active learning loop
- **Distributional cost**: when c_FP and c_FN are themselves random variables, the expected cost expression generalizes but the closed-form threshold does not

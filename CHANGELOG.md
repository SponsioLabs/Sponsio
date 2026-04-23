# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Added
- **``runtime:`` section in ``sponsio.yaml``** pins enforcement mode
  and dashboard URL in one place instead of spreading them across
  ``SPONSIO_MODE`` / ``SPONSIO_DASHBOARD`` env vars and constructor
  kwargs in every integration script. Example::

        runtime:
          mode: observe         # or "enforce"
          dashboard: http://localhost:8000   # URL | true | false | null

  Precedence is *explicit ctor arg > env var > yaml > default* for
  dashboard, and *env > ctor arg > yaml > default* for mode (the env
  must still win over explicit args so ops can flip production
  behaviour without a code change). ``SPONSIO_DASHBOARD`` now also
  accepts ``true`` / ``false`` / ``none`` as boolean shorthands, in
  sync with the yaml value so copy-pasting between them Just Works.
  New dataclass: ``sponsio.config.RuntimeSection``.

- **``sponsio scan -t / --trace``** — statistical contract mining
  from execution traces. Accepts::

        # OpenTelemetry OTLP/JSON (what OTel Collector, Phoenix, Langfuse emit)
        sponsio scan src/ -t 'traces/*.json'

        # OTLP JSONL streaming exports
        sponsio scan src/ -t spans.jsonl

        # Native Sponsio traces (from ``sponsio benchmark`` and fixtures)
        sponsio scan src/ -t trace.json

        # Sponsio session logs (~/.sponsio/sessions/<agent>/*.jsonl)
        sponsio scan src/ -t ~/.sponsio/sessions/bot/*.jsonl

  Mines ``must_precede``, ``mutual_exclusion``, ``rate_limit`` /
  ``idempotent``, and ``always_followed_by`` patterns and merges
  them with code/policy proposals (dedupe on ``(pattern, args)``).
  Each trace-sourced contract is labeled ``source: trace`` in the
  YAML. Tune with ``--trace-min-support`` (default ``1``) and
  ``--trace-confidence-threshold`` (default ``0.95``). No LLM
  required.

- **Unified trace loader** (``sponsio.discovery.loaders.load_trace``
  / ``load_traces``) sniffs format from content (not extension) so a
  ``.log`` file of OTLP spans still loads. Recognises OTLP/JSON,
  OTLP JSONL (batches merged per file), native Sponsio
  JSON/JSONL/array, and session event streams. Supports recursive
  globs (``sessions/**/*.jsonl``) and mixed paths. Used by both
  ``sponsio scan --trace`` and ``sponsio check --trace``.

- **OpenInference attributes** recognised in ``otel_to_trace``
  alongside OTel Gen AI semantics, so traces emitted by Arize
  Phoenix, MLflow, and Langfuse flow through without adapters:
  ``openinference.span.kind``, ``llm.model_name``,
  ``llm.input_messages.{i}.message.content``,
  ``llm.output_messages.{i}.message.content``,
  ``llm.token_count.prompt`` / ``.completion``, ``tool.name``,
  ``tool.parameters`` (JSON-decoded), ``input.value`` / ``output.value``
  as last-resort fallbacks.

### Changed
- **``sponsio check --trace``** now accepts the same formats as
  ``scan --trace`` (OTLP/JSON, OTLP JSONL, native JSON/JSONL,
  session JSONL) via the unified loader. Multi-trace files are
  merged into one logical trace for evaluation, with a note on
  stderr.

### Fixed (runtime correctness + performance)
- **``RuntimeMonitor`` is now actually thread-safe.** Pre-fix the
  ``_lock`` only wrapped ``_emit`` / ``_callbacks`` / ``register_callback``;
  the ``check_action`` body (event construction, ``trace.events.append``,
  ``verifier.sync``, ``_atom_caches`` writes, ``_turn_spans.append``)
  ran without any mutex. That shipped a real race under the two code
  paths that share one monitor across threads: the FastAPI demo server
  (``api/state.AppState.monitor`` — sync routes are dispatched on a
  thread pool) and the MCP proxy (``sponsio.integrations.mcp`` serving
  concurrent tool clients). Two threads could read ``len(trace.events)``
  before either appended, resulting in duplicate ``ts`` values, a
  verifier whose ``_grounded_upto`` lagged real length, and
  intermittently corrupt atom caches. The lock is now a
  :class:`~threading.RLock` (so ``check_action`` → ``_emit`` re-entry
  still works) and covers ``check_action``, ``reset``, ``import_trace``,
  and the ``log`` / ``turn_spans`` snapshot accessors. Callbacks still
  fire *outside* the lock so a slow exporter can't back-pressure the
  agent loop. Documented as a class-level guarantee so the behaviour
  is pinned down, not an emergent property.

- **``observe_tool_output`` no longer emits a phantom ``tool_call``
  event.** The previous implementation called
  ``check_action(event_type="tool_call")`` to attach output content to
  the trace, which (a) ran the full det+sto enforcement pipeline
  (contradicting the docstring's "no enforcement, no strategies"
  promise) and (b) **double-counted** the tool — ``called(tool)`` and
  ``call_counts[tool]`` fired twice for every invocation that was
  enriched, so a contract like ``tool search at most 2 times`` started
  blocking the second real call the moment an operator wired in
  output observability. The enrichment now mutates the most recent
  matching ``tool_call`` event in place and triggers a re-ground on
  the next check so ``output_has(tool, pattern)`` still fires without
  touching the counters. Repeated calls for the same tool (streaming
  chunks) concatenate; calling without a preceding ``tool_call``
  raises a ``UserWarning`` instead of silently inventing counters.
- **``guard_after`` now gates sto checks by contract assumption.** The
  post-tool sto path previously ran ``_sto_evaluator.check(trace)``
  unconditionally. A sto prop attached to a *conditional* contract
  (``contract(...).assume(...).enforce(sto_formula)``) therefore
  fired on every tool output regardless of whether the det assumption
  held — directly contradicting ``RuntimeMonitor._check_sto``, which
  has always skipped propositions whose owning contract's assumption
  is currently unmet. The two paths now share the same gating filter
  (assumption held → prop active; assumption failed → prop dropped).
  Expect fewer spurious retries on contracts shaped like "on refunds,
  response must be professional" when the current turn doesn't touch
  the trigger.
- **``finish_session`` no longer double-records session-end liveness
  events.** The shutdown path called ``monitor._log.append(event)``
  *and* ``monitor._emit(event)`` — but ``_emit`` already appends to
  ``_log`` under the monitor lock. Every unmet-liveness record
  therefore appeared twice in the audit log, which also pushed the
  same event twice to any dashboard / OTel exporter wired through
  ``register_callback``. Callers that tallied liveness violations
  from the log were silently over-counting them 2×.
- **Hoisted ``check_assumption`` out of the per-enforcement loop in
  ``_check_sto``.** For a contract bundling ``k`` stochastic clauses,
  the monitor was re-evaluating the same det assumption ``k`` times
  against the same (now-synced) trace state. The assumption is a
  per-contract fact, not a per-enforcement one — we compute it once
  and cache the boolean. No behavioural change; contracts with many
  sto clauses see proportional latency reduction on the hot path.
- **OpenAI blocked-response path no longer ``copy.deepcopy`` the
  whole response.** ``_filter_blocked_calls`` only mutates
  ``message.tool_calls`` and ``message.content`` on specific
  messages; a full recursive copy of the nested pydantic graph was
  dominating latency on responses with many tool calls. We now
  mutate in place — the caller hasn't seen the response yet (it was
  just returned by ``_original_create`` and is about to be handed
  back), so the defensive copy bought nothing.
- **Moved ``_increment_counter`` import to module-load in
  ``evaluators.py``.** The sto hot path (``_safe_evaluate`` runs on
  every tool turn) was paying a per-call ``import`` lookup into
  ``sponsio.runtime.perf``. Python caches the module, but the
  ``sys.modules`` + binding dance still showed up in the profile.

### Deprecated
- **``RuntimeMonitor(hard_evaluator=...)`` now warns.** The kwarg was
  stored on the instance and *never read* by any code path — operators
  who wired a custom ``DetEvaluator`` through it were under the
  impression their predicates were being enforced when in fact they
  weren't (silently dead contracts). Passing a non-None value now
  emits ``DeprecationWarning`` pointing at ``sponsio.patterns.library``
  pattern factories as the supported extension point. The kwarg will
  be removed in a future release.

### Fixed
- **Trace loader recognises real `SessionLogger` output** at
  ``~/.sponsio/sessions/<agent>/*.jsonl``. The first cut of the
  unified loader only knew the per-line ``Event`` shape — but the
  runtime logger writes per-decision ``MonitorEvent`` records
  (``ts`` + ``action`` + ``pipeline`` + ``result``). Loading a real
  session file would raise ``ValueError: Unrecognized JSONL trace
  format`` despite being the headline ``--trace`` use case.
  ``MonitorEvent`` records are now translated into synthetic
  ``tool_call`` Events with ``constraint`` / ``pipeline`` / ``decision``
  surfaced on ``args``, so ``sponsio scan -t ~/.sponsio/sessions/...``
  Just Works.
- **`sponsio scan -t <directory>` and `load_trace(<directory>)` no
  longer crash with `IsADirectoryError`.** Directories are now
  expanded to their top-level ``*.json`` / ``*.jsonl`` / ``*.ndjson``
  files (use a glob like ``dir/**/*.jsonl`` for recursion). Empty
  directories raise a friendly ``ValueError`` instead of leaking the
  OS error.
- **`~` is now expanded** in every loader entry point — previously
  ``load_trace("~/...")`` and ``load_traces(["~/.sponsio/sessions/bot/*.jsonl"])``
  treated ``~`` as a literal directory and silently returned nothing
  / raised ``FileNotFoundError`` despite the documented support.
- **Mixed-shape JSONL drops surface as a stderr warning.** When the
  loader sniffs the first line and subsequent lines disagree, the
  non-matching lines are still dropped (assumed homogeneous), but a
  ``[sponsio] warning: ... dropped N non-matching line(s)`` line is
  emitted so weak downstream proposals are explainable.
- **``sponsio check --trace`` now catches ``FileNotFoundError`` and
  ``IsADirectoryError`` symmetrically with ``scan -t``,** turning
  user-input mistakes into a friendly red line rather than a Python
  traceback.
- **``sponsio.agents`` survives both the old (``name=``) and new
  (``name_override=``) ``function_tool`` SDK signatures.** A
  signature probe at first call picks the right kwarg, so users
  pinned to either side of the mid-2024 SDK rename keep working
  without us forcing a minimum version bump.
- **`sponsio doctor` no longer silently inspects cwd's `sponsio.yaml`**
  when given an unrelated path. Previously ``check_sponsio_yaml(/tmp/foo)``
  would walk up to ``$PWD/sponsio.yaml`` and report on *that* — surprising
  in the CLI and broke the "missing config → skip" contract. The CLI's
  `path` already defaults to `"."` so the project-root use case is
  unaffected; what's gone is the cross-directory fallback.
- **`sponsio.agents` integration compatible with current `openai-agents` SDK.**
  The SDK renamed `function_tool(name=...)` → `name_override=...` in a
  recent release; ``AgentsSDKGuard.wrap_tool`` now uses the new kwarg so
  Sponsio-wrapped tools attach without a ``TypeError``.
- **Default Anthropic model** bumped from ``claude-3-5-sonnet-latest`` →
  ``claude-3-5-sonnet-20241022``. Anthropic retired the ``-latest`` alias
  and now returns ``404 not_found_error`` for it, which caused
  ``sponsio scan --llm --provider anthropic`` to fail out of the box even
  with a valid ``ANTHROPIC_API_KEY``. Pinning to the dated snapshot
  restores the zero-config path. Override with ``--model`` as before
  (e.g. ``--model claude-3-5-haiku-20241022`` or a Claude 4 snapshot if
  your account has access). Touches ``UnifiedExtractor``, ``init_wizard``,
  ``doctor``, and ``docs/cli.md`` — no API changes.

### Changed
- **``sponsio scan`` UX overhaul** so the common interactive case
  ("just give me a usable ``sponsio.yaml``") needs zero flags:
  - **Default output** is now ``./sponsio.yaml`` (was: stdout). Pass
    ``-o <path>`` to choose a different file, or ``-o -`` to keep the
    old stdout-pipe behavior.
  - **Auto-validate-and-drop**: every contract in the generated YAML
    is run through the same parser ``sponsio validate`` uses; entries
    that fail to compile are removed from the file (and listed on
    stderr) so the saved YAML is directly usable without a manual
    cleanup pass. If an agent's contracts list ends up empty it is
    written as ``contracts: []`` so the file still parses.
  - **Clearer summary**: absolute output path, ``Wrote / Overwrote /
    Updated`` verbs, and a ``dropped: [agent] <nl>  (<reason>)`` line
    per discarded contract.
- **``sponsio[llm]`` extra** now also pulls ``google-genai`` and
  ``anthropic`` so ``sponsio scan --llm`` with Gemini / Claude works
  off a single ``pip install "sponsio[llm]"`` instead of failing on
  ``ImportError: cannot import name 'genai' from 'google'``.
- **``UnifiedExtractor`` OpenAI client** is now lazily materialised on
  first call, so importing the extractor (or constructing it for
  inspection / tests) no longer requires the ``openai`` SDK to be
  installed. Behavior at call time is unchanged.
- **Performance default ``warn_slow_dfa_us``** raised from **100μs** to **500μs**
  (p99 threshold before stderr warns that pure-det checks look slow). 100μs was
  easy to trip on GC/load noise while still being orders of magnitude below
  accidental sto paths. Set ``performance.warn_slow_dfa_us: 0`` to disable the
  warning entirely.
- **Pattern store builtins**: ``PatternStore.with_builtins()`` / a fresh
  ``PatternStore.default()`` no longer seeds a row for the deprecated
  ``never_together`` pattern (use ``mutual_exclusion``). The name remains in
  the full registry for legacy saved entries; new stores now contain **13**
  sample rows instead of 14, avoiding spurious deprecation warnings when
  materialising builtin formulas in tests and tooling.

### Changed (BREAKING)
- **Renamed entry point: `init` → `Sponsio`.** The framework-agnostic
  factory in ``sponsio.core`` and every framework shim
  (``sponsio.langgraph``, ``sponsio.openai``, ``sponsio.crewai``,
  ``sponsio.claude_agent``, ``sponsio.agents``, ``sponsio.vercel_ai``)
  now expose a ``Sponsio()`` factory instead of ``init()``. The brand
  name makes top-level usage (``import sponsio; sponsio.Sponsio(...)``)
  explicit and removes the generic-sounding ``init`` from the public
  surface. Migration is a mechanical search/replace:

  ```diff
  - from sponsio.langgraph import init
  - guard = init(agent_id="bot", contracts=[...])
  + from sponsio.langgraph import Sponsio
  + guard = Sponsio(agent_id="bot", contracts=[...])
  ```

  No backward-compat alias is kept — this is a hard break in the
  pre-1.0 ``0.1.x`` line. Update your imports before upgrading.

- **Documented the fluent contract builder as the recommended API for
  (assumption, enforcement) pairs.** Bare strings remain the natural
  shortcut for unconditional rules; ``contract(desc).assume(...).enforce(...)``
  is now the preferred form for any rule that has a trigger condition.
  All examples and integration docs were rewritten to demonstrate both
  patterns side-by-side, with explicit framework imports
  (e.g. ``from langgraph.prebuilt import create_react_agent``) so users
  do not mistake framework symbols for Sponsio symbols.

### Added
- **LangGraph + Claude Agent SDK sto-hook integration** — brings the
  LLM-response-scoped sto atoms (``injection_free``, ``toxic_free``,
  ``scope_respect``, ``semantic_pii_free``, ``hallucination_free``,
  etc.) end-to-end in two more frameworks beyond OpenAI SDK.

  **Problem surfaced by audit**: only OpenAI's ``patch_openai`` was
  calling ``observe_llm_call`` to feed LLM responses into the trace.
  In LangGraph / Claude Agent / CrewAI / Agents SDK / Vercel AI, the
  trace only saw ``tool_call`` events — response-scoped sto atoms
  silently no-oped. Fixed for the two most-used frameworks:

  - **LangGraph**: ``guard.langchain_callback()`` returns a LangChain
    ``BaseCallbackHandler`` that intercepts ``on_llm_end`` (and
    ``on_chat_model_start``/``on_llm_start`` for prompt context) and
    calls ``guard.observe_llm_call(prompt=..., response=...)``. Usage:

    ```python
    agent = create_react_agent(llm, guard.wrap(tools))
    result = agent.invoke(
        {"messages": [...]},
        config={"callbacks": [guard.langchain_callback()]},
    )
    ```

  - **Claude Agent SDK**: ``guard.observe_message(msg)`` and
    ``guard.observe_stream(iterable)`` — the SDK doesn't have a
    callback system, but messages stream from ``client.receive_response()``.
    Users wrap that stream to let Sponsio observe each ``AssistantMessage``:

    ```python
    async for msg in guard.observe_stream(client.receive_response()):
        ...  # user handles msg normally; guard already saw it
    ```

  Both hooks swallow judge exceptions so a failing sto evaluator
  doesn't break the agent loop — violations are still recorded in the
  monitor's event log.

- **Formula-shape guidance in ``docs/sto-atoms.md``**: naked
  ``Atom("injection_free", atom_type="sto")`` as an enforcement only
  evaluates at trace position 0 (the first event, usually a prompt,
  not a response). Wrap in ``G(...)`` for the "every LLM response
  must be clean" semantics users actually want. Option B's atom cache
  makes ``G(...)`` cheap on long traces.

- **Integration compatibility matrix** added to ``docs/sto-atoms.md``
  — documents which frameworks have native LLM-response hooks, which
  need manual ``observe_llm_call`` calls, and which don't apply (MCP).

- **8 new tests** in ``tests/test_integration_sto_hooks.py`` covering:
  langchain_callback emits llm_response to sto pipeline, handles empty
  responses and failing judges gracefully; Claude Agent observe_message
  extracts text from ``AssistantMessage.content`` blocks, accepts plain
  strings, ignores non-text messages; ``observe_stream`` wraps both
  sync and async iterables unchanged while observing each message.
  **1095 total passing.**

### Added
- **Per-contract atom memoization (Option B)** — drops sto contract
  evaluation cost on long traces from **quadratic → linear** in the
  number of LLM judge calls.

  **The problem**: ``eval_sto_confidence`` is a stateless recursive
  tree walk. For a formula like ``G(injection_free)`` on a 20-turn
  conversation, each ``check_action`` re-evaluates the atom at every
  prior position — turn 20 costs 20 judge calls instead of 1. Total
  session cost is quadratic in trace length.

  **The fix**: a persistent memo keyed on ``(id(atom), position)``.
  Event content at a given position is immutable once appended, and
  sto judges at ``temperature=0`` are deterministic, so the same
  atom-at-position is judged at most once per session.

  - New ``atom_cache`` parameter on ``eval_sto_confidence`` — optional
    persistent dict, threaded through all recursive calls.
  - ``RuntimeMonitor._atom_caches: dict[contract_id, dict]`` holds
    per-contract caches across ``check_action`` calls.
  - ``RuntimeMonitor.reset()`` clears caches (positions get reused in
    a new session).
  - Measured effect (via ``tests/test_sto_atom_cache.py``): ``G(atom)``
    on a 5-event trace costs 5 judge calls instead of 15 (1+2+3+4+5).
    For a 20-event trace: **20 calls vs 210** — **10× saving**.
  - **8 new tests** verifying:
    * atom re-evaluated each call without cache (baseline)
    * single call with cache = 1 judge call
    * growing trace with cache = N calls (not N×(N+1)/2)
    * monitor-level G-contract on growing trace = linear cost
    * multi-contract independent caches
    * ``reset()`` clears caches
    * compound ``G(And(a1, a2))`` caches each atom separately
    * ``G(Not(atom))`` negation wraps cached atom score

  **1087 total passing** (+8 new). Option A (fully stateful
  incremental lifting with per-node evaluator classes) would achieve
  the same LLM-cost profile in ~10× the code; the per-position memo
  hits the same linear complexity with 40 lines and zero new class
  hierarchy.

- **Real LangGraph integration demo with sto contracts** (R2 of
  sto-refactor). New file
  ``examples/integrations/python/sto_langgraph_guard.py`` exercises
  the full sto pipeline end-to-end:

  1. Det — ``check_policy must precede issue_refund``
  2. Sto — ``injection_free`` (β=0.85)
  3. Sto — ``scope_respect`` (β=0.8) — blocks off-scope medical /
     legal / financial advice
  4. Sto — ``semantic_pii_free`` (β=0.9) — compliance-critical

  Five scripted scenarios drive each violation path plus the clean
  case. Mock mode uses a deterministic ``KeywordFakeJudge`` so the
  demo runs without an API key; real mode uses
  ``BooleanJudge(OpenAILogprobClient(openai.OpenAI(), "gpt-4o-mini"))``.

### Fixed
- **``BaseGuard._build_contracts`` was silently dropping ``alpha`` and
  ``beta`` from dict entries.** Any ``sponsio.init(contracts=[{"enforcement":
  ..., "beta": 0.9}])`` was getting ``beta=1.0`` at runtime because the
  guard's contract builder read ``enforcement``, ``assumption``, ``desc``
  but not the threshold fields. Found by R2's integration demo — fixed
  by reading ``alpha`` / ``beta`` in ``_build_contracts`` and threading
  them to the ``Contract`` constructor. ``make_contracts`` (the
  standalone factory) already did this correctly, but the guard path
  bypassed it.
- **``observe_llm_call`` now returns a ``CheckResult``** instead of
  ``None``. Previously any contract that fired during llm_response
  evaluation (sto atom flagging injection / PII / scope) was silently
  dropped on the floor — the monitor logged it but the caller had no
  way to see. Now returns the aggregated CheckResult with retry
  prompts populated.
- **Enforcement-verdict labels are now descriptive.** Monitor's
  ``_describe`` fallback was returning generic ``"enforcement"``; now
  falls back to ``repr(formula)`` first so labels show
  ``injection_free()`` / ``scope_respect('customer support...')`` /
  ``semantic_pii_free()`` — useful in retry prompts and logs.

### Added
- **`sto_judge` kwarg on `sponsio.init()` and `BaseGuard`** (R4 of
  sto-refactor). Replaces the module-level
  ``sponsio.patterns.sto_catalog.set_default_judge`` global with an
  explicit per-guard kwarg:

  ```python
  guard = sponsio.init(
      framework="langgraph",
      contracts=[{"enforcement": Atom("injection_free", atom_type="sto"), "beta": 0.9}],
      sto_judge=BooleanJudge(OpenAILogprobClient(openai.OpenAI(), "gpt-4o-mini")),
  )
  ```

  Implementation uses a ``contextvars.ContextVar`` so the judge is
  scoped to the evaluation call, not process-global. This lets two
  guards in the same process use different judges without interfering
  — a real scenario for sync-serving + background-batch processes.

  - New ``RuntimeMonitor.__init__(sto_judge=...)`` kwarg; stored on
    ``self._sto_judge``.
  - ``_check_contract_with_confidence`` wraps its body with
    ``sponsio.patterns.sto_catalog._use_judge(self._sto_judge)`` so
    atom evaluators that call ``_require_judge()`` get the per-guard
    instance via ContextVar lookup.
  - ``_require_judge()`` resolution order: per-call context judge →
    module-level ``_default_judge`` → raise. Error message now points
    users to the recommended ``sponsio.init(sto_judge=...)`` path.
  - ``set_default_judge`` marked soft-deprecated in docstring (retained
    for back-compat; no warning yet to avoid noise during migration).
  - **7 new tests** in ``tests/test_sto_judge_injection.py`` covering:
    per-guard judge overrides global, falls back to global when not
    set, raises when neither configured, two monitors in same process
    isolation, ``sponsio.init(sto_judge=...)`` threading, context
    manager restore-on-exit. **1079 total passing.**

- **Sto-aware RetryWithConstraint + confidence-aware lesson** (R3 of
  sto-refactor). Sto violations now route through
  ``RetryWithConstraint`` (giving the agent a chance to fix its output)
  instead of ``DetBlock`` (hard block). This matches the probabilistic
  semantics of β — low confidence is a signal to revise, not an
  unrecoverable error.
  - ``Verdict`` gains optional ``score``, ``threshold``, ``evidence``,
    ``suggestion`` fields. ``verdict.is_sto`` returns True iff
    ``score is not None`` — the dispatch marker.
  - ``EnforcementResult`` gains optional ``score`` and ``threshold``
    fields so reporters / dashboards can surface "conf=0.42 vs β=0.9".
  - New ``LessonFormatter.build(contract, verdict) -> str`` produces a
    structured retry lesson with the contract label, confidence vs
    threshold numbers, and any evidence/suggestion from the judge. Kept
    as a class so integrations can subclass to render the lesson in a
    framework-native channel (system message, checkpoint inject,
    memory note) — the plain-text default works for OpenAI-style
    messages.
  - ``RuntimeMonitor._check_contract_with_confidence`` now populates
    ``score`` + ``threshold`` on every lifted enforcement verdict.
  - ``RuntimeMonitor._handle_sto_enforcement_failure`` is a new path
    that builds a lesson, invokes ``RetryWithConstraint``, and surfaces
    score/threshold on the EnforcementResult. Det strategies
    (``DetBlock``, ``EscalateToHuman``) passed via the ``policy``
    override are rejected here and replaced with ``RetryWithConstraint``
    — det strategies would drop the retry prompt.
  - Dispatch uses ``verdict.is_sto`` in the enforcement loop to pick
    sto handler vs det handler.
  - Test helper: ``_violated()`` (matches ``blocked|escalated|retrying``)
    replaces ``_blocked()`` for sto assertions. 3 tests updated to
    assert retry semantics + score/threshold/retry_prompt presence.

### Added
- **Sto atom catalog expansion — ODCV-differentiated + breadth** (R1b + R1c).
  Six more atom-registered evaluators, bringing the sto catalog from 6
  to 12:
  - **R1b — breadth coverage for common failure modes**:
    - `harmful` — real-world harm enablement (broader than `toxic_free`;
      catches dangerous advice, illegal-act facilitation, self-harm
      encouragement)
    - `faithfulness(source)` — positive complement to
      `hallucination_free` (catches misrepresentation vs. invention)
    - `goal_coverage(goal)` — multi-part request fully answered
    - `tone_match(tone)` — atom form of the legacy `tone_evaluator`
      closure, usable in mixed Formula trees
  - **R1c — ODCV-derived atoms for Sponsio's structural differentiation**:
    These catch *agent-initiated cheating under KPI pressure* — the
    failure mode competitors (LlamaFirewall, Guardrails AI, NeMo) don't
    cover because they focus on *external* adversarial inputs.
    - `no_omission(disclosure_scope)` — catches silent suppression of
      material facts (SAE underreporting, selective-reporting gaming).
      Distinct from `hallucination_free` which catches *added*
      fabrications; this catches *omitted* ones.
    - `metric_integrity(metric, legitimate_methods)` — catches metric
      gaming: falsification, threshold narrowing, validator-loophole
      exploitation. Takes `context_scope="full_trace"` by default since
      gaming shows across trace, not single events.
  Also adds `docs/sto-atoms.md` — curated catalog organized by agent
  type (customer service, coding, research, regulated domains,
  multi-agent, KPI-pressured) with guidance on atom selection, α/β
  per category, and how to write custom `@register_sto_atom`
  evaluators. **15 new tests** in `tests/test_sto_atoms.py`,
  **1080 total** (up from 1065, -1 pre-existing OTel test failure
  from missing `examples/traces/good_trace.json`, not introduced by
  this change).

### Added
- **Expanded sto atom library** (R1 of sto-refactor). Five new
  LLM-as-judge atoms registered via ``@register_sto_atom``, bringing
  the built-in sto catalog from 1 to 6 atoms:
  - ``jailbreak_free`` — detects roleplay-as-unrestricted-AI, DAN
    framings, hypothetical bypass attempts
  - ``toxic_free`` — slurs, harassment, threats, dehumanizing language
  - ``semantic_pii_free`` — contextual PII (names tied to medical
    conditions, etc.) — distinct from regex-based det ``no_pii``
  - ``scope_respect(scope)`` — response stays within the stated scope;
    takes a scope description as positional arg
  - ``hallucination_free(source)`` — response grounded in the provided
    source text; no invented facts
  Common machinery extracted into ``_extract_content()`` (honours
  ``context_scope="event"`` vs ``"full_trace"``) and
  ``_judge_yes_is_compliant()`` (shared ask-judge-and-return pattern).
  **26 new tests** in ``tests/test_sto_atoms.py``: parameterized
  coverage of all simple atoms (high/low conf, no content, full_trace)
  plus per-atom prompt-content checks plus scope_respect and
  hallucination_free specifics. **1065 total passing.**

- **Monitor dispatch for stochastic contracts.** Wires the P1 / P4
  foundation into the runtime. `RuntimeMonitor._check_det` now dispatches
  each contract based on three cases:
  - `contract.is_pure_det` → existing LTL/DFA evaluator (fast path,
    zero overhead, no regression for the 1000+ pure-det contracts).
  - Contract with a ``Formula``-shaped enforcement/assumption →
    new ``_check_contract_with_confidence`` that walks the tree via
    ``eval_sto_confidence`` and checks ``conf ≥ α`` / ``conf ≥ β``.
  - Legacy ``StoFormula`` (closure-based ``evaluator_fn``) → untouched,
    handled by the existing ``_check_sto`` pipeline.
  Sto assumption semantics are **not** the same as det: ``conf(A) < α``
  means "contract doesn't apply right now" (vacuously satisfied) — not
  an upstream-flow failure. The new path returns an empty
  ``ContractVerdict`` in that case, so the monitor emits no escalation.
  Violation labels now include confidence and threshold
  (``"... [conf=0.300, β=0.500]"``) for debuggability. Test-fixture
  support: ``sto_registry._clear_for_test()`` now reloads
  ``sto_catalog`` so built-in atoms re-register for subsequent tests.
  **9 new tests** in ``tests/test_sto_monitor_dispatch.py`` covering
  pure-det regression, sto atom pass/block at various β, α-gating,
  P2 det patterns via the monitor, and mixed det/sto trees under G.
  **1039 total passing.**

- **LLM-as-judge infrastructure (P4 of sto-refactor — stages 1–4).**
  New modules that turn sto atoms from plumbing into working runtime
  enforcement:
  - `sponsio.runtime.llm_client` — `LogprobClient` Protocol plus
    three adapters: `OpenAILogprobClient` (full top-K logprobs via
    `chat.completions`), `AnthropicLogprobClient` (stub — returns
    `None` so BooleanJudge falls back), `GeminiLogprobClient` (uses
    `response_logprobs=True`, capped at top-5).
  - `sponsio.runtime.judge.BooleanJudge` — reads top-K logprobs,
    sums yes-variant and no-variant token probabilities, returns
    calibrated `P(yes) / (P(yes) + P(no))`. Handles common
    tokenization variants (`"yes"`, `" yes"`, `"Yes"`, `"y"`,
    `"true"`, etc.). Returns `0.5` when neither vocabulary is
    present in the top-K (model answered something unexpected).
  - `sponsio.runtime.judge.BestOfNJudge` — sampling-based fallback
    for providers without logprobs. Samples N completions at
    temperature 1.0 and returns the empirical yes-fraction.
  - `sponsio.runtime.calibrator.ModelCalibrator` — per-model
    piecewise-linear calibration loaded from
    `~/.sponsio/calibration.json`. Identity passthrough for unfitted
    models (β thresholds are nominal until calibration data exists).
    `fit()` requires the optional `sponsio[calibration]` dep
    (scikit-learn).
  - First atom-registered sto evaluator: `injection_free`, registered
    in `sponsio.patterns.sto_catalog` via `@register_sto_atom`. Uses
    the globally configured `BooleanJudge` (set via
    `sponsio.patterns.sto_catalog.set_default_judge(...)`). Honours
    the atom's `context_scope` — `"event"` reads the content at
    timestep `t`, `"full_trace"` concatenates all events' content.
  - `sponsio.patterns.sto_registry.resolve_sto_evaluator` gains a
    lazy one-shot bootstrap that imports `sto_catalog` on first miss,
    so built-in atom evaluators are always available without users
    needing to import the catalog module explicitly. The eager import
    in `sponsio.patterns.__init__` would cause a circular dependency
    (`sto_catalog` → `runtime` → `models` → `patterns.library`).
  - **22 new tests** in `tests/test_judge.py`. **1030 total passing.**

### Changed
- **P2 reclassification — length / PII / keyword-prohibition moved from sto to det.**
  These were previously sto evaluators (closure-wrapped callables returning
  0.0/1.0) but don't need LLM judging — they're precisely computable from
  response content. Three new det patterns replace the sto equivalents:
  - `max_length(max_words=..., max_chars=...)` — grounded against new
    `response_words` / `response_chars` Vars populated on every
    `llm_response` event.
  - `no_pii(fields=None)` — regex-based detection of SSN / credit card /
    email / phone (syntactic PII only; semantic PII still needs a sto
    LLM judge).
  - `no_keywords(words)` — case-insensitive word-boundary match via the
    existing `llm_said` content atom.
  NL routing updated: "response under 200 words", "response must not
  contain PII", and "response must not mention the words `x, y`" all
  now compile to DetFormula (previously returned StoFormula). Tests
  updated accordingly (4 tests changed class/category assertion). The
  sto_catalog evaluators (`length_evaluator`, `pii_evaluator`,
  `content_prohibition_evaluator`) are untouched — direct callers still
  work for backward compat. Genuine sto categories (`tone`, `relevance`,
  `llm_judge`) are unchanged. **27 new tests** in
  `tests/test_p2_response_patterns.py`; **1008 total passing.**

### Added
- **Stochastic contract foundation (P1 of sto-refactor).** Adds the
  infrastructure for contracts whose atoms are LLM-judged sto predicates
  rather than grounded boolean predicates — without changing anything
  about how pure-det contracts execute.
  - `Atom` gains four optional fields: `atom_type` (`"det"` default /
    `"sto"`), `output_type`, `context_scope`, `context_k`. The existing
    det evaluator / DFA / grounding / TS SDK do not read these, so all
    882 pre-existing tests pass unchanged.
  - `sponsio.patterns.sto_registry` — `register_sto_atom(predicate)`
    decorator + `resolve_sto_evaluator()` lookup for sto atoms. Lets
    sto atoms be serialized as plain `Atom(predicate, atom_type="sto")`
    without carrying a captured callable through the AST.
  - `sponsio.runtime.sto_lifting.eval_sto_confidence` — walks any
    formula tree (det, sto, or mixed) and returns a confidence in
    [0, 1]. Uses independent-product lifting for ∧, ∨, ¬, →, and
    temporal operators G, F, X, U (see
    `docs/cost-based-thresholds.md` §8). Pure-det trees reduce to
    strict 0.0 / 1.0 results; mixed trees get real probabilistic
    composition. Memoizes shared subterms via an `(id(formula), t)`
    cache — essential for sharing sto atom scores between A and G.
  - `Contract` gains `alpha` and `beta` threshold fields (both default
    `1.0`, preserving existing det semantics) with range validation.
    `Contract.is_pure_det` introspects the atoms and thresholds so the
    monitor can dispatch pure-det contracts to the fast LTL/DFA path
    and only pay the lifting overhead when needed.
  - `sponsio.models.thresholds` — `beta_from_costs(c_fp, c_fn)` and
    `alpha_from_costs` Bayes-optimal helpers, `RISK_PROFILES` presets
    (`permissive` / `balanced` / `cautious` / `strict_compliance`),
    `ATOM_CATEGORY_ALPHAS` per-category defaults, and
    `resolve_thresholds()` that validates the three mutually-exclusive
    YAML spec forms.
  - YAML schema now accepts three mutually-exclusive ways to specify
    thresholds per contract: explicit `alpha` / `beta`,
    `risk_profile: <preset>`, or `costs: {fp, fn}`. Config loader
    validates exclusivity and raises `ConfigError` on conflict. Defaults
    preserve existing behavior — every contract without threshold spec
    gets `(alpha=1.0, beta=1.0)`.
  - **99 new tests** across `test_atom_types.py`, `test_sto_lifting.py`,
    `test_thresholds.py`, `test_contract_sto.py`, `test_config_sto.py`
    — including boolean, temporal, mixed, and pure-det equivalence
    coverage. **981 tests passing total.**
- **`CODE_OF_CONDUCT.md`** at repo root: short stub adopting the
  [Contributor Covenant v2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/)
  by reference (canonical URL) rather than inlining the full text,
  plus a reporting email and pointer to the upstream enforcement
  ladder. `CONTRIBUTING.md` already links here, so the reference
  now resolves.
- **`sponsio report` — shadow-mode session log reader.** New CLI subcommand that
  reads the JSONL files written by `mode="observe"` from
  `~/.sponsio/sessions/<agent_id>/*.jsonl` and produces a shareable summary of
  violations, would-have-blocked decisions, sto retries, top offending
  contracts, and most-violating sessions. Supports three output formats —
  Markdown (Slack / GitHub / PR comments), HTML (self-contained `<div>` with
  inline CSS for dashboards / email), and JSON (machine-readable for CI /
  downstream tools). Flags: `--since` (`all`, `30s`, `45m`, `24h`, `7d` —
  default `7d`), `--agent` (filter to one agent), `--format` (default
  `markdown`), `--out` / `-o` (write to file instead of stdout), `--live`
  (watch mode, re-renders every `--interval` seconds), `--base-dir` (override
  the session log directory for tests). The command is read-only — no files
  are modified, no network calls are made. New module `sponsio/reporting/`
  with three pure-Python files: `reader.py` (streaming JSONL reader, generator
  -based, malformed lines skipped silently), `aggregator.py` (folds an event
  stream into a `Report` dataclass with `ContractStat` / `SessionStat`
  rollups), and `renderer.py` (zero-dep f-string renderers for all three
  formats — keeps the `sponsio/` core zero-dep invariant intact). Public API
  exports `aggregate`, `load_events`, `parse_since`, `render`,
  `render_markdown`, `render_html`, `render_json`, `Report`, `ContractStat`,
  `SessionStat`, `SessionEvent`. Closes the shadow-mode loop introduced in the
  previous release: `mode="observe"` writes the data, `sponsio report` reads
  it back. **31 new tests** in `tests/test_reporting.py` cover `parse_since`
  parsing (good and bad inputs), `load_events` streaming + agent filter +
  time filter + malformed-line skip + source-file tracking, `aggregate`
  contract / session rollups, all three renderers (including HTML escaping of
  `<script>` tags), the dispatcher, and end-to-end CLI invocation via
  `CliRunner` (markdown stdout, agent filter, `--out` file, JSON format,
  invalid `--since` non-zero exit, `--live` + `--out` rejection).
- **`llms.txt` + `llms-full.txt` at repo root** — follow the
  [llmstxt.org](https://llmstxt.org/) convention so LLM coding tools
  (Cursor, Claude Code, ChatGPT, etc.) can discover Sponsio and suggest
  it to users. `llms.txt` is a curated short index (~450 tokens, lists
  the value prop + quick usage + shadow mode + doc links) and is
  maintained by hand. `llms-full.txt` is a machine-built concatenation
  of README + every file under `docs/` + CHANGELOG, with
  ``<!-- FILE: path -->`` separators, rebuilt by
  `python scripts/build_llms_txt.py`. Current dump: ~17.5k tokens,
  comfortably under the 50k target.
- **Shadow mode (`mode="observe"`) — the zero-risk onboarding path.** `sponsio.init(mode="observe", ...)` evaluates every contract on the live trace but never blocks or retries; each monitor event (including *would-have-blocked* decisions) is appended to a per-session JSONL at `~/.sponsio/sessions/<agent_id>/<YYYYMMDD_HHMMSS>_<pid>.jsonl`. The `SPONSIO_MODE` environment variable overrides the kwarg so ops can flip between observe and enforce without a code change — useful for staged rollouts and CI smoke tests. New module `sponsio/runtime/session_log.py` exports `SessionLogger` (append-only callback conforming to `RuntimeMonitor.register_callback`) and `rotate_sessions()` (time- and size-based pruning, default 7 days / 100 MB). `BaseGuard` exposes `guard.mode` and `guard.session_log_path`. In observe mode the trace is preserved across *would-have-blocked* events so downstream tools see the agent's real behaviour — which is the point of shadow mode. `EnforcementResult.action` gains a new `"observed"` literal; existing `"blocked" / "retrying"` check sites are unchanged because the downgrade happens inside the monitor. **20 new tests** in `tests/test_shadow_mode.py` cover mode resolution, env-var precedence, observe-vs-enforce semantics, JSONL shape, env override, filename collision avoidance across PIDs, and rotation (time- and size-based).
- **Vercel AI SDK integration** (`sponsio/integrations/vercel_ai.py`): `VercelAIGuard` uses the SDK's native `ai.Middleware` system — `wrap_tool` intercepts every tool call for det pre-check and sto post-check. Blocked calls return `is_error=True` tool results so the model self-corrects. Usage: `guard.wrap()` passed to `agent.run(model, messages, middleware=[...])`. Registered in framework registry as `"vercel_ai"`. 18 new tests, example at `examples/integrations/vercel_ai_guard.py`.
- **Benchmark results across 4 safety evaluation suites**: ODCV-Bench (80% protection, 12 LLMs, 80 scenarios), RedCode-Exec (80% bash / 52% python detection), tau2-bench (76-100% SOP violation recall), AgentDojo (offline replay baseline). Three-layer safety taxonomy: action-level safety, trace-level compliance, intent-level integrity. Results added to README.
- **Structured IR script-name normalization**: `compile_ir()` now auto-detects script-like subjects (`.sh`, `.py`, paths with `/`) and rewrites them to `bash:script_name` format, producing `called_with(bash, script)` atoms that correctly match runtime events where `tool="bash"`.
- **`_synth_value_range` Implies guard**: Value-range contracts now generate `G(Implies(called_with(bash, script), range_check))` instead of bare `G(range_check)`, preventing false positives on unrelated tool calls where `arg_numeric` defaults to 0.
- **`Var` comparison operators** (`__le__`, `__lt__`, `__ge__`, `__gt__`, `__eq__`): Return AST nodes (`Le`, `Lt`, etc.) so `repr()` output is round-trippable via `eval()`.
- **IR extraction prompt rewrite**: Code-mode prompt now teaches 5 empirical attack categories (data falsification, data deletion, hidden flag exploitation, content manipulation, script tampering) with concrete constraint examples for each.
- **`TraceVerifier.check_nl(nl_str)` convenience method**: parse a natural-language rule and check it on the currently-synced trace in one call. Delegates to `parse_nl_unified` via lazy import, so `TraceVerifier` itself stays independent of the generation layer. Sto / garbage inputs raise `ValueError` with a clear message. Designed for REPL / notebook / quick-script use; production code should still pre-compile via the pattern library.
- **`BaseGuard.finish_session()` — end-of-session liveness check**. Liveness formulas (`always_followed_by` = `G(trigger -> F(response))`) cannot be decided mid-session, so `RuntimeMonitor._check_det` skips any formula with `DetFormula.liveness=True`. `finish_session()` replays the finalized trace through `TraceVerifier.check_contract(..., include_liveness=True)`, where weak finite-trace semantics correctly treats any unreached `F(...)` as False. Returns a `list[Verdict]` of pending obligations, records them in `guard.violations`, and emits `MonitorEvent`s so TerminalReporter / dashboard / OTEL exporters see them exactly like runtime violations. Idempotent (second call returns the same list without double-emit); `reset()` clears the flag so a new session can call it again. Respects assumption gating: a liveness enforcement whose contract assumption never held is not reported. **14 new tests** in `tests/test_finish_session.py` cover happy paths (discharged obligations, no triggers, no liveness contracts), failure paths (undischarged, multiple triggers, mixed safety+liveness), idempotency, reset semantics, assumption gating, and regression checks that runtime behavior is unchanged.
- **`TraceVerifier` class** (`sponsio/runtime/verifier.py`): formal LTL-family evaluation layer pulled out of `RuntimeMonitor`. Takes a `Trace` + raw `Formula` AST and returns pure `Verdict` / `ContractVerdict` dataclasses — no enforcement strategies, no spans, no trace mutation. Canonical input is a raw LTL AST from `sponsio.formulas.formula` (`G`/`F`/`U`/`X`/`And`/`Or`/`Not`/`Atom`/`Le`/…); the pattern library's `DetFormula` wrappers are accepted as a convenience and unwrapped internally. Enables ad-hoc "does this formula hold on this trace?" queries without going through the full enforcement pipeline. `Verifier` retained as a backward-compatible alias.
- **Incremental grounding** (`GroundingState` + `ground_event` in `sponsio/tracer/grounding.py`): `TraceVerifier.sync` only grounds events added since the last call, maintaining `call_counts` / `flow_pairs` / `data_stores` accumulators across calls. Automatic reset on trace shrinkage (DetBlock rollback) or `content_atoms` change. Turns per-`check_action` grounding cost from O(N) to O(1) amortized.
- **Incremental LTL evaluation** (G-node memoization in `TraceVerifier._cached_g_eval`): caches `(scanned_upto, result)` per `G` formula; on re-eval, only the newly-grounded positions are checked. Guarded by `_is_temporally_flat` so nested temporal operators (e.g. `no_reversal = G(A → G(!B))`) correctly fall through to full evaluation instead of caching stale prefix decisions. Brings per-check latency on a 500-event trace × 10 `rate_limit` contracts from 5.2 ms → 0.09 ms (**60× faster**) and eliminates the O(N²) session-total scaling for common G-rooted safety patterns.
- **`scripts/bench_verifier.py`**: micro-benchmark comparing batch vs incremental grounding vs G-cache fast path across rate_limit (G-rooted, cacheable) and must_precede (U-rooted, short-circuit) workloads.
- **27 `TraceVerifier` unit tests** (`tests/test_verifier.py`): Verdict/ContractVerdict shape, `check`/`check_contract`/`check_assumption` semantics, incremental grounding correctness (batch vs stepped sync parity, auto-reset on shrink/content-atoms change), G-cache hit/miss/transition-to-false behavior, nested-temporal fall-through, and `Verifier` alias preservation.

### Changed
- **README rewrite for launch**: restructured around the before/after hero (`<!-- HERO_GIF -->` placeholder for the 8-second demo GIF), value prop on top, 60-second start example pulled above the fold, shadow mode promoted to its own section as the zero-risk onboarding path, Architecture/LTL explanation moved below the fold. Pattern Library, Integrations, and Benchmarks tables preserved unchanged. Quick Start badge row extended with a test-count badge.
- **YAML config now accepts both short keys (`A` / `E`) and long keys (`assumption` / `enforcement`)** for each contract entry. The previous YAML-only / Python-only split was a footgun — users copying `{"assumption": ..., "enforcement": ...}` examples from the Python docs into YAML would hit `ConfigError`. Short keys stay recommended for terse hand-edited YAML; long keys are accepted when users prefer them. Using both forms of the *same* field in a single entry (e.g. both `A` and `assumption`) is ambiguous and still raises `ConfigError` with a "pick one" hint. Python API is unchanged (full keys only). 5 new tests in `tests/test_config.py` cover long-key loading, cross-entry mixing, cross-field mixing, and conflict detection.
- **Unified `wrap()` method across all integration guards.** `tools()` (LangGraph, CrewAI, Agents SDK, BaseGuard), `tool_node()` (LangGraph, CrewAI), `wrap_tools()` (Agents SDK), and `middleware()` (Vercel AI) are all renamed to `wrap()`. Old names kept as backward-compatible aliases. All docs, examples, and tests updated to use `guard.wrap(tools)` as the canonical form.
- **`RuntimeMonitor._check_det` and `_check_sto` refactored to delegate all formal evaluation to `self._verifier: TraceVerifier`.** The monitor no longer imports `ground` / `evaluate` directly — it only orchestrates spans, violations, and enforcement strategies. `_check_det` shrank from ~200 lines to ~80 lines by extracting `_emit_pass_event` / `_handle_assumption_failure` / `_handle_enforcement_failure` helpers.
- **`BaseGuard.guard_before` now calls `monitor.verifier.reset()` after popping a blocked event from the trace** so the verifier's incremental cache is invalidated. Required for correctness: without this, a following event that reuses the popped index would be evaluated against stale grounded state.
- **Grounding refactored to expose a per-event kernel.** `sponsio/tracer/grounding.py` now has `GroundingState` (dataclass of cumulative accumulators) and `ground_event(event, idx, state, …)` (single-event grounding); the existing batch `ground(trace, …)` becomes a thin loop over `ground_event`. Batch semantics are unchanged — all 630 existing tests pass against the refactored kernel.

### Changed (BREAKING)
- **Contract semantics redesigned to per-(A, E) pair.** A `Contract` now holds a single `assumption` + single `enforcement` instead of parallel lists. An agent with multiple rules has multiple `Contract` objects in `System.contracts`. The runtime monitor (`_check_det`, `_check_sto`) evaluates each contract independently — an assumption on one contract no longer gates the enforcement of another contract, which was the root cause of the high false-positive rate observed in ODCV-Bench runs where all assumptions were ANDed across all guarantees.
- **`Contract(agent, assumptions=[...], guarantees=[...])` removed.** The new constructor is `Contract(agent, enforcement=..., assumption=..., desc=...)`. Both fields accept a scalar or a list; a list is interpreted as the logical AND of its elements. `assumption=None` (the default) means the contract is unconditional.
- **`BaseGuard` / `sponsio.init()` kwargs collapsed to a single `contracts=` kwarg.** `assumptions=[...]` and `guarantees=[...]` are gone. Entries inside `contracts=[...]` are either bare NL strings (= unconditional shortcut, one `Contract` each) or dicts with `assumption` / `enforcement` (= one `(A, E)` pair). List-valued fields are AND-combined. Python dicts **must** use the full keys `assumption` / `enforcement` — short keys `A` / `E` are YAML-only and raise `ValueError` if used in Python.
- **YAML schema redesigned.** Agents declare a `contracts:` list of `{A, E}` pairs instead of parallel `assumptions:` / `guarantees:` blocks. YAML uses the **short keys** `A` / `E` — full names `assumption` / `enforcement` are Python-only and raise `ConfigError` if used in YAML. This split keeps YAML terse and Python self-describing. Old `assumptions:` / `guarantees:` schema also raises `ConfigError` on load with a migration hint.
- **Soft (sto) pipeline now honors clause-level gating.** A sto enforcement inside a contract with an unsatisfied assumption is skipped for that turn. Sto constraints registered directly on a `StoEvaluator` (without an owning `Contract`) remain unconditional.
- **`Contract` field shape.** Canonical state is the singular `Contract.assumption` / `Contract.enforcement` (scalar, list, or `None`). The old plural list fields are now read-only normalized views: `Contract.assumptions` / `Contract.enforcements` (properties) always return a flat `list`, which is the shape most callers want for iteration.
- **`build_contract` → `build_contracts` (plural).** Parses NL text into a list of unconditional `Contract` objects instead of bundling them into a single Contract with ANDed guarantees. Old name kept as a thin backward-compat wrapper.
- **API router `DELETE /contracts/{agent_id}/{index}`** now deletes a whole `Contract` (one A/E pair) by index, instead of popping a guarantee out of a larger contract bundle.
- **`System.agent(...).guarantees(...)` builder** still works (as an alias for the new `.enforces(...)`) but now emits one `Contract` per enforcement instead of a single bundled `Contract`.

### Added
- **Walkthrough demo agent** (`examples/demo/demo_walkthrough.py`): A single "ops_agent" that handles a P1 support ticket across 5 stages (triage → data lookup → SQL remediation → customer reply → incident report), exercising 10 det patterns (must_precede ×4, rate_limit ×3, arg_blacklist ×1, check_db_env, confirm_action) and 4 sto evaluators (tone_empathy, pii_check, redaction_quality, content_prohibition). Runs in mock mode with no API key; pushes span trees to dashboard for live monitoring.
- **Mock data fallback for all dashboard pages**: MonitorPage (Live + History tabs), Analytics, and Leaderboard now fall back to comprehensive mock data when the API is offline — enables demo/hackathon mode without running the backend. Mock data includes ops_agent trace events, 10 span trees, 6 trace summaries, analytics with score history + violation breakdowns, and a 7-entry leaderboard.
- **Expanded mock data** (`web/src/data/mockMonitorData.ts`): Added ops_agent events (13 trace events, 17 monitor events, 4 new span trees with det+sto combined checks), updated analytics to include pii_check and content_prohibition patterns, added ops_agent to leaderboard and reliability scores.
- **Monitor page redesign** (`web/src/pages/MonitorPage.tsx`): Complete rewrite of the Live tab as a professional observability dashboard with 6 panels: (1) Metric Cards — 5 real-time stats (events, det blocks, sto retries, pass rate, avg latency) with color-coded accents; (2) Agent Timeline — horizontal swimlane per agent with clickable dots colored by verdict (pass/block/retry); (3) Span Inspector — filterable span tree list with agent/status dropdowns and full SpanTree expansion; (4) Contract Health — per-contract pass rate progress bars sorted by violation count; (5) Enforcement Summary — stacked bar + legend showing outcome breakdown (blocked/retried/escalated/passed); (6) Enhanced Violation Feed — det/sto tabs, evidence display, and suggested fixes.
- **External span bridge in API** (`api/routers/monitor.py`): Added `_external_log_entries()` and `_external_trace_events()` helpers that synthesize MonitorEvent and TraceEvent data from externally pushed span trees. The `/monitor/log`, `/monitor/status`, `/monitor/trace` endpoints and SSE stream now include data from agents that push via `/monitor/push-span`, fixing the issue where the demo walkthrough data was invisible to the frontend.

- **Unified LLM extraction layer** (`sponsio/generation/llm_extraction.py`): Single Atom-aware LLM prompt shared by all three input paths (NL strings, policy documents, code scanning). The LLM is told about the full Atom vocabulary and Pattern catalog, enabling automatic hard/soft classification and accurate pattern selection. Includes `UnifiedExtractor` with `extract_from_nl()`, `extract_from_document()`, and `extract_from_code()` methods.
- **Enhanced CodeAnalyzer** (`sponsio/discovery/extractors/code_analysis.py`): Two-stage pipeline — AST pass (deterministic, zero deps) + optional LLM pass for deeper inference across all 16 hard patterns and 6 soft categories. New: LangGraph `graph.add_node()` detection, docstring/param/source extraction for LLM context, `generate_yaml()` for `sponsio init --scan`, `get_tool_inventory()` export.
- **Validation + suggestion engine**: Parse failures now include actionable suggestions ("Did you mean 'must_precede'?") with the list of available patterns.
- **36 new tests** (`tests/test_llm_extraction.py`): Full coverage of compilation (all 16 hard patterns + 6 soft categories), suggestion engine, mock LLM integration, error resilience.
- **LLM fallback in NL parser** — `parse_nl_unified()` now accepts an optional `llm_extractor` parameter. When rule-based keyword parsing fails, the `UnifiedExtractor` is tried before falling back to soft keyword classification. `config_to_system()` also accepts `llm_extractor` and `tool_inventory` so YAML-loaded contracts benefit from LLM-assisted parsing.
- **Extensible Atom vocabulary** — `register_atom(signature, description)` and `register_atoms()` let users define custom observable predicates that are automatically included in the LLM extraction prompt. Custom atoms appear alongside built-in atoms so the LLM can reference them in constraint extraction. `get_custom_atoms()`, `clear_custom_atoms()` for inspection and testing.

### Changed
- **`AnnotatedFormula` renamed to `DetFormula`** with backward-compatible alias for compatibility. All internal references updated to use the new name. Reflects the renamed concept: "deterministic" (hard) constraints evaluated via LTL on the execution trace.
- **`StoConstraint` renamed to `StoFormula`** with backward-compatible alias for compatibility. All internal references updated to use the new name.
- **DocumentExtractor rewritten** to delegate to `UnifiedExtractor` — uses Atom-aware prompting instead of the old pattern-name-only prompt. Unknown hard patterns now fail with logged error instead of silently converting to soft constraints.
- **Multi-Agent Eval Pipeline** (`AgentEval/`): Flagship example — 5 agents, 12 tools, 13 contracts covering every Sponsio pattern type. Demonstrates both NL and programmatic contract APIs, catches 5 violation types (must_precede, scope_limit, requires_permission, no_reversal, rate_limit), and exports trace datasets for downstream analysis.
- **Pattern Architecture design doc** (`agent_docs/pattern_architecture.md`): Establishes the four-level concept stack (Atom -> Formula -> Pattern -> Contract), documents the full atom vocabulary, defines grounding as a thin event adapter, describes two complementary observation models (integration hooks for real-time blocking, OTEL consumer for post-hoc audit), and classifies patterns by observation boundary.
- **`arg_paths_within(tool, *prefixes)` atom**: Checks all file paths in tool args are within allowed prefix set (replaces FOL `ForAllPaths` quantifier).
- **`arg_field_has(tool, field, pattern)` atom**: Regex match on a specific arg field (`event.args[field]`), restoring field-level precision that was lost in FOL elimination. Used by `arg_blacklist`.

### Fixed
- **MCP example DX consistency** — `mcp_guard.py` rewritten to use `sponsio.init()` + `guard_before()`/`guard_after()` in mock mode, matching the pattern of all other integration examples. Now shows contract banner, per-constraint enforcement lines, and session summary.

### Changed
- **`arg_blacklist` uses `arg_field_has` for field-specific matching** — restores the per-field precision from the old FOL system. `arg_blacklist("bash", "command", [...])` now only checks `args["command"]`, not all args.
- **`arg_blacklist`, `scope_limit`, `data_intact` now return `DetFormula`** instead of `PropertyConstraint`. Same function signatures, same behavioral semantics, unified into the Atom + LTL system.
- **`PropertyConstraint` class removed** — all patterns use `DetFormula`.
- **`sponsio/formulas/fol.py` deprecated** — FOL AST replaced by grounding-level atoms. Module emits `DeprecationWarning` on import.
- **`property_constraints` parameter removed from `ground()`** (internal API).
- **`prop.*` predicate key namespace removed** (internal — replaced by standard atom keys).
- **`never_together` deprecated** — delegates to `mutual_exclusion` (original formula trivially satisfied in sequential traces)
- **NL parser: `no_data_leak` with tool names** routes to `no_reversal` (tool ordering, not data flow)
- **NL parser: `requires_permission` with tool-like names** routes to `must_precede` (dynamic, not static)
- **`must_precede` and `must_confirm` formulas** rewritten to use `Until` operator instead of derived `precedes()` predicate
- **Grounding simplified** — removed derived `precedes()` predicate; ordering now handled purely by LTL
- **CrewAI hooks renamed**: `before_hook` → `on_tool_start`, `after_hook` → `on_tool_end` (old names kept as deprecated aliases)

### Added
- **NL keyword rules for 7 patterns**: `idempotent`, `must_confirm`, `cooldown`, `bounded_retry`, `segregation_of_duty`, `deadline`, `always_followed_by` — all now parseable from natural language
- **End-to-end pattern verification** (`tests/test_pattern_e2e.py`): 13 tests covering all patterns through NL → BaseGuard pipeline
- **6 integration examples** (`examples/integrations/`): Vanilla, LangGraph, OpenAI SDK, CrewAI, Agents SDK, MCP — each with a different real-world scenario and contract patterns. All support mock mode and real Gemini LLM (`USE_MOCK=0 GOOGLE_API_KEY=...`).
- **`sponsio.init()` one-liner entry point** (`sponsio/core.py`): `guard = sponsio.init(framework="langgraph", agent_id="bot", guarantees=[...])` auto-selects the correct Guard class. Supports `dashboard=True` for auto-start, `config="sponsio.yaml"` for file-based config, and all framework guards. Framework registry with lazy imports — optional deps only loaded when needed.
- **Clean `__init__.py`**: Removed internal class exports (`DetEvaluator`, `StoEvaluator`, `RuntimeMonitor`). Now exports only user-facing API: `init`, `load_config`, models, and lazy-loaded Guard classes via `__getattr__`.
- **Module reorganization**: `_pred_key.py` moved to `sponsio/formulas/`, `scoring.py` moved to `sponsio/scoring/` package. Backward-compatible re-exports at old locations.
- **CLI commands `validate` and `check`**: `sponsio validate` parses NL contracts and shows pattern/formula; `sponsio check --trace` verifies contracts against OTEL trace files. Both support `--config` for YAML config files and `--json` for machine-readable output.
- **YAML contract config** (`sponsio/config.py`): Define per-agent assume/guarantee contracts in `sponsio.yaml`. `load_config()`, `config_to_guard_kwargs()`, `config_to_system()`. Optional dep: `pip install sponsio[config]`.
- **Guard class renames**: `ContractGuard` → `LangGraphGuard`, `AgentsGuard` → `AgentsSDKGuard`. Old names preserved as backward-compatible aliases.
- **TerminalReporter** (`sponsio/runtime/terminal.py`): Real-time CLI feedback during agent execution with `assume`/`enforce` labels, colored pass/VIOLATED output. `Contract.to_str()` with aligned `▸` bullet display. `BaseGuard.print_summary()` for session summaries.
- **Example traces**: `examples/traces/good_trace.json` and `bad_trace.json` (OTEL format) for testing `sponsio check`.
- **OTEL ingestion endpoint** — Sponsio dashboard now accepts traces from any OTEL-compatible agent framework via `POST /api/otel/v1/traces`. Query trace summaries (`GET /traces`), full span trees (`GET /traces/{id}`), and flat lists (`GET /traces/{id}/flat`). Sponsio spans and external framework spans (LangGraph, OpenAI, etc.) appear together with `is_sponsio` flag for differentiated rendering. Push-span bridge ensures internal Sponsio spans also appear in the OTEL trace store.
- **OpenTelemetry span exporter** (`sponsio/integrations/otel.py`): Optional `OTelExporter` that translates Sponsio span trees into OTEL spans and sends them to any compatible backend (LangFuse, Arize Phoenix, Datadog, Jaeger). All 8 span types mapped with correct attributes, parent-child nesting, and status codes. Install with `pip install sponsio[otel]`.
- **BaseGuard `otel_exporter` parameter**: Pass an `OTelExporter` instance to any guard (LangGraph, MCP, OpenAI, CrewAI, Agents SDK) for automatic span export after every `guard_before()` / `guard_after()` call.
- **Unified TraceTimeline component** (`web/src/components/TraceTimeline.tsx`): Single shared trace viewer used by both Demos and AgentConnect pages. Shows `call`/`read`/`write` type badges, source/target data flow labels (e.g. `read <- Google Drive`, `write -> #marketing`), streaming animation, SpanTree expansion on click, and side-by-side "Without Sponsio" / "With Sponsio" comparison.
- **Mock / Real LLM toggle on Demos page**: Switch between scripted mock scenarios and real Gemini agent runs. Real LLM mode runs the agent twice (without guard, with ContractGuard) and displays both results side-by-side.
- **Live LLM demo endpoints**: `GET /demo/live-status` checks API key and dependency availability. `POST /demo/run-live` runs both unguarded and guarded LLM flows, returns structured results with data flow metadata.
- **Data flow metadata on demo scenarios**: All demo tool_call steps now include `event_type` (`data_read`/`data_write`/`tool_call`), `source` (e.g. "Order DB", "Google Drive"), and `target` (e.g. "Payment Gateway", "#marketing") for trace visualization.
- **Dashboard push from demo examples**: `fmt.py` gains `dashboard_reset()`, `dashboard_seed()`, `dashboard_push_span()`, `dashboard_push_all_spans()`. All three demo scripts push ContractGuard span trees to the dashboard API after each guarded flow.
- **`.env` file support**: API server loads `.env` from project root on startup for API keys (gitignored).

### Fixed
- **Leaderboard crash**: `sqlite3.Row` `row_factory` was being mutated per-query on a shared connection across threads, causing segfaults. Now set once at connection creation.
- **Python 3.9 compatibility**: Added `from __future__ import annotations` to `api/db.py` for `Dict[str, Any] | None` syntax.

### Changed
- **`examples/hackathon/` renamed to `examples/demo/`**: All paths updated across codebase.
- **`config_driven.py` removed**: YAML config usage documented in README instead.
- **Demos.tsx refactored**: Replaced `NaiveTimeline` + `EnforcedTimeline` with unified `TraceTimeline` component.
- **AgentConnect.tsx refactored**: Replaced inline `Timeline` component (~130 lines) with the same `TraceTimeline`.

### Added
- **Frontend redesign — 3-view developer tool**: Consolidated 7 pages into 3 focused views (Dashboard, Playground, Trace). Notebook-style Playground with contract cell, agent cell, execution cell, and inline result cell. Trace Viewer with horizontal flow summary, expandable block detail with SpanTree progressive disclosure, and sliding re-verify panel.
- **Dark mode**: Full light/dark theme toggle via Tailwind `darkMode: 'class'`, persisted in localStorage with `prefers-color-scheme` fallback. All components styled for both modes.
- **SpanTree component** (`web/src/components/SpanTree.tsx`): 3-level progressive disclosure — Level 1 (verdict card), Level 2 (per-contract summary), Level 3 (full evaluation chain). Auto-expands for violations.
- **PatternPicker component** (`web/src/components/PatternPicker.tsx`): Searchable, collapsible pattern library picker grouped by category. Reusable in Playground and Trace pages.
- **Real-time event streaming**: `POST /monitor/push` endpoint for live agents to push events via `dashboard_url` parameter on BaseGuard. `_push_to_dashboard()` fire-and-forget helper uses stdlib `urllib` (zero deps).
- **Trace import/export**: `POST /monitor/import` for bulk trace import, `BaseGuard.export_trace()` for serialization. JSON file import in Trace page UI.
- **Contract re-verification**: `POST /monitor/re-verify` tests new NL contracts against existing traces with per-step pass/fail results. Sliding panel in Trace page with hypothetical violation annotations.
- **Span tree API**: `GET /monitor/spans` returns structured span trees from the current session. Fixed `to_dict()` in all span subclasses to include subclass-specific fields.
- **Pattern library API**: `GET /patterns/library` returns all 16 patterns with examples, descriptions, and parameters.
- **Session reset**: `POST /monitor/reset` clears all state for fresh sessions.

### Fixed
- **Span serialization**: `to_dict()` in all 8 span subclasses (`AgentTurnSpan`, `ContractCheckSpan`, `PreconditionSpan`, `GuaranteeSpan`, `ViolationSpan`, `EnforcementSpan`, `StoCheckSpan`, `StoEvalSpan`) now includes subclass-specific fields

### Removed
- Frontend pages: `ContractEditor.tsx`, `Agents.tsx`, `PatternLibrary.tsx`, `Discovery.tsx` (functionality absorbed into Playground and Trace views)

### Added
- **First-Order Logic (FOL) predicate engine** (`sponsio/formulas/fol.py`): Per-event property checking with a typed AST — value references (`Field`, `Literal`), comparison predicates (`Equals`, `Matches`, `HasPrefix`, `InSet`, `GreaterThan`, `LessThanEq`), boolean connectives (`PNot`, `PAnd`, `POr`, `PImplies`), and universal quantification over file paths (`ForAllPaths`). Two-backend design: Python `eval_predicate()` for runtime, Z3 backend planned for pre-deployment satisfiability. Zero new dependencies.
- **FOL-based PropertyConstraint patterns** (`sponsio/patterns/library.py`): Per-event constraints that bridge FOL predicates into the LTL pipeline — `arg_blacklist(tool, param, patterns)` forbids regex-matched content in tool arguments, `scope_limit(tool, allowed_paths)` restricts file operations to whitelisted path prefixes, `data_intact(bound_tool, original_paths)` ensures tools operate only on unmodified source data. Results are grounded as `prop.*` predicates consumed by the LTL evaluator.
- **Structured observability** (`sponsio/models/spans.py`): Hierarchical span trees for contract enforcement. Each `check_action()` call produces an `AgentTurnSpan` tree with per-phase spans (`PreconditionSpan`, `GuaranteeSpan`, `ViolationSpan`, `EnforcementSpan`, `StoCheckSpan`, `StoEvalSpan`). Includes `SpanCollector` context manager, `render_tree()` CLI output, `to_dict()` / `to_flat_list()` for OTel-ready export. Zero new dependencies.
- **BaseGuard span accessors**: `last_check_span`, `check_spans`, `render_checks()` on all integration guards
- **RuntimeMonitor span properties**: `last_turn_span`, `turn_spans`, `render_last_turn()`
- **Demo span tree output**: All three demos (`customer_service`, `coding_agent`, `mcp_leak`) now print structured span trees after each "with protection" scenario

### Fixed
- **Version mismatch**: `sponsio/__version__` now matches pyproject.toml (`"0.1.0-alpha"`)
- **Repo-wide lint cleanup**: Fixed all 35 lint errors — unused imports across `discovery/`, `models/`, `tests/`, `examples/`; ambiguous variable `l` → `line` in `sto_catalog.py`; unused `agent_id` in `crewai.py`
- **Stale docs**: Updated test counts (262 → 341) in CLAUDE.md and README.md; corrected STATUS.md known issues (CLI and `[project.scripts]` already exist); fixed soft evaluator count in cli.py (7 → 5)

### Added
- **Pattern library expanded to 16+ patterns**: 14 temporal LTL patterns (`idempotent`, `deadline`, `must_confirm`, `cooldown`, `segregation_of_duty`, `bounded_retry` — with bounded temporal operators) plus FOL-based `PropertyConstraint` patterns (`arg_blacklist`, `scope_limit`, `data_intact`)
- **Grounding layer**: Added `count(action)` cumulative invocation tracking; FOL `property_constraints` parameter evaluates per-event predicates and stores results as `prop.*` keys in valuations
- **Automatic Contract Discovery** (`sponsio/discovery/`):
  - **Document extraction** (Phase 1): LLM-based extraction from policy docs (.txt, .md, .pdf)
  - **Trace mining** (Phase 2): Statistical pattern mining from historical traces (.json) — discovers ordering, exclusion, frequency, and sequence patterns
  - **Code analysis** (Phase 3): AST-based extraction from Python source — finds tool registrations, call graph dependencies, guard patterns
  - **Validation pipeline**: 5-step validation (syntactic, triviality, consistency, trace replay, human review)
  - **PatternStore**: Categorized, JSON-backed storage at `~/.sponsio/patterns.json` with auto-save, organized by source (builtin / user_defined / auto_extracted) and status (proposed / verified / rejected). Builtin patterns are protected from deletion.
  - **File loaders**: Support for `.txt`, `.md`, `.pdf` documents; single/array `.json` traces with glob patterns; `.py` files and directories with recursive resolution
- **New integrations**: CrewAI (`CrewAIGuard` + before/after hooks), OpenAI Agents SDK (`AgentsGuard` + `wrap()`), OpenAI SDK (`patch_openai()` / `unpatch_openai()`)
- All integrations support `store=` parameter for automatic pattern registration
- **`on_violation` parameter**: Contracts support per-rule violation actions — `"block"` (default), `"warn"`, or `"log"`. New `WarnOnly` strategy records violations without blocking execution
- **`sponsio` CLI**: `sponsio demo --scenario customer|coding|mcp [--real]` runs demos from terminal; `sponsio patterns` lists all 14 hard patterns + 7 soft evaluator categories
- **Assume-guarantee contracts**: `assumptions=` parameter on all guards; assumption violations report upstream problems via `EscalateToHuman` instead of silently skipping
- **Soft (semantic) constraints**: Auto-detected from NL input alongside det constraints
  - Built-in evaluators: PII detection (regex), length check, format validation, content prohibition — zero dependencies
  - LLM evaluators: tone, relevance, generic judge — optional, requires `openai`
  - `StoConstraint` dataclass in `sponsio/patterns/sto.py`; catalog in `sto_catalog.py`
  - `parse_nl_unified()` auto-routes NL to hard pattern or soft evaluator
  - Discovery extractors emit soft proposals for unmatched patterns

### Planned
- Pre-deployment compositional verification
- Metrics & before/after comparison dashboard

---

## [0.1.0-alpha] - 2026-03-29

Initial public release.

### Added
- **Core formula engine**: Immutable LTL AST (`Atom`, `Not`, `And`, `Or`, `Implies`, `G`, `F`, `X`, `U`) with finite-trace evaluator using weak semantics
- **Arithmetic constraints**: `Le`, `Lt`, `Ge`, `Gt`, `Eq`, `Var`, `Const`, `Subset` nodes (SMT-ready)
- **Pattern library**: 8 user-facing constraint patterns that compile to LTL — `must_precede`, `always_followed_by`, `never_together`, `no_reversal`, `requires_permission`, `no_data_leak`, `mutual_exclusion`, `rate_limit`
- **NL → contract parser**: Keyword-based rule parser mapping natural language descriptions to pattern functions; LLM-assisted path (optional, requires OpenAI key)
- **Dual enforcement pipeline**: Det constraints (binary, blocks before execution) and sto constraints (scored 0–1, generates feedback for agent self-correction)
- **Grounding layer**: Converts raw `Event` traces to per-timestep predicate valuations (`called`, `precedes`, `contains`, `flow`, `perm`)
- **LangGraph integration**: `ContractGuard` using LangGraph's native `wrap_tool_call` API — zero schema changes required
- **MCP integration**: `MCPContractProxy` for Model Context Protocol tool scanning and enforcement
- **FastAPI dashboard backend**: REST API for agent/contract management, playground, and monitoring
- **React + TypeScript frontend**: Dashboard UI built with Vite, Tailwind CSS, and React Router
- **Demo scenarios**: Customer service agent (det constraint, happy path, sto constraint)
- **Docker support**: `docker-compose.yml` for one-command local setup

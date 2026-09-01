"""A run, as the console sees it."""

from __future__ import annotations

import hashlib
import json
import secrets
import os
import time
from pathlib import Path
from typing import Any

from sponsio.bridge.spans import (
    STOPPING_ACTIONS,
    args_preview,
    slug,
    step_status,
    violations_from_turn,
)


def _contracts_from_guard(guard: Any) -> list[dict]:
    """Project the guard's contracts into view-model rows.

    Status comes from each contract's effective mode, so an observing book
    with one armed rule shows exactly that instead of claiming all or
    nothing.
    """
    rows: list[dict] = []
    system = getattr(guard, "_system", None)
    global_mode = getattr(guard, "mode", "observe")
    for contract in getattr(system, "_contracts", None) or []:
        for guarantee in getattr(contract, "guarantees", None) or []:
            label = getattr(guarantee, "desc", None) or getattr(contract, "desc", None)
            if not label:
                continue
            if isinstance(label, list):
                label = " ".join(str(x) for x in label)
            mode = getattr(contract, "mode", None) or global_mode
            row = {
                "id": slug(str(label)),
                "label": str(label),
                "status": "armed" if mode == "enforce" else "watching",
                "pipeline": "det",
                "violationCount": 0,
            }
            authored = getattr(contract, "desc", None)
            # The authored sentence and the compiled formula's description
            # can differ; a violation may arrive under either spelling.
            if authored and str(authored) != str(label):
                row["_alt_label"] = str(authored)
            rows.append(row)
    return rows


def _trace_id(agent: str) -> str:
    """A stable 128-bit id per agent, so each agent's steps group into their
    own trace the way OTEL expects."""
    return hashlib.sha1(agent.encode()).hexdigest()[:32]


def _say_of(response: Any) -> str:
    """The sentence to show for a model turn.

    Structured output is the normal case: prefer a headline/message-ish
    field, else join its string values, so the console shows words rather
    than a JSON blob. Falls back to the raw text.
    """
    text = response if isinstance(response, str) else ""
    try:
        parsed = json.loads(text) if text else response
    except (ValueError, TypeError):
        return text
    if not isinstance(parsed, dict):
        return text
    for key in ("message", "headline", "reply", "text", "answer", "summary"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            return value
    joined = " · ".join(str(v) for v in parsed.values() if isinstance(v, str))
    return joined or text


def _own_book(stamp: str, agent: str) -> str | None:
    """This agent's ``agent@vN`` out of a checkout stamp.

    A whole-project pull is stamped "<project> a@v3 b@v1 ... sha:..."; a
    single-agent pull "<project>@vN" with no agent prefix at all.
    """
    tokens = [t for t in stamp.split() if "@v" in t]
    for token in tokens:
        if token.startswith(f"{agent}@v"):
            return token
    if len(tokens) == 1:
        version = _book_version(tokens[0])
        if version is not None:
            return f"{agent}@v{version}"
    return None


def _book_version(book: str | None) -> int | None:
    if not book or "@v" not in book:
        return None
    tail = book.rsplit("@v", 1)[1]
    digits = "".join(ch for ch in tail if ch.isdigit())
    return int(digits) if digits else None


def _claim_span(verified: Any) -> str:
    """What the model actually said for this claim, as display text."""
    value = getattr(verified, "value", None)
    if value not in (None, ""):
        return str(value)
    return str(getattr(getattr(verified, "spec", None), "claim_field", "") or "claim")


class BridgeSession:
    """One run being streamed to a console.

    Sending is best-effort by construction: every upload is wrapped, and a
    failure is counted and reported at the end rather than raised. An agent
    must not fail because a dashboard did.
    """

    def __init__(
        self,
        guard: Any,
        *,
        project: str = "default",
        session_id: str | None = None,
        client: Any = None,
        contracts: list[dict] | None = None,
        agents: list[dict] | None = None,
        runs_dir: str | Path | None = None,
    ) -> None:
        self.guard = guard
        self.project = project
        # The server keys a run by this id and upserts, so two runs sharing
        # one id silently become one: the older is overwritten with no error.
        # The old id was 32 bits derived from id(guard) and the clock, which
        # collides around 65k runs by birthday alone — and worse in practice,
        # since memory addresses get reused and a booting fleet shares the
        # clock. 64 bits from the system CSPRNG puts a collision out of reach.
        self.session_id = session_id or ("run-" + secrets.token_hex(8))
        self.mode = getattr(guard, "mode", None) or "observe"
        self.root_agent = getattr(guard, "agent_id", "agent")
        self.started_at = int(time.time() * 1000)
        self.steps: list[dict] = []
        self.edges: list[dict] = []
        self.live = True
        self.send_failures = 0
        self._last_error: str | None = None

        self._runs_dir = (
            Path(runs_dir) if runs_dir else Path.cwd() / ".sponsio" / "runs"
        )

        if client is None:
            from sponsio.cloud.client import CloudClient

            client = CloudClient()
        self.client = client

        self.contracts: dict[str, dict] = {}
        # Default to the guard's own book. Without this the console shows a
        # run with an empty Rulebook, which reads as "nothing was checked"
        # when in fact everything was.
        for raw in contracts if contracts is not None else _contracts_from_guard(guard):
            contract = dict(raw)
            contract.setdefault("violationCount", 0)
            contract.setdefault("status", "armed")
            contract.setdefault("pipeline", "det")
            contract.setdefault("source", "policy")
            contract.setdefault("boundAgents", [])
            self.contracts[contract["id"]] = contract

        self._by_label: dict[str, dict] = {}
        for contract in self.contracts.values():
            if contract.get("label"):
                self._by_label[contract["label"]] = contract
            # A violation span carries the guarantee's formula text, which
            # can differ from the authored label; index both so a rule still
            # matches whichever spelling the runtime emitted.
            if contract.get("_alt_label"):
                self._by_label.setdefault(contract["_alt_label"], contract)

        self.agents: dict[str, dict] = {}
        for agent in agents or []:
            self._ensure_agent(
                agent["id"],
                role=agent.get("role", "agent"),
                tools=agent.get("tools", []),
            )

    # -- agents ------------------------------------------------------------

    def _ensure_agent(
        self, agent_id: str, role: str = "agent", tools: list | None = None
    ) -> dict:
        agent = self.agents.get(agent_id)
        if agent is None:
            agent = {
                "id": agent_id,
                "serviceName": agent_id,
                "role": role,
                "tools": list(tools or []),
                "traceIds": [_trace_id(agent_id)],
                "violationCount": 0,
            }
            self.agents[agent_id] = agent
        return agent

    # -- recording ---------------------------------------------------------

    def record(
        self,
        tool: str,
        args: Any = None,
        *,
        agent: str | None = None,
        type: str = "tool_call",
    ) -> dict:
        """Project the guard's last check into a step and send the run."""
        turn = getattr(self.guard, "last_check_span", None)
        turn = turn.to_dict() if turn is not None else {}

        agent_id = agent or turn.get("agent_id") or self.root_agent
        violations = violations_from_turn(turn, self._by_label)
        status = step_status(turn, violations)
        idx = len(self.steps)

        step = {
            "id": f"s{idx}",
            "ts": idx,
            "agentId": agent_id,
            "serviceName": agent_id,
            "traceId": _trace_id(agent_id),
            "spanId": f"{idx:016x}",
            "type": type,
            "tool": tool,
            "argsPreview": args_preview(args),
            "durationMs": round(float(turn.get("duration_ms", 0.0) or 0.0), 2),
            "status": status,
        }
        checked = int(turn.get("total_contracts_checked", 0) or 0)
        if checked or violations:
            step["verdict"] = {
                "blocked": status in STOPPING_ACTIONS,
                "contractsChecked": checked,
                "violations": violations,
            }

        self.steps.append(step)
        agent_row = self._ensure_agent(agent_id)
        for violation in violations:
            agent_row["violationCount"] += 1
            contract = self.contracts.get(violation["contractId"])
            if contract is not None:
                contract["violationCount"] = contract.get("violationCount", 0) + 1

        self._send_soon()
        return step

    def note(self, agent: str, text: str, type: str = "message") -> dict:
        idx = len(self.steps)
        step = {
            "id": f"s{idx}",
            "ts": idx,
            "agentId": agent,
            "serviceName": agent,
            "traceId": _trace_id(agent),
            "spanId": f"{idx:016x}",
            "type": type,
            "tool": text.split(":")[0].strip() or type,
            "argsPreview": text,
            "durationMs": 1.0,
            "status": "ok",
        }
        self.steps.append(step)
        self._ensure_agent(agent)
        self._send_soon()
        return step

    # Console action labels for the server's policy action. The server says
    # what it decided; the console says what the reader sees happen to the
    # output. A block that carries a correction is a rewrite; one that does
    # not is simply a block.
    _EV_ACTIONS = {"release": "released", "clarify": "clarify"}

    def record_output(self, response: Any, result: Any, *, agent: str | None = None) -> dict | None:
        """Project one model turn and its claim verdicts into a step.

        The action lane records tool calls; this is the output lane. Without
        it an ``observe_llm_call`` verdict lives only in the local trace and
        never reaches the console, so a run looks clean while the model is
        stating something false. Returns None when the turn carried no
        verified claims (nothing to show).
        """
        claims_in = list(getattr(result, "evidence_claims", None) or [])
        if not claims_in:
            return None

        claims: list[dict] = []
        for vc in claims_in:
            res = getattr(vc, "result", None)
            if res is None:
                continue
            verdict = str(getattr(res, "verdict", "") or "").upper()
            correction = getattr(res, "correction", None)
            values = list(getattr(res, "values", None) or [])
            action = self._EV_ACTIONS.get(
                str(getattr(res, "action", "") or "").lower(),
                "rewritten" if correction not in (None, "") else "blocked",
            )
            claims.append(
                {
                    "span": _claim_span(vc),
                    "predicate": str(getattr(getattr(vc, "spec", None), "predicate", "") or ""),
                    "verdict": verdict,
                    "source": str(getattr(res, "source", "") or ""),
                    "freshness": "",
                    "evidence": ("authoritative: " + ", ".join(str(v) for v in values)) if values else "",
                    "action": action,
                    "fix": "" if correction in (None, "") else str(correction),
                }
            )
        if not claims:
            return None

        agent_id = agent or self.root_agent
        idx = len(self.steps)
        step = {
            "id": f"s{idx}",
            "ts": idx,
            "agentId": agent_id,
            "serviceName": agent_id,
            "traceId": _trace_id(agent_id),
            "spanId": f"{idx:016x}",
            "type": "assistant_output",
            "tool": "reply",
            "durationMs": 1.0,
            "status": "mismatch" if any(c["verdict"] == "MISMATCH" for c in claims) else "ok",
            "say": _say_of(response),
            "output": {"checked": len(claims), "claims": claims},
        }
        self.steps.append(step)
        self._ensure_agent(agent_id)
        self._send_soon()
        return step

    def _edge(self, source: str, target: str, kind: str, label: str) -> None:
        self._ensure_agent(source)
        self._ensure_agent(target, role="worker")
        self.edges.append(
            {"from": source, "to": target, "kind": kind, "ts": len(self.steps)}
        )
        self.note(source, label, type="delegation")

    def call(self, parent: str, child: str) -> None:
        """Invocation edge: ``parent`` calls ``child`` and gets a result back."""
        self._edge(parent, child, "call", f"call -> {child}")

    def handoff(self, source: str, target: str) -> None:
        """Causal edge: ``source`` finishes and passes control (and any taint)
        to ``target``."""
        self._edge(source, target, "handoff", f"handoff -> {target}")

    # -- view-model --------------------------------------------------------

    def summary(self) -> dict:
        total = len(self.steps)
        stopped = sum(1 for s in self.steps if s["status"] in STOPPING_ACTIONS)
        clean = sum(1 for s in self.steps if s["status"] in ("ok", "allowed"))
        armed = sum(1 for c in self.contracts.values() if c.get("status") == "armed")
        return {
            "passRate": round(clean / total, 4) if total else 1.0,
            "totalSteps": total,
            "detBlocks": sum(1 for s in self.steps if s["status"] == "blocked"),
            "stoRetries": sum(1 for s in self.steps if s["status"] == "retrying"),
            "escalations": sum(1 for s in self.steps if s["status"] == "escalated"),
            "contractsArmed": armed,
            "contractsTotal": len(self.contracts),
            "stopped": stopped,
        }

    def view_model(self) -> dict:
        vm: dict[str, Any] = {
            "session": {
                "id": self.session_id,
                "agentId": self.root_agent,
                "mode": self.mode,
                "startedAt": self.started_at,
            },
            "agents": list(self.agents.values())
            or [self._ensure_agent(self.root_agent)],
            "edges": list(self.edges),
            "steps": list(self.steps),
            "contracts": list(self.contracts.values()),
            "summary": self.summary(),
        }
        # Which book this run enforced, when the config came from the cloud.
        # Absent for a local file, and absent is honest: a fabricated version
        # would make a replay claim to reproduce something it cannot.
        stamp = os.environ.get("SPONSIO_RULEBOOK_STAMP", "").strip()
        if stamp:
            # The checkout stamp names every agent in the project
            # ("default a@v3 b@v1 ... sha:..."); a run is ONE agent's, so
            # it records that agent's book — the form the console links
            # to and the server keys tests by — and keeps the whole stamp
            # for provenance. Without this the run named seventeen books
            # and its version parsed as none.
            own = _own_book(stamp, self.root_agent)
            vm["rulebook"] = own or stamp
            vm["rulebookRef"] = stamp
            version = _book_version(own)
            if version is not None:
                vm["session"]["rulebookVersion"] = version
        return vm

    # -- transport ---------------------------------------------------------

    # A frame carries the WHOLE run, so sending one per step makes a long
    # agent upload O(steps^2) bytes: measured, 1k steps is 142 MB and 5k is
    # 3.5 GB, and past ~27k the frame alone exceeds the 8 MB ingest cap and
    # every later send 413s. Coalescing to at most one frame per interval
    # keeps the console live to the eye and the traffic linear-ish; finish()
    # always flushes, so the final state is never the stale one.
    SEND_MIN_INTERVAL_S = 1.0

    # Frames grow with the run, so a fixed interval still lets a very long
    # agent climb: the rate, not the count, is what has to stay bounded.
    # Backing off in proportion to the last frame holds outbound traffic
    # near this ceiling no matter how big the session gets.
    SEND_MAX_KB_PER_S = 250.0

    def _send_interval(self) -> float:
        last_kb = getattr(self, "_last_frame_bytes", 0) / 1024.0
        return max(self.SEND_MIN_INTERVAL_S, last_kb / self.SEND_MAX_KB_PER_S)

    def _send_soon(self) -> None:
        """Mark the run changed; send if this frame is not too soon after the last."""
        now = time.time()
        if (now - getattr(self, "_last_send_at", 0.0)) < self._send_interval():
            self._pending = True
            return
        self.send()

    def send(self) -> bool:
        """Best effort. Never raises, never blocks the agent."""
        self._pending = False
        self._last_send_at = time.time()
        if not getattr(self.client, "configured", False):
            return False
        payload = {"key": self.session_id, "live": self.live, "vm": self.view_model()}
        try:
            self._last_frame_bytes = len(json.dumps(payload))
        except Exception:  # noqa: BLE001 - sizing is advisory only
            self._last_frame_bytes = 0
        try:
            self.client.ingest_session(self.project, payload)
            return True
        except Exception as exc:  # noqa: BLE001 - telemetry must not break a run
            self.send_failures += 1
            self._last_error = str(exc)
            # Losing telemetry must not change what the agent does, but it
            # must not be invisible either: a run that silently stopped
            # reaching the console still looks live and complete on screen.
            if not getattr(self, "_warned_send", False):
                self._warned_send = True
                print(
                    f"  sponsio: this run is no longer reaching the console "
                    f"({exc}); the agent is unaffected.",
                    flush=True,
                )
            return False

    def finish(self) -> dict[str, Any]:
        """End the run: final send, local artifacts, one honest summary line."""
        self.live = False
        delivered = self.send()

        paths: dict[str, str] = {}
        try:
            self._runs_dir.mkdir(parents=True, exist_ok=True)
            key = slug(self.session_id)
            # The trace keeps full arguments and stays local. It is what a
            # coding agent mines for missing contracts; it is not uploaded.
            exporter = getattr(self.guard, "export_trace", None)
            if callable(exporter):
                trace_path = self._runs_dir / f"{key}.trace.json"
                trace_path.write_text(json.dumps(exporter(), indent=1, default=str))
                paths["trace"] = str(trace_path)
            session_path = self._runs_dir / f"{key}.session.json"
            session_path.write_text(
                json.dumps(self.view_model(), indent=1, default=str)
            )
            paths["session"] = str(session_path)
        except OSError as exc:  # noqa: BLE001 - artifacts are a convenience
            self._last_error = str(exc)

        summary = self.summary()
        line = (
            f"  sponsio: {summary['totalSteps']} steps · "
            f"{summary['contractsTotal']} contracts · "
            f"{summary['stopped']} stopped"
        )
        if delivered:
            # Only claim a session exists in the console once one does.
            line += f" · session {self.session_id}"
        elif getattr(self.client, "configured", False):
            line += f" · not uploaded ({self._last_error or 'send failed'})"
        print(line, flush=True)

        return {"summary": summary, "paths": paths, "uploaded": delivered}


def attach(
    guard: Any,
    *,
    project: str = "default",
    auto: bool = True,
    session_id: str | None = None,
    client: Any = None,
    contracts: list[dict] | None = None,
    agents: list[dict] | None = None,
    runs_dir: str | Path | None = None,
) -> BridgeSession:
    """Stream ``guard``'s run to a console.

    With ``auto=True`` every ``guard_before`` becomes a step. Turn it off for
    multi-agent runs where each step needs attributing to an agent and the
    delegation edges have to be drawn by hand.
    """
    session = BridgeSession(
        guard,
        project=project,
        session_id=session_id,
        client=client,
        contracts=contracts,
        agents=agents,
        runs_dir=runs_dir,
    )
    if auto:
        original = guard.guard_before

        def wrapped(tool: str, args: Any = None, *rest: Any, **kwargs: Any):
            result = original(tool, args, *rest, **kwargs)
            try:
                session.record(tool, args)
            except Exception:  # noqa: BLE001 - recording must not break a run
                session.send_failures += 1
            return result

        guard.guard_before = wrapped  # type: ignore[method-assign]
        session._original_guard_before = original  # type: ignore[attr-defined]

    # The output lane does NOT ride the auto switch. `auto=False` says "I
    # attribute my own tool steps per agent", which is a statement about the
    # ACTION lane; it never meant "drop my claim verdicts". Returning early
    # on it took the output lane with it, so every multi-agent run — the
    # only reason to pass auto=False — showed a clean trace while the model
    # was stating something false.
    observe = getattr(guard, "observe_llm_call", None)
    if callable(observe):

        def wrapped_observe(*args: Any, **kwargs: Any):
            result = observe(*args, **kwargs)
            try:
                response = kwargs.get("response")
                if response is None and len(args) >= 2:
                    response = args[1]
                session.record_output(response, result)
            except Exception:  # noqa: BLE001 - recording must not break a run
                session.send_failures += 1
            return result

        guard.observe_llm_call = wrapped_observe  # type: ignore[method-assign]
        session._original_observe_llm_call = observe  # type: ignore[attr-defined]
    return session

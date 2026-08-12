"""A run, as the console sees it."""

from __future__ import annotations

import hashlib
import json
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
        self.session_id = session_id or (
            "run-" + hashlib.sha1(f"{id(guard)}{time.time()}".encode()).hexdigest()[:8]
        )
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

        self.send()
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
        self.send()
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
            vm["rulebook"] = stamp
        return vm

    # -- transport ---------------------------------------------------------

    def send(self) -> bool:
        """Best effort. Never raises, never blocks the agent."""
        if not getattr(self.client, "configured", False):
            return False
        payload = {"key": self.session_id, "live": self.live, "vm": self.view_model()}
        try:
            self.client.ingest_session(self.project, payload)
            return True
        except Exception as exc:  # noqa: BLE001 - telemetry must not break a run
            self.send_failures += 1
            self._last_error = str(exc)
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
    if not auto:
        return session

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
    return session

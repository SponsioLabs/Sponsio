"""Daemon-side evaluation of host hook events.

The per-call hook path (``sponsio host guard``) parses the library and
replays the JSONL trace on every tool call. This module is the same
decision (:func:`sponsio.guard_stdin.evaluate`) fed from state the
daemon keeps:

* :class:`SnapshotStore` loads each library once per content change.
  The cache key is the content of the yaml **and every pack it
  includes**, so editing an included pack invalidates it too. How a
  change is served is a policy (``on_change``): the default serves the
  new content; P0.7 pins the loaded snapshot until an explicit reload
  and reports the drift.
* :class:`MemoryTraceStore` keeps one trace per conversation in memory,
  merged across buckets the way the file layout merges them, with a
  write-ahead log under the daemon state dir so a restart restores
  every live conversation. Log records carry the same event fields as
  the legacy ``.shield-trace.jsonl`` lines, no more and no fewer.
* :class:`GuardService` serialises calls per conversation, runs the
  decision, and renders the host's reply, so a client (the shim, the
  OpenClaw plugin, the legacy CLI) only forwards bytes.
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading
import time
from contextlib import redirect_stderr
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from sponsio.guard_stdin import (
    _MAX_ATTACHED_OUTPUT,
    GuardOutcome,
    GuardPolicy,
    LibrarySnapshot,
    _error_outcome,
    _resolve_library,
    _safe_conv_id,
    evaluate,
    library_root,
    load_snapshot,
    render_reply,
)

OnChange = Callable[["LibrarySnapshot | None", LibrarySnapshot], LibrarySnapshot]


def default_state_dir() -> Path:
    """Where the daemon keeps its own state (traces, later audit)."""
    return Path.home() / ".sponsio" / "state"


# ---------------------------------------------------------------------------
# Snapshot store: compile once per content change
# ---------------------------------------------------------------------------


def _stat_signature(files: tuple[str, ...]) -> tuple:
    """Cheap change detector: (mtime_ns, size) per file, ``None`` when
    a file is gone. A match means the content hash need not be redone;
    a mismatch triggers a reload, which then compares hashes."""
    sig = []
    for p in files:
        try:
            st = os.stat(p)
            sig.append((st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append(None)
    return tuple(sig)


@dataclass
class _CacheEntry:
    path: str
    files: tuple[str, ...]
    stats: tuple
    loaded: LibrarySnapshot
    served: LibrarySnapshot


class SnapshotStore:
    """A :class:`~sponsio.guard_stdin.RuleStore` that caches by content.

    ``root`` is the plugin root, or a callable returning it (the tests
    resolve it per call; the daemon fixes it at start so the client's
    environment cannot move it). ``on_change`` decides what to serve
    when a library's content changes: the default is the new snapshot.
    """

    def __init__(
        self,
        root: Path | Callable[[], Path] | None = None,
        *,
        on_change: OnChange | None = None,
    ) -> None:
        self._root = root if root is not None else library_root
        self._on_change: OnChange = on_change or (lambda _old, new: new)
        self._lock = threading.Lock()
        self._cache: dict[str, _CacheEntry] = {}

    @property
    def root(self) -> Path:
        return self._root() if callable(self._root) else self._root

    def resolve(self, plugin_id: str) -> LibrarySnapshot | None:
        root = self.root
        lib_path, agent_id = _resolve_library(plugin_id, root)
        if not lib_path.exists():
            with self._lock:
                self._cache.pop(plugin_id, None)
            return None
        with self._lock:
            entry = self._cache.get(plugin_id)
            if (
                entry is not None
                and entry.path == str(lib_path)
                and entry.stats == _stat_signature(entry.files)
            ):
                return entry.served
            snap = load_snapshot(plugin_id, lib_path, agent_id)
            files = (
                tuple(snap.config.source_files)
                if snap.config is not None and snap.config.source_files
                else (str(lib_path),)
            )
            stats = _stat_signature(files)
            if (
                entry is not None
                and entry.path == str(lib_path)
                and entry.loaded.content_hash == snap.content_hash
            ):
                # Touched but not changed (a save with identical bytes).
                entry.stats = stats
                return entry.served
            served = self._on_change(entry.served if entry else None, snap)
            self._cache[plugin_id] = _CacheEntry(
                path=str(lib_path),
                files=files,
                stats=stats,
                loaded=snap,
                served=served,
            )
            return served

    def loaded(self, plugin_id: str) -> LibrarySnapshot | None:
        """The most recently loaded snapshot (what is on disk), which
        may differ from what :meth:`resolve` serves under a pinning
        policy."""
        with self._lock:
            entry = self._cache.get(plugin_id)
            return entry.loaded if entry else None

    def invalidate(self, plugin_id: str | None = None) -> None:
        with self._lock:
            if plugin_id is None:
                self._cache.clear()
            else:
                self._cache.pop(plugin_id, None)


# ---------------------------------------------------------------------------
# Trace store: memory plus write-ahead log
# ---------------------------------------------------------------------------


@dataclass
class _Conversation:
    key: str
    wal_path: Path
    events: list[tuple[str, dict]] = field(default_factory=list)
    last_activity: float = field(default_factory=time.time)


class MemoryTraceStore:
    """A :class:`~sponsio.guard_stdin.TraceStore` held in memory.

    One conversation per ``conversation_id`` (``"default"`` when the
    host sends none), merged across buckets, the same view
    ``_load_prior_events`` builds from the per-bucket files. Every
    append is written to ``<wal_dir>/<conversation>.jsonl`` before it
    is acknowledged; :meth:`load` replays those files on start.

    Records are one JSON object per line: ``{"b": bucket, "e": event}``
    for an admitted event, ``{"b": bucket, "a": {"tool", "content"}}``
    for an output attached by a post-event. ``event`` has exactly the
    fields the legacy trace lines have (``ts``, ``agent``, ``type``,
    ``tool``, ``args``, and ``content`` once attached).

    A conversation idle for longer than the TTL is dropped with its log
    on next access or at load, which is the legacy rotation rule and
    what bounds the log directory.
    """

    def __init__(
        self,
        wal_dir: Path,
        *,
        ttl_seconds: float | Callable[[], float] = 24 * 3600,
    ) -> None:
        self.wal_dir = Path(wal_dir)
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._convs: dict[str, _Conversation] = {}

    # ---- lifecycle ----

    @property
    def ttl_seconds(self) -> float:
        return float(self._ttl() if callable(self._ttl) else self._ttl)

    def load(self) -> int:
        """Replay the write-ahead logs. Returns the number of
        conversations restored."""
        self._ensure_dir()
        restored = 0
        with self._lock:
            for path in sorted(self.wal_dir.glob("*.jsonl")):
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if time.time() - mtime > self.ttl_seconds:
                    self._unlink(path)
                    continue
                conv = _Conversation(key=path.stem, wal_path=path, last_activity=mtime)
                self._replay(conv, path)
                self._convs[conv.key] = conv
                restored += 1
        return restored

    def _ensure_dir(self) -> None:
        self.wal_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.wal_dir, 0o700)
        except OSError:  # pragma: no cover - unusual filesystem
            pass

    @staticmethod
    def _replay(conv: _Conversation, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return
        for raw in text.splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            bucket = rec.get("b")
            if not isinstance(bucket, str):
                continue
            if isinstance(rec.get("e"), dict):
                conv.events.append((bucket, rec["e"]))
            elif isinstance(rec.get("a"), dict):
                MemoryTraceStore._apply_attach(
                    conv, bucket, rec["a"].get("tool"), rec["a"].get("content")
                )

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass

    # ---- conversation access ----

    def _key(self, conversation_id: str | None) -> str:
        return _safe_conv_id(conversation_id) if conversation_id else "default"

    def _conv(
        self, conversation_id: str | None, *, create: bool
    ) -> _Conversation | None:
        """Look up a conversation, dropping it first if it expired.
        Caller holds ``self._lock``."""
        key = self._key(conversation_id)
        conv = self._convs.get(key)
        now = time.time()
        if conv is not None and now - conv.last_activity > self.ttl_seconds:
            self._convs.pop(key, None)
            self._unlink(conv.wal_path)
            conv = None
        if conv is None and create:
            conv = _Conversation(key=key, wal_path=self.wal_dir / f"{key}.jsonl")
            self._convs[key] = conv
        return conv

    def conversations(self) -> list[str]:
        with self._lock:
            return sorted(self._convs)

    def forget(self, conversation_id: str | None) -> None:
        with self._lock:
            conv = self._convs.pop(self._key(conversation_id), None)
            if conv is not None:
                self._unlink(conv.wal_path)

    # ---- TraceStore protocol ----

    def events(self, plugin_id: str, conversation_id: str | None) -> list:
        from sponsio.models.trace import Event

        with self._lock:
            conv = self._conv(conversation_id, create=False)
            if conv is None:
                return []
            records = list(conv.events)
        events = []
        for _bucket, d in records:
            try:
                events.append(
                    Event(
                        ts=int(d["ts"]),
                        agent=d["agent"],
                        event_type=d["type"],
                        tool=d.get("tool"),
                        key=d.get("key"),
                        contains=d.get("contains"),
                        to=d.get("to"),
                        args=d.get("args"),
                        content=d.get("content"),
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue
        events.sort(key=lambda e: (e.ts, e.agent or ""))
        return events

    def append(self, plugin_id: str, conversation_id: str | None, event: dict) -> None:
        record = dict(event)
        with self._lock:
            conv = self._conv(conversation_id, create=True)
            assert conv is not None
            self._write(conv, {"b": plugin_id, "e": record})
            conv.events.append((plugin_id, record))
            conv.last_activity = time.time()

    def attach_output(
        self,
        plugin_id: str,
        conversation_id: str | None,
        tool_name: str,
        text: str,
    ) -> bool:
        clipped = text[:_MAX_ATTACHED_OUTPUT]
        with self._lock:
            conv = self._conv(conversation_id, create=False)
            if conv is None:
                return False
            if not self._apply_attach(conv, plugin_id, tool_name, clipped):
                return False
            self._write(
                conv, {"b": plugin_id, "a": {"tool": tool_name, "content": clipped}}
            )
            conv.last_activity = time.time()
            return True

    @staticmethod
    def _apply_attach(
        conv: _Conversation, bucket: str, tool_name: Any, content: Any
    ) -> bool:
        if not isinstance(tool_name, str) or not isinstance(content, str):
            return False
        for b, d in reversed(conv.events):
            if (
                b == bucket
                and d.get("type") == "tool_call"
                and d.get("tool") == tool_name
                and d.get("content") is None
            ):
                d["content"] = content
                return True
        return False

    def _write(self, conv: _Conversation, record: dict) -> None:
        self._ensure_dir()
        line = json.dumps(record, separators=(",", ":")) + "\n"
        fd = os.open(conv.wal_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


_PRE_EVENTS_CLAUDE = {"PreToolUse"}


class GuardService:
    """Evaluate hook events for every host, with daemon-held state.

    ``policy`` is a :class:`GuardPolicy` or a callable returning one;
    the default reads the daemon's own environment (P0.6 moves it to
    the daemon config file). ``root`` fixes the plugin root; ``state_dir``
    holds the write-ahead log.
    """

    def __init__(
        self,
        *,
        root: Path | Callable[[], Path] | None = None,
        state_dir: Path | None = None,
        policy: GuardPolicy | Callable[[], GuardPolicy] | None = None,
        on_change: OnChange | None = None,
    ) -> None:
        self.state_dir = (
            Path(state_dir) if state_dir is not None else default_state_dir()
        )
        self._policy = policy if policy is not None else GuardPolicy.from_env
        self.store = SnapshotStore(root, on_change=on_change)
        self.traces = MemoryTraceStore(
            self.state_dir / "wal",
            ttl_seconds=lambda: self.policy().trace_ttl_hours * 3600,
        )
        self.traces.load()
        self._locks_lock = threading.Lock()
        self._conv_locks: dict[str, threading.Lock] = {}

    def policy(self) -> GuardPolicy:
        return self._policy() if callable(self._policy) else self._policy

    def _lock_for(self, conversation_id: str | None) -> threading.Lock:
        key = _safe_conv_id(conversation_id) if conversation_id else "default"
        with self._locks_lock:
            lock = self._conv_locks.get(key)
            if lock is None:
                lock = self._conv_locks[key] = threading.Lock()
            return lock

    # ---- public API ----

    def check(
        self,
        *,
        host: str | None,
        hook_event: str | None = None,
        payload: dict | None = None,
        raw: str | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        """Decide a pre-event and render the host's reply.

        Exactly one of ``payload`` (the parsed hook JSON) or ``raw``
        (the bytes the host wrote to the hook's stdin) is given. The
        result carries the decision and the rendered ``stdout`` and
        ``exit_code`` for the host. ``mode`` is the hook command's
        mode, when the installer wrote one.
        """
        return self._handle(host, hook_event, payload, raw, post=False, mode=mode)

    def observe(
        self,
        *,
        host: str | None,
        hook_event: str | None = None,
        payload: dict | None = None,
        raw: str | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        """Record a post-event (tool output); never gates."""
        return self._handle(host, hook_event, payload, raw, post=True, mode=mode)

    # ---- internals ----

    def _handle(
        self,
        host: str | None,
        hook_event: str | None,
        payload: dict | None,
        raw: str | None,
        *,
        post: bool,
        mode: str | None = None,
    ) -> dict[str, Any]:
        if host == "cursor":
            return self._handle_cursor(hook_event, payload, raw, post=post, mode=mode)
        return self._handle_stdin_protocol(host, payload, raw, post=post, mode=mode)

    @staticmethod
    def _parse_raw(raw: str, label: str) -> tuple[dict | None, str | None]:
        """Parse hook stdin. Returns ``(payload, problem)``; a problem
        is a host protocol error, allowed today and denied from P0.4."""
        if not raw.strip():
            return None, None
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            return None, f"{label}:invalid JSON on stdin: {e}"
        if not isinstance(obj, dict):
            return None, f"{label}:stdin payload must be a JSON object"
        return obj, None

    @staticmethod
    def _result(outcome: GuardOutcome, stdout: str, exit_code: int) -> dict[str, Any]:
        return {
            "allowed": outcome.allowed,
            "reason": outcome.reason,
            "plugin_id": outcome.plugin_id,
            "library_path": outcome.library_path,
            "stdout": stdout,
            "exit_code": exit_code,
        }

    def _evaluate(self, event: dict, mode: str | None = None) -> GuardOutcome:
        policy = self.policy()
        if mode in ("enforce", "observe") and mode != policy.mode:
            policy = replace(policy, mode=mode)
        conversation_id = event.get("conversation_id")
        if not isinstance(conversation_id, str):
            conversation_id = None
        err = io.StringIO()
        with self._lock_for(conversation_id):
            try:
                with redirect_stderr(err):
                    outcome = evaluate(
                        event, store=self.store, traces=self.traces, policy=policy
                    )
            except Exception as e:  # noqa: BLE001 - every failure is a decision
                outcome = _error_outcome(e, "", None, policy.on_error)
        if err.getvalue():
            sys.stderr.write(err.getvalue())
        return outcome

    def _handle_stdin_protocol(
        self,
        host: str | None,
        payload: dict | None,
        raw: str | None,
        *,
        post: bool,
        mode: str | None = None,
    ) -> dict[str, Any]:
        if payload is None:
            payload, problem = self._parse_raw(raw or "", "sponsio plugin guard")
            if payload is None:
                if problem:
                    sys.stderr.write(problem + "\n")
                silent = GuardOutcome(allowed=True, reason=problem or "empty stdin")
                return self._result(silent, "", 0)
        event = dict(payload)
        if host and not (isinstance(event.get("host"), str) and event["host"].strip()):
            event["host"] = host
        if (
            post
            and (event.get("hook_event_name") or "PreToolUse") in _PRE_EVENTS_CLAUDE
        ):
            event["hook_event_name"] = "PostToolUse"
        outcome = self._evaluate(event, mode)
        stdout, code = render_reply(event, outcome)
        return self._result(outcome, stdout, code)

    def _handle_cursor(
        self,
        hook_event: str | None,
        payload: dict | None,
        raw: str | None,
        *,
        post: bool,
        mode: str | None = None,
    ) -> dict[str, Any]:
        from sponsio.integrations.cursor import (
            _record_subagent,
            cursor_event_to_sponsio_event,
            render_cursor_reply,
        )

        hook_event = hook_event or ("postToolUse" if post else "preToolUse")
        if payload is None:
            payload, problem = self._parse_raw(raw or "", "sponsio cursor guard")
            if payload is None:
                if problem:
                    sys.stderr.write(problem + "\n")
                silent = GuardOutcome(allowed=True, reason=problem or "empty stdin")
                return self._result(silent, "", 0)
        if hook_event == "subagentStart":
            _record_subagent(payload)
            return self._result(
                GuardOutcome(allowed=True, reason="subagent recorded"), "", 0
            )
        event = cursor_event_to_sponsio_event(payload, hook_event)
        outcome = self._evaluate(event, mode)
        rewrite_deny = os.environ.get("SPONSIO_CURSOR_REWRITE_DENY", "").lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        stdout, code = render_cursor_reply(
            outcome, hook_event, rewrite_deny=rewrite_deny, cursor_payload=payload
        )
        return self._result(outcome, stdout, code)

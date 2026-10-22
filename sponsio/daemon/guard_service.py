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
  every live conversation. Log records carry the event fields of the
  legacy ``.shield-trace.jsonl`` lines plus ``seq``, the event's place
  in its conversation, which is the order traces are rebuilt in.
* :class:`GuardService` serialises calls per conversation, runs the
  decision, and renders the host's reply, so a client (the shim, the
  OpenClaw plugin, the legacy CLI) only forwards bytes.
"""

from __future__ import annotations

import io
import itertools
import json
import os
import sys
import threading
import time
from collections import OrderedDict
from contextlib import redirect_stderr
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from sponsio.daemon import audit
from sponsio.daemon.config import ConfigFile
from sponsio.guard_stdin import (
    _MAX_ATTACHED_OUTPUT,
    GuardOutcome,
    GuardPolicy,
    LibrarySnapshot,
    _error_outcome,
    _resolve_library,
    _safe_conv_id,
    conversation_key,
    evaluate,
    library_root,
    load_snapshot,
    malformed_outcome,
    parse_hook_input,
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


_GENERATION = itertools.count(1)


@dataclass
class _Conversation:
    key: str
    wal_path: Path
    events: list[tuple[str, dict]] = field(default_factory=list)
    attaches: list[tuple[int, str]] = field(default_factory=list)
    """``(index into events, content)`` per attached output, in order."""
    next_seq: int = 0
    """The ``seq`` the next appended event gets."""
    last_activity: float = field(default_factory=time.time)
    generation: int = field(default_factory=lambda: next(_GENERATION))
    """Distinct per conversation object, so an expired and recreated
    conversation under the same key never looks like the old one."""


Revision = tuple[int, int, int]
"""``(generation, events, attaches)``: what a cached guard was synced to."""


def _to_event(d: dict):
    """An :class:`Event` whose ``ts`` is the record's ``seq``.

    The ``ts`` a guard assigned is its trace length at the time, which
    counts a ``context_update`` the store never records; a later event
    could then share it, and ordering by ``(ts, agent)`` put the two the
    wrong way round. ``seq`` is unique and gap-free per conversation, so
    ``now`` and ``time_since`` count stored steps too.
    """
    from sponsio.models.trace import Event

    seq = d.get("seq")
    return Event(
        ts=seq if isinstance(seq, int) else int(d["ts"]),
        agent=d["agent"],
        event_type=d["type"],
        tool=d.get("tool"),
        key=d.get("key"),
        contains=d.get("contains"),
        to=d.get("to"),
        args=d.get("args"),
        content=d.get("content"),
    )


class MemoryTraceStore:
    """A :class:`~sponsio.guard_stdin.TraceStore` held in memory.

    One conversation per session key (:func:`sponsio.guard_stdin.conversation_key`,
    ``"default"`` when the host sends no id), merged across buckets, the same view
    ``_load_prior_events`` builds from the per-bucket files. Every
    append is written to ``<wal_dir>/<conversation>.jsonl`` before it
    is acknowledged; :meth:`load` replays those files on start.

    Records are one JSON object per line: ``{"b": bucket, "e": event}``
    for an admitted event, ``{"b": bucket, "a": {"tool", "content"}}``
    for an output attached by a post-event. ``event`` has the fields the
    legacy trace lines have (``ts``, ``agent``, ``type``, ``tool``,
    ``args``, and ``content`` once attached) plus ``seq``.

    The daemon is the only writer, so it numbers each conversation's
    events as it admits them: ``seq`` is 0, 1, 2, ... in admission order
    across buckets, survives a restart, and is the order every rebuilt
    trace follows. A logged event without one (written before ``seq``
    existed) is numbered by its place in the log, which is the order it
    was appended in.

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
                event = rec["e"]
                seq = event.get("seq")
                if not isinstance(seq, int) or seq < conv.next_seq:
                    event["seq"] = seq = conv.next_seq
                conv.next_seq = seq + 1
                conv.events.append((bucket, event))
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
        return [e for _i, e in self.records(conversation_id)]

    def records(self, conversation_id: str | None) -> list[tuple[int, Any]]:
        """``(index, Event)`` for every event of the conversation, in the
        order :meth:`events` returns them: by ``seq``, admission order."""
        with self._lock:
            conv = self._conv(conversation_id, create=False)
            if conv is None:
                return []
            raw = list(enumerate(d for _b, d in conv.events))
        out = []
        for i, d in raw:
            try:
                out.append((i, _to_event(d)))
            except (KeyError, ValueError, TypeError):
                continue
        out.sort(key=lambda r: r[1].ts)
        return out

    def revision(self, conversation_id: str | None) -> Revision | None:
        """Where the conversation stands, or ``None`` if it does not exist."""
        with self._lock:
            conv = self._conv(conversation_id, create=False)
            if conv is None:
                return None
            return (conv.generation, len(conv.events), len(conv.attaches))

    def changes_since(
        self, conversation_id: str | None, rev: Revision
    ) -> tuple[list[tuple[int, Any]], list[tuple[int, str]], Revision] | None:
        """Events and attached outputs recorded after ``rev``, plus the
        new revision. ``None`` when ``rev`` is not a past state of this
        conversation (it expired and was recreated)."""
        with self._lock:
            conv = self._conv(conversation_id, create=False)
            if conv is None or conv.generation != rev[0]:
                return None
            if len(conv.events) < rev[1] or len(conv.attaches) < rev[2]:
                return None
            start = rev[1]
            new_events = [(start + k, rec) for k, rec in enumerate(conv.events[start:])]
            new_attaches = list(conv.attaches[rev[2] :])
            now: Revision = (conv.generation, len(conv.events), len(conv.attaches))
        events = []
        for i, (_b, d) in new_events:
            try:
                events.append((i, _to_event(d)))
            except (KeyError, ValueError, TypeError):
                return None
        return events, new_attaches, now

    def append(self, plugin_id: str, conversation_id: str | None, event: dict) -> None:
        record = dict(event)
        with self._lock:
            conv = self._conv(conversation_id, create=True)
            assert conv is not None
            record["seq"] = conv.next_seq
            self._write(conv, {"b": plugin_id, "e": record})
            conv.next_seq += 1
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
        for i in range(len(conv.events) - 1, -1, -1):
            b, d = conv.events[i]
            if (
                b == bucket
                and d.get("type") == "tool_call"
                and d.get("tool") == tool_name
                and d.get("content") is None
            ):
                d["content"] = content
                conv.attaches.append((i, content))
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
# Live guards: one per (bucket, library content, conversation, mode)
# ---------------------------------------------------------------------------

_CONTENT_READING_ATOMS = ("output_has", "llm_said", "prompt_contains")
"""Atoms whose grounding reads an event's ``content``. Only these make an
attached output change past valuations."""


def _reads_content(guard: Any) -> bool:
    try:
        from sponsio.runtime.verifier import _collect_det_formulas
        from sponsio.tracer.grounding import collect_content_atoms

        atoms = collect_content_atoms(_collect_det_formulas(guard._system.contracts))
    except Exception:  # noqa: BLE001 - unknown means assume it does
        return True
    return any(atoms.get(name) for name in _CONTENT_READING_ATOMS)


def _order(ev: Any) -> int:
    """The key :meth:`MemoryTraceStore.records` sorts a history by: the
    event's ``seq``, which the store hands out as ``ts``."""
    return ev.ts


@dataclass
class _Live:
    guard: Any
    rev: Revision
    positions: dict[int, int]
    """Store index of each event -> its position in the guard's trace."""
    reads_content: bool


class LiveGuards:
    """A :class:`~sponsio.guard_stdin.GuardCache` over a
    :class:`MemoryTraceStore`.

    Without it every check builds a guard and grounds the whole session
    again, so a check costs time linear in the session's length. With
    it, a check appends the events recorded since the guard last saw the
    conversation (other buckets' calls, usually none) and grounds only
    the new one.

    The reused guard must decide exactly as a rebuilt one would. It is
    dropped, and the next call rebuilds, whenever that is not certain:
    the conversation expired and was recreated; the store's history and
    the guard's trace disagree; a new event would sort before the
    guard's last one; or an output was attached to a call whose position
    the cache cannot find. An attached output is applied in place; when
    the library has an atom that reads outputs (``output_has``), the
    guard's grounding is reset so it is recomputed with the output, as a
    rebuild would.

    Least recently used guards are evicted past ``capacity``.
    """

    def __init__(self, traces: MemoryTraceStore, *, capacity: int = 128) -> None:
        self._traces = traces
        self.capacity = capacity
        self._lock = threading.Lock()
        self._idle: OrderedDict[tuple, _Live] = OrderedDict()
        self._busy: dict[tuple, _Live] = {}
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._idle) + len(self._busy)

    def checkout(self, key: tuple, conversation_id: str | None) -> Any | None:
        with self._lock:
            live = self._idle.pop(key, None)
        if live is None or not self._sync(live, conversation_id):
            with self._lock:
                self.misses += 1
            return None
        live.guard._violations.clear()
        with self._lock:
            self.hits += 1
            self._busy[key] = live
        return live.guard

    def _sync(self, live: _Live, conversation_id: str | None) -> bool:
        changes = self._traces.changes_since(conversation_id, live.rev)
        if changes is None:
            return False
        events, attaches, now = changes
        trace = live.guard._monitor.trace.events
        if len(trace) != len(live.positions):
            return False
        last = trace[-1] if trace else None
        for idx, ev in events:
            if last is not None and _order(ev) < _order(last):
                return False
            live.positions[idx] = len(trace)
            trace.append(ev)
            last = ev
        regrounding = False
        for idx, content in attaches:
            pos = live.positions.get(idx)
            if pos is None:
                return False
            trace[pos].content = content
            regrounding = regrounding or live.reads_content
        if regrounding:
            live.guard._monitor._verifier.reset()
        live.rev = now
        return True

    def checkin(self, key: tuple, guard: Any, conversation_id: str | None) -> None:
        with self._lock:
            live = self._busy.pop(key, None)
        rev = self._traces.revision(conversation_id)
        if rev is None:
            return
        trace = guard._monitor.trace.events
        if live is None or live.guard is not guard:
            live = self._fresh(guard, rev, conversation_id)
        elif not self._advance(live, rev, trace):
            return
        if live is None:
            return
        with self._lock:
            self._idle[key] = live
            self._idle.move_to_end(key)
            while len(self._idle) > self.capacity:
                self._idle.popitem(last=False)

    def _fresh(
        self, guard: Any, rev: Revision, conversation_id: str | None
    ) -> _Live | None:
        """Adopt a guard built from scratch, if its trace is the store's."""
        records = self._traces.records(conversation_id)
        trace = guard._monitor.trace.events
        if len(records) != len(trace):
            return None
        for (_idx, stored), held in zip(records, trace):
            if (stored.ts, stored.tool, stored.event_type) != (
                held.ts,
                held.tool,
                held.event_type,
            ):
                return None
        positions = {idx: i for i, (idx, _e) in enumerate(records)}
        return _Live(guard, rev, positions, _reads_content(guard))

    @staticmethod
    def _advance(live: _Live, rev: Revision, trace: list) -> bool:
        """Account for the decision: one event admitted, or none."""
        if rev[0] != live.rev[0] or rev[2] != live.rev[2]:
            return False
        if rev[1] == live.rev[1] + 1 and len(trace) == len(live.positions) + 1:
            # A rebuild orders the history by seq, which the store gave
            # the admitted event as the next number. The guard numbered
            # it by its trace length; if that is behind its last event,
            # the trace is not the store's and a rebuild would differ.
            if len(trace) >= 2 and _order(trace[-1]) <= _order(trace[-2]):
                return False
            live.positions[rev[1] - 1] = len(trace) - 1
        elif rev[1] != live.rev[1] or len(trace) != len(live.positions):
            return False
        live.rev = rev
        return True

    def discard(self, key: tuple) -> None:
        with self._lock:
            self._busy.pop(key, None)
            self._idle.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._idle.clear()
            self._busy.clear()


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


_PRE_EVENTS_CLAUDE = {"PreToolUse"}

KNOWN_HOSTS = frozenset({"claude-code", "cursor", "openclaw"})
"""Hosts the daemon renders replies for. A request naming any other host
is denied: the daemon cannot tell what that host would read as a deny.
``None`` (no host named) is the legacy Claude Code protocol."""

FAIL_OPEN = "fail_open"
"""Audit event: a call was allowed only because it could not be
evaluated and the on-error policy is ``allow``."""


class GuardService:
    """Evaluate hook events for every host, with daemon-held state.

    The policy for a call (mode, on-error, unconfigured tools, trace TTL)
    comes from ``<state_dir>/daemon.yaml`` (:mod:`sponsio.daemon.config`),
    per host, and from nothing the client sends: a ``mode`` on the
    request or the hook command is ignored, and no environment variable
    is read. ``policy`` (a :class:`GuardPolicy` or a callable returning
    one) replaces the config for tests and embedders. ``root`` is the
    plugin root unless the config names one; ``state_dir`` holds the
    config, the write-ahead log, the audit log and the proposals.
    """

    def __init__(
        self,
        *,
        root: Path | Callable[[], Path] | None = None,
        state_dir: Path | None = None,
        policy: GuardPolicy | Callable[[], GuardPolicy] | None = None,
        on_change: OnChange | None = None,
        reuse_guards: bool = True,
    ) -> None:
        self.state_dir = (
            Path(state_dir) if state_dir is not None else default_state_dir()
        )
        self.config = ConfigFile(self.state_dir)
        self._policy = policy
        base = root

        def _root() -> Path:
            configured = self.config.current().plugin_root
            if configured is not None:
                return configured
            if base is None:
                return library_root()
            return base() if callable(base) else base

        self.store = SnapshotStore(_root, on_change=on_change)
        self.traces = MemoryTraceStore(
            self.state_dir / "wal",
            ttl_seconds=lambda: self.policy().trace_ttl_hours * 3600,
        )
        self.traces.load()
        self.guards: LiveGuards | None = (
            LiveGuards(self.traces) if reuse_guards else None
        )
        self._locks_lock = threading.Lock()
        self._conv_locks: dict[str, threading.Lock] = {}

    def set_host_mode(self, host: str, mode: str) -> None:
        """Persist ``host``'s mode in the daemon config (``mode.set``)."""
        self.config.set_host_mode(host, mode)

    def policy(self, host: str | None = None) -> GuardPolicy:
        if self._policy is not None:
            return self._policy() if callable(self._policy) else self._policy
        return self.config.current().policy_for(host)

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
        if host is not None and host not in KNOWN_HOSTS:
            outcome = malformed_outcome(f"unknown host {host!r}")
            sys.stderr.write(outcome.reason + "\n")
            stdout, _code = render_reply({}, outcome)
            return self._result(outcome, stdout, 2)
        if host == "cursor":
            return self._handle_cursor(hook_event, payload, raw, post=post, mode=mode)
        return self._handle_stdin_protocol(host, payload, raw, post=post, mode=mode)

    @staticmethod
    def _parse(payload: dict | None, raw: str | None) -> tuple[dict | None, str | None]:
        """The event, from the parsed payload or the raw stdin text."""
        if payload is not None:
            if isinstance(payload, dict):
                return payload, None
            return None, "payload must be a JSON object"
        return parse_hook_input(raw or "")

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
        # ``mode`` is what an older hook command or plugin still sends;
        # the daemon config decides, so it is ignored.
        del mode
        policy = self.policy(event.get("host"))
        conversation_id = conversation_key(event)
        err = io.StringIO()
        with self._lock_for(conversation_id):
            try:
                with redirect_stderr(err):
                    outcome = evaluate(
                        event,
                        store=self.store,
                        traces=self.traces,
                        policy=policy,
                        guards=self.guards,
                    )
            except Exception as e:  # noqa: BLE001 - every failure is a decision
                outcome = _error_outcome(e, "", None, policy.on_error)
        if err.getvalue():
            sys.stderr.write(err.getvalue())
        if outcome.fail_open:
            audit.append(
                self.state_dir,
                FAIL_OPEN,
                host=event.get("host"),
                tool=event.get("tool_name"),
                conversation=conversation_id,
                plugin_id=outcome.plugin_id,
                reason=outcome.reason,
            )
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
        payload, problem = self._parse(payload, raw)
        if payload is None:
            outcome = malformed_outcome(problem or "")
            sys.stderr.write(outcome.reason + "\n")
            name = "PostToolUse" if post else "PreToolUse"
            stdout, _code = render_reply({"hook_event_name": name}, outcome)
            return self._result(outcome, stdout, 2)
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
        payload, problem = self._parse(payload, raw)
        if payload is None:
            outcome = malformed_outcome(problem or "")
            sys.stderr.write(outcome.reason + "\n")
            stdout, code = render_cursor_reply(outcome, hook_event)
            return self._result(outcome, stdout, code)
        if hook_event == "subagentStart":
            _record_subagent(payload)
            return self._result(
                GuardOutcome(allowed=True, reason="subagent recorded"), "", 0
            )
        try:
            event = cursor_event_to_sponsio_event(payload, hook_event)
        except Exception as e:  # noqa: BLE001 - an unconvertible event is denied
            outcome = malformed_outcome(f"cannot convert the {hook_event} event: {e}")
            stdout, code = render_cursor_reply(outcome, hook_event)
            return self._result(outcome, stdout, code)
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

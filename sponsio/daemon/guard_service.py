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

import contextlib
import io
import itertools
import json
import os
import sys
import threading
import time
from collections import OrderedDict
from contextlib import redirect_stderr
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from sponsio.daemon import audit
from sponsio.daemon.config import ConfigFile
from sponsio.daemon.integrity import (
    CLOUD_DRIFT,
    CLOUD_VERIFIED,
    IDS_ALGO,
    SELF_PROTECTION_PACKS,
    TAMPER_ATTEMPT,
    TRACE_DELETED,
    IntegrityWatcher,
    RulePolicy,
    RulesetDiff,
    agent_ruleset,
    engine_files,
)
from sponsio.guard_stdin import (
    _MAX_ATTACHED_OUTPUT,
    GuardOutcome,
    GuardPolicy,
    LibrarySnapshot,
    ProjectLayer,
    _error_outcome,
    _resolve_library,
    _safe_conv_id,
    allowed_tools,
    conversation_key,
    derive_plugin_id,
    evaluate,
    find_project_layer,
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
    unsigned: bool = False
    """The loaded content failed its signature check."""
    placeholder: bool = False
    """``served`` is a signature hold, not rules that passed."""
    signing: Any = None
    """The signing requirement the entry was checked under (the key, or
    ``None``); an entry checked under another one is not reused."""
    missing: bool = False
    """The rule file is gone and ``served`` is the last signed rules."""
    unsigned_reason: str | None = None
    """Why what is on disk is not what is served: its signature check
    failed, or the file is gone."""


def _load_recorded(
    plugin_id: str, lib_path: Path, agent_id: str
) -> tuple[LibrarySnapshot, dict[str, bytes], dict[str, tuple]]:
    """:func:`load_snapshot`, keeping the bytes of every file it parsed and
    each file's ``(mtime_ns, size)`` when it was read, by real path."""
    from sponsio.config import _FILE_READER

    contents: dict[str, bytes] = {}
    stats: dict[str, tuple] = {}

    def reader(path: Path) -> str:
        with open(path, "rb") as fh:
            st = os.fstat(fh.fileno())
            data = fh.read()
        real = os.path.realpath(path)
        contents[real] = data
        stats[real] = (st.st_mtime_ns, st.st_size)
        return data.decode("utf-8")

    token = _FILE_READER.set(reader)
    try:
        snap = load_snapshot(plugin_id, lib_path, agent_id)
    finally:
        _FILE_READER.reset(token)
    return snap, contents, stats


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
        signature: Callable[[str, tuple[str, ...], dict[str, bytes]], str | None]
        | None = None,
        on_unsigned: Callable[[str, str, bool], None] | None = None,
        signing_state: Callable[[str], Any] | None = None,
        known: Callable[[str], bool] | None = None,
    ) -> None:
        self._root = root if root is not None else library_root
        self._on_change: OnChange = on_change or (lambda _old, new: new)
        self._signature = signature
        """``(plugin_id, files, contents) -> reason`` when the files must be
        signed and the bytes parsed (``contents``, by real path) are not;
        ``None`` when they pass or need no signature."""
        self._on_unsigned = on_unsigned
        """Told ``(plugin_id, reason, kept_previous)`` when a load fails
        its signature check."""
        self._signing_state = signing_state
        """``plugin_id -> state``: ``None`` when the bucket needs no
        signature, otherwise what it is checked against (the key)."""
        self._known = known
        """Whether rules for a bucket were applied before (an approved
        ruleset is on record), so a missing file is a removal."""
        self._lock = threading.Lock()
        self._cache: dict[str, _CacheEntry] = {}
        self._held_missing: set[str] = set()

    @property
    def root(self) -> Path:
        return self._root() if callable(self._root) else self._root

    def resolve(self, plugin_id: str) -> LibrarySnapshot | None:
        root = self.root
        lib_path, agent_id = _resolve_library(plugin_id, root)
        signing = (
            self._signing_state(plugin_id)
            if self._signing_state is not None and self._signature is not None
            else None
        )
        with self._lock:
            entry = self._cache.get(plugin_id)
            if entry is not None and entry.signing != signing:
                # Signing turned on, or the key changed: nothing checked
                # under the old requirement is kept.
                self._cache.pop(plugin_id, None)
                entry = None
            if not lib_path.exists():
                if signing is not None:
                    return self._missing_signed(plugin_id, lib_path, agent_id, entry)
                self._cache.pop(plugin_id, None)
                return None
            self._held_missing.discard(plugin_id)
            if (
                entry is not None
                and entry.path == str(lib_path)
                and entry.stats == _stat_signature(entry.files)
            ):
                return entry.served
            if signing is None:
                snap = load_snapshot(plugin_id, lib_path, agent_id)
                contents: dict[str, bytes] = {}
                read_stats: dict[str, tuple] = {}
            else:
                snap, contents, read_stats = _load_recorded(
                    plugin_id, lib_path, agent_id
                )
            files = (
                tuple(snap.config.source_files)
                if snap.config is not None and snap.config.source_files
                else (str(lib_path),)
            )
            unsigned = None
            if signing is not None:
                from sponsio.daemon.signing import SIG_SUFFIX

                # Every file parsed is checked, including the ones read
                # before a parse failure, and against the bytes parsed. The
                # bucket's own file is named as it sits in the plugin root,
                # so a link from there into the engine is not taken for a
                # pack that ships with it.
                files = tuple(dict.fromkeys((str(lib_path), *files, *contents)))
                unsigned = self._signature(plugin_id, files, contents)
                if unsigned is None and snap.error is not None:
                    # A file that failed to resolve or parse was never read,
                    # so nothing proves the failure is in signed content.
                    unsigned = f"they do not load: {snap.error}"
                # A signature added or replaced must be seen as a change.
                files = files + tuple(f + SIG_SUFFIX for f in files)
            stats = tuple(
                read_stats.get(os.path.realpath(f), now)
                for f, now in zip(files, _stat_signature(files))
            )
            if unsigned is not None:
                # Keep serving the last rules that passed; with none, the
                # bucket is held, and nothing of the unsigned file decides.
                keep = entry is not None and not entry.placeholder
                served = (
                    entry.served
                    if keep
                    else replace(
                        snap,
                        config=None,
                        error=None,
                        held_reason=(
                            f"Sponsio: the rules for {plugin_id} cannot be "
                            f"verified against the configured key ({unsigned}), "
                            f"so this call is denied. Signed rules apply on the "
                            f"next call."
                        ),
                    )
                )
                self._cache[plugin_id] = _CacheEntry(
                    path=str(lib_path),
                    files=files,
                    stats=stats,
                    loaded=snap,
                    served=served,
                    unsigned=True,
                    placeholder=not keep,
                    signing=signing,
                    unsigned_reason=unsigned,
                )
                if self._on_unsigned is not None:
                    self._on_unsigned(plugin_id, unsigned, keep)
                return served
            if (
                entry is not None
                and entry.path == str(lib_path)
                and entry.loaded.content_hash == snap.content_hash
                and not entry.unsigned
                and not entry.missing
            ):
                # Touched but not changed (a save with identical bytes).
                entry.stats = stats
                return entry.served
            # A signature hold is not a ruleset to compare with; the
            # policy compares with the last approved one instead.
            old = entry.served if entry is not None and not entry.placeholder else None
            served = self._on_change(old, snap)
            self._cache[plugin_id] = _CacheEntry(
                path=str(lib_path),
                files=files,
                stats=stats,
                loaded=snap,
                served=served,
                signing=signing,
            )
            return served

    def _missing_signed(
        self,
        plugin_id: str,
        lib_path: Path,
        agent_id: str,
        entry: _CacheEntry | None,
    ) -> LibrarySnapshot | None:
        """A covered bucket with no rule file. Nothing signed says the bucket
        has no rules, and a removal looks the same as a bucket that never
        had a file: the last signed rules keep enforcing, and with none
        loaded the bucket is held, whatever ``unconfigured`` says. Called
        with the lock held."""
        if entry is not None and not entry.placeholder:
            if not entry.missing:
                entry.missing = True
                entry.unsigned_reason = f"{lib_path.name} was removed"
                if self._on_unsigned is not None:
                    self._on_unsigned(plugin_id, f"{lib_path.name} was removed", True)
            return entry.served
        self._cache.pop(plugin_id, None)
        removed = self._known is not None and self._known(plugin_id)
        reason = (
            f"{lib_path.name} was removed"
            if removed
            else f"there is no signed {lib_path.name}"
        )
        if plugin_id not in self._held_missing:
            self._held_missing.add(plugin_id)
            if self._on_unsigned is not None:
                self._on_unsigned(plugin_id, reason, False)
        return LibrarySnapshot(
            plugin_id=plugin_id,
            agent_id=agent_id,
            path=str(lib_path),
            content_hash="",
            held_reason=(
                f"Sponsio: the rules for {plugin_id} must be signed by the "
                f"configured key and {reason}, so this call is denied. Signed "
                f"rules apply on the next call."
            ),
        )

    def bucket_ids(self) -> list[str]:
        with self._lock:
            return list(self._cache)

    def unsigned(self) -> dict[str, str]:
        """Buckets whose rule file on disk is not what is served, because
        it failed its signature check or is gone, with why. Each serves
        the last signed rules, or is held when there are none."""
        with self._lock:
            return {
                pid: e.unsigned_reason
                for pid, e in self._cache.items()
                if e.unsigned_reason
            }

    def snapshots(self) -> list[LibrarySnapshot]:
        """What each cached bucket serves."""
        with self._lock:
            return [e.served for e in self._cache.values()]

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
    wal_size: int = 0
    """Bytes this store has in the log; a log found shorter (or gone)
    was cut by someone else."""
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

    A log that is gone or shorter than what this store wrote, when the
    next record is due, was deleted or truncated by someone else: the
    session's history is still in memory, so the log is rewritten from
    it and ``on_tamper(conversation, problem)`` is called.
    """

    def __init__(
        self,
        wal_dir: Path,
        *,
        ttl_seconds: float | Callable[[], float] = 24 * 3600,
        on_tamper: Callable[[str, str], None] | None = None,
    ) -> None:
        self.wal_dir = Path(wal_dir)
        self._on_tamper = on_tamper
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
                try:
                    conv.wal_size = path.stat().st_size
                except OSError:
                    conv.wal_size = 0
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
        if conv.wal_size:
            try:
                size = conv.wal_path.stat().st_size
            except FileNotFoundError:
                size = -1
            if size < conv.wal_size:
                problem = "deleted" if size < 0 else f"truncated to {size} bytes"
                self._rewrite(conv)
                if self._on_tamper is not None:
                    self._on_tamper(conv.key, problem)
        line = json.dumps(record, separators=(",", ":")) + "\n"
        data = line.encode("utf-8")
        fd = os.open(conv.wal_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        conv.wal_size += len(data)

    def _rewrite(self, conv: _Conversation) -> None:
        """The log again, from memory: every event, outputs included."""
        lines = [
            json.dumps({"b": b, "e": d}, separators=(",", ":")) + "\n"
            for b, d in conv.events
        ]
        data = "".join(lines).encode("utf-8")
        tmp = conv.wal_path.with_suffix(".rewrite")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        os.replace(tmp, conv.wal_path)
        conv.wal_size = len(data)


# ---------------------------------------------------------------------------
# Live guards: one per (bucket, library content, conversation, mode)
# ---------------------------------------------------------------------------

_CONTENT_READING_ATOMS = ("output_has", "llm_said", "prompt_contains")
"""Atoms whose grounding reads an event's ``content``. Only these make an
attached output change past valuations."""


def _empty_trace():
    from sponsio.models.trace import Trace

    return Trace(events=[])


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
        watch: bool = False,
        run_dir: Path | None = None,
    ) -> None:
        self.state_dir = (
            Path(state_dir) if state_dir is not None else default_state_dir()
        )
        # How a changed library is served (P0.7, Q1): strengthening
        # applies, weakening is held until `sponsio rules reload`.
        # ``watch`` applies everything, still audited. An explicit
        # ``on_change`` replaces the policy (tests).
        self.rules = RulePolicy(
            self.state_dir, watch=watch, held_snapshot=self._held_snapshot
        )
        self._watcher: IntegrityWatcher | None = None
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

        self.store = SnapshotStore(
            _root,
            on_change=on_change or self.rules,
            signature=self._unsigned,
            on_unsigned=self._audit_unsigned,
            signing_state=self._signing_state,
            known=lambda bucket: self.rules.approved.get(bucket) is not None,
        )
        self.traces = MemoryTraceStore(
            self.state_dir / "wal",
            ttl_seconds=lambda: self.policy().trace_ttl_hours * 3600,
            on_tamper=self._trace_tampered,
        )
        self.traces.load()
        self.guards: LiveGuards | None = (
            LiveGuards(self.traces) if reuse_guards else None
        )
        self._locks_lock = threading.Lock()
        self._conv_locks: dict[str, threading.Lock] = {}
        self._tamper_guards: dict[str, Any] = {}
        self._tamper_packs: dict[str, set[str]] = {}
        self._project_reported: set[tuple[str, ...]] = set()
        self._cloud_status: dict[str, dict[str, Any]] = {}
        self._cloud_stop: threading.Event | None = None
        # The proposal store the daemon's handlers serve, set when they
        # are registered; cloud proposal sync decides through it.
        self.proposals: Any = None
        self._proposal_refused: set[str] = set()
        self._proposal_status: dict[str, Any] = {}
        self._gateway: Any = None
        self._gateway_lock = threading.Lock()
        self.run_dir = Path(run_dir) if run_dir is not None else self.state_dir
        """Where sockets every user may reach go (the agent socket's
        directory); the egress proxy's sockets live here."""
        self._egress: Any = None

    @staticmethod
    def _held_snapshot(new: LibrarySnapshot, diff: RulesetDiff) -> LibrarySnapshot:
        return replace(
            new,
            held_reason=(
                f"Sponsio: the rules for {new.plugin_id} on disk are weaker than "
                f"the last approved rules ({diff.summary(limit=2)}), so this call "
                f"is denied until they are reviewed. From your own terminal: "
                f"`sponsio rules diff`, then `sponsio rules reload` to accept them."
            ),
        )

    def _signing_state(self, plugin_id: str) -> str | None:
        cfg = self.config.current()
        if not cfg.requires_signature(plugin_id):
            return None
        return cfg.signing_public_key

    def _unsigned(
        self, plugin_id: str, files: tuple[str, ...], contents: dict[str, bytes]
    ) -> str | None:
        key = self._signing_state(plugin_id)
        if key is None:
            return None
        from sponsio.daemon.signing import unsigned_reason

        return unsigned_reason(files, key, contents)

    def _audit_unsigned(self, plugin_id: str, reason: str, kept: bool) -> None:
        from sponsio.daemon.signing import RULES_UNSIGNED

        audit.append(
            self.state_dir,
            RULES_UNSIGNED,
            bucket=plugin_id,
            reason=reason,
            kept_previous=kept,
        )
        if kept:
            self.rules.warn(
                f"Sponsio: the rules for {plugin_id} on disk are not signed by the "
                f"configured key ({reason}); the last signed rules keep enforcing."
            )

    # ---- the daemon's own cloud key (P1.5) ----

    def cloud_key_path(self) -> Path:
        return self.state_dir / "cloud.key"

    def cloud_api_key(self) -> tuple[str | None, str | None]:
        """``(key, source)``: the daemon's own key in its state dir, else
        (Tier 1, where the daemon is the user) the user's ``sponsio
        login`` credentials. Never an environment variable."""
        from sponsio.cloud.client import credentials_path, read_api_key_file

        key = read_api_key_file(self.cloud_key_path())
        if key:
            return key, "daemon"
        key = read_api_key_file(credentials_path())
        return (key, "user") if key else (None, None)

    def set_cloud_key(self, key: str, peer: dict | None) -> dict[str, Any]:
        import hashlib
        import tempfile

        path = self.cloud_key_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".cloud.key.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(f"api_key = {key}\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        fingerprint = hashlib.sha256(key.encode()).hexdigest()[:12]
        audit.append(
            self.state_dir, "cloud_key_set", fingerprint=fingerprint, peer=peer
        )
        return {"fingerprint": fingerprint, "path": str(path)}

    def _trace_tampered(self, conversation: str, problem: str) -> None:
        audit.append(
            self.state_dir, TRACE_DELETED, conversation=conversation, problem=problem
        )
        self.rules.warn(
            f"Sponsio: this session's trace log was {problem}; it was restored "
            f"from the daemon's memory and the event is on record."
        )

    # ---- tamper evidence ----

    def watched_files(self) -> list[Path]:
        """Every source file of a loaded library, and the engine modules."""
        import sponsio

        files: list[Path] = []
        for snap in self.store.snapshots():
            config = snap.config
            sources = getattr(config, "source_files", None) or (snap.path,)
            files.extend(Path(p) for p in sources)
        files.extend(engine_files(Path(sponsio.__file__).parent))
        return files

    def start_watch(
        self, interval_s: float = 2.0, *, start: bool = True
    ) -> IntegrityWatcher:
        """Poll the watched files for changes (``sponsio daemon run``).

        Every change is recorded. Only an engine module's change is also
        announced on the next hook reply: a rule file's change is the rule
        policy's to announce, and it does so only when the change is held
        (a strengthening applies with nothing to act on).
        """
        import sponsio

        engine = {str(p) for p in engine_files(Path(sponsio.__file__).parent)}

        def on_drift(rec: dict[str, Any]) -> None:
            if rec.get("file") in engine:
                self.rules.warn(
                    f"Sponsio: {rec.get('file')} changed on disk; it is on record."
                )

        self._watcher = IntegrityWatcher(
            self.state_dir, self.watched_files, interval_s=interval_s, on_drift=on_drift
        )
        if start:
            self._watcher.start()
        return self._watcher

    def stop_watch(self) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None

    # ---- the published book (P0.7 step 4) ----

    def verify_cloud(self, client: Any = None) -> dict[str, dict[str, Any]]:
        """Compare each linked bucket's served rules with the book the
        cloud publishes for its agent (``cloud:`` in daemon.yaml).

        Never on the decision path; a failure is a status, not a deny.
        A change of outcome is audited (``cloud_drift`` when weaker,
        ``cloud_verified`` otherwise), and weaker rules are announced on
        the next hook reply.
        """
        import socket as _socket

        import sponsio
        from sponsio.cloud.client import CloudClient, CloudError

        cfg = self.config.current()
        if not cfg.cloud_buckets:
            return {}
        if client is None:
            key, _source = self.cloud_api_key()
            client = CloudClient(api_key=key or "", timeout=10.0, retries=0)
        results: dict[str, dict[str, Any]] = {}
        for bucket, agent in sorted(cfg.cloud_buckets.items()):
            if not client.configured:
                result: dict[str, Any] = {"status": "no_key"}
            else:
                snap = self.store.resolve(bucket)
                if snap is None or snap.config is None:
                    result = {"status": "not_loaded"}
                else:
                    try:
                        result = client.verify_rulebook(
                            cfg.cloud_project,
                            agent=agent,
                            ids=sorted(agent_ruleset(snap.config, snap.agent_id)),
                            ids_algo=IDS_ALGO,
                            sponsio_version=getattr(sponsio, "__version__", None),
                            machine=_socket.gethostname(),
                            bucket=bucket,
                        )
                    except CloudError as e:
                        result = {
                            "status": "unreachable" if e.status is None else "error",
                            "http_status": e.status,
                            "detail": str(e),
                        }
            results[bucket] = result
            prev = self._cloud_status.get(bucket, {}).get("status")
            self._cloud_status[bucket] = result
            if result.get("status") == prev:
                continue
            weaker = result.get("status") == "weaker"
            audit.append(
                self.state_dir,
                CLOUD_DRIFT if weaker else CLOUD_VERIFIED,
                bucket=bucket,
                agent=agent,
                project=cfg.cloud_project,
                status=result.get("status"),
                published_version=result.get("published_version"),
                missing=result.get("missing", []),
            )
            if weaker:
                self.rules.warn(
                    f"Sponsio: the rules for {bucket} lack "
                    f"{len(result.get('missing', []))} contract(s) the cloud publishes "
                    f"for {agent} (v{result.get('published_version')}); "
                    f"run `sponsio rules verify` from your own terminal."
                )
        return results

    def sync_proposals(self, client: Any = None) -> dict[str, Any]:
        """Upload proposals and apply what a person decided in the cloud
        console (``cloud: {proposals: true}``). See
        :mod:`sponsio.daemon.proposal_sync`."""
        from sponsio.cloud.client import CloudClient, CloudError
        from sponsio.daemon.proposal_sync import sync_once

        cfg = self.config.current()
        if not cfg.cloud_proposals or self.proposals is None:
            return {"status": "off"}
        if client is None:
            key, _source = self.cloud_api_key()
            client = CloudClient(api_key=key or "", timeout=10.0, retries=0)
        if not client.configured:
            result: dict[str, Any] = {"status": "no_key"}
        else:
            try:
                result = {
                    "status": "ok",
                    **sync_once(
                        self.proposals,
                        client,
                        project=cfg.cloud_project,
                        refused=self._proposal_refused,
                    ),
                }
            except CloudError as e:
                result = {
                    "status": "unreachable" if e.status is None else "error",
                    "http_status": e.status,
                    "detail": str(e),
                }
        self._proposal_status = result
        return result

    def start_cloud_verify(self) -> None:
        """Verify (and sync proposals) every ``cloud_verify_interval_s`` on
        a background thread."""
        stop = threading.Event()
        self._cloud_stop = stop

        def loop() -> None:
            while True:
                try:
                    self.verify_cloud()
                except Exception as e:  # noqa: BLE001 - the loop must not die
                    sys.stderr.write(f"sponsio: cloud verification failed: {e}\n")
                try:
                    self.sync_proposals()
                except Exception as e:  # noqa: BLE001 - the loop must not die
                    sys.stderr.write(f"sponsio: proposal sync failed: {e}\n")
                if stop.wait(self.config.current().cloud_verify_interval_s):
                    return

        threading.Thread(target=loop, name="sponsio-cloud-verify", daemon=True).start()

    def stop_cloud_verify(self) -> None:
        if self._cloud_stop is not None:
            self._cloud_stop.set()
            self._cloud_stop = None

    def rules_status(self) -> dict[str, Any]:
        # What is on disk now, not as of the last call that happened to
        # load each bucket: an operator checking a machine has usually
        # just changed something, and a reload empties the cache.
        held = self.rules.status()
        for bucket in {*self.store.bucket_ids(), *self.rules.approved.buckets(), *held}:
            try:
                self.store.resolve(bucket)
            except Exception:  # noqa: BLE001 - a bucket that will not load still reports
                pass
        loaded = {}
        unsigned = self.store.unsigned()
        for snap in self.store.snapshots():
            loaded[snap.plugin_id] = {
                "path": snap.path,
                "content_hash": snap.content_hash,
                "held": snap.held_reason is not None,
                # The file on disk is not what is enforced: an edit made on
                # the machine to a signed bucket lands here, not in `held`.
                "unsigned": unsigned.get(snap.plugin_id),
            }
        return {
            "loaded": loaded,
            "held": self.rules.status(),
            "watch": self.rules.watch,
            "cloud": dict(self._cloud_status),
            "proposals": dict(self._proposal_status),
        }

    def rules_reload(self, bucket: str | None, peer: dict | None) -> dict[str, Any]:
        done = self.rules.reload(bucket, peer)
        self.store.invalidate(bucket)
        if self.guards is not None:
            self.guards.clear()
        return {"reloaded": [r.get("bucket") for r in done]}

    def _audit_tamper(self, event: dict, outcome: GuardOutcome) -> None:
        """A denial by a self-protection pack is an attempted tamper (D8).

        Violations do not say which pack a contract came from, so a deny
        is checked again against a guard built from the bucket's
        self-protection contracts alone (stateless argument rules, built
        once per library content). Only denies pay for this.
        """
        if outcome.allowed or not outcome.plugin_id:
            return
        snap = self.store.loaded(outcome.plugin_id)
        config = getattr(snap, "config", None)
        if config is None:
            return
        guard = self._self_protection_guard(snap)
        if guard is None:
            return
        tool = event.get("tool_name") or ""
        args = (
            event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
        )
        try:
            with (
                redirect_stderr(io.StringIO()),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                guard._monitor.import_trace(_empty_trace())
                result = guard.guard_before(tool_name=tool, args=args)
        except Exception:  # noqa: BLE001 - evidence must not break a decision
            return
        if not result.stop_original:
            return
        audit.append(
            self.state_dir,
            TAMPER_ATTEMPT,
            host=event.get("host"),
            tool=tool,
            bucket=outcome.plugin_id,
            packs=sorted(self._tamper_packs.get(snap.content_hash, ())),
            reason=outcome.reason,
        )

    def _self_protection_guard(self, snap: LibrarySnapshot) -> Any | None:
        key = snap.content_hash
        if key in self._tamper_guards:
            return self._tamper_guards[key]
        import copy

        from sponsio.integrations.base import BaseGuard

        config = copy.deepcopy(snap.config)
        packs: set[str] = set()
        for agent in config.agents.values():
            agent.contracts = [
                c for c in agent.contracts if c.pack_source in SELF_PROTECTION_PACKS
            ]
            packs.update(c.pack_source for c in agent.contracts)
        guard = None
        if packs:
            try:
                with (
                    redirect_stderr(io.StringIO()),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    guard = BaseGuard(
                        agent_id=snap.agent_id,
                        parsed_config=config,
                        verbose=False,
                        verbosity=0,
                        mode="enforce",
                        env_mode=False,
                        init_banner=False,
                        auto_summary=False,
                    )
            except Exception:  # noqa: BLE001
                guard = None
        self._tamper_guards[key] = guard
        self._tamper_packs[key] = packs
        return guard

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
    def _result(
        outcome: GuardOutcome, stdout: str, exit_code: int, warning: str | None = None
    ) -> dict[str, Any]:
        out = {
            "allowed": outcome.allowed,
            "reason": outcome.reason,
            "plugin_id": outcome.plugin_id,
            "library_path": outcome.library_path,
            "stdout": stdout,
            "exit_code": exit_code,
        }
        if warning:
            # Written to the hook's stderr by the shim; a deny also
            # carries it in its reason.
            out["stderr"] = warning
        return out

    def _evaluate_warned(
        self, event: dict, mode: str | None = None
    ) -> tuple[GuardOutcome, str | None]:
        """The decision, plus the drift warning pending for the next reply
        (appended to a deny's reason, where every host shows it)."""
        outcome = self._evaluate(event, mode, project=self._project_layer(event))
        warning = self.rules.take_warning()
        if warning and not outcome.allowed:
            outcome = replace(outcome, reason=f"{outcome.reason} [{warning}]")
        return outcome, warning

    def _evaluate(
        self,
        event: dict,
        mode: str | None = None,
        *,
        offered_only: bool = False,
        library: str | None = None,
        project: ProjectLayer | None = None,
        record: bool = True,
    ) -> GuardOutcome:
        # ``mode`` is what an older hook command or plugin still sends;
        # the daemon config decides, so it is ignored.
        del mode
        host = event.get("host")
        policy = self.policy(host)
        conversation_id = conversation_key(event)
        err = io.StringIO()
        with self._lock_for(conversation_id):
            try:
                with redirect_stderr(err):
                    tool = event.get("tool_name") or ""
                    if offered_only and not self._offered(
                        tool, host, conversation_id, policy, library
                    ):
                        # The gateway lists only these tools; a call by
                        # name to one it did not list is refused even
                        # where the call's arguments would pass.
                        outcome = GuardOutcome(
                            allowed=False,
                            reason=(
                                f"Sponsio: `{tool}` is not available in this "
                                f"session's current state."
                            ),
                            plugin_id=library or derive_plugin_id(tool, host=host),
                        )
                    else:
                        outcome = evaluate(
                            event,
                            store=self.store,
                            traces=self.traces,
                            policy=policy,
                            guards=self.guards,
                            library=library,
                            project=project,
                            record=record,
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
        self._audit_tamper(event, outcome)
        self._audit_project(event, project, outcome)
        return outcome

    def _project_layer(self, event: dict) -> ProjectLayer | None:
        """The project layer of a host hook's event, read as the agent's
        user would own it (the connection's peer; this process's user
        outside a request)."""
        from sponsio.daemon.server import current_peer

        peer = current_peer()
        uid = peer.uid if peer is not None else os.getuid()
        try:
            return find_project_layer(event, uid)
        except Exception as e:  # noqa: BLE001 - a missing layer only adds nothing
            sys.stderr.write(f"sponsio: project layer lookup failed: {e}\n")
            return None

    def _audit_project(
        self, event: dict, project: ProjectLayer | None, outcome: GuardOutcome
    ) -> None:
        """A project layer that tried to weaken the protected library, or
        that is not the agent's own file, is an attempted tamper (D5).
        Recorded once per file content, not on every call."""
        if project is None:
            return
        if project.refused is not None:
            key: tuple[str, ...] = ("refused", project.path, project.refused)
            what = [f"{project.path} is ignored: {project.refused}"]
        elif outcome.project_dropped:
            key = ("dropped", project.path, project.digest)
            what = list(outcome.project_dropped)
        else:
            return
        if key in self._project_reported:
            return
        if len(self._project_reported) > 4096:
            self._project_reported.clear()
        self._project_reported.add(key)
        for line in what:
            sys.stderr.write(f"sponsio: project layer: {line}\n")
        audit.append(
            self.state_dir,
            TAMPER_ATTEMPT,
            host=event.get("host"),
            source="project_layer",
            path=project.path,
            dropped=what,
        )

    def _offered(
        self,
        tool: str,
        host: Any,
        conversation_id: str | None,
        policy: GuardPolicy,
        library: str | None = None,
    ) -> bool:
        return bool(
            allowed_tools(
                [tool],
                host=host if isinstance(host, str) else None,
                conversation_id=conversation_id,
                store=self.store,
                traces=self.traces,
                policy=policy,
                guards=self.guards,
                library=library,
            )
        )

    def allowed_tools(
        self,
        host: str,
        conversation_id: str,
        names: list[str],
        library: str | None = None,
    ) -> list[str]:
        """The tools in ``names`` a call to which the conversation's current
        state admits (:func:`sponsio.guard_stdin.allowed_tools`)."""
        with self._lock_for(conversation_id):
            with redirect_stderr(io.StringIO()):
                return allowed_tools(
                    names,
                    host=host,
                    conversation_id=conversation_id,
                    store=self.store,
                    traces=self.traces,
                    policy=self.policy(host),
                    guards=self.guards,
                    library=library,
                )

    def decide(
        self,
        event: dict,
        *,
        offered_only: bool = False,
        library: str | None = None,
        record: bool = True,
    ) -> GuardOutcome:
        """Decide a Sponsio-shaped event (``tool_name``, ``tool_input``,
        ``hook_event_name``, ``host``, ``session_id``) for a caller that is
        not a host hook. ``offered_only`` also refuses a tool that
        :meth:`allowed_tools` would not list; ``library`` names the
        library instead of deriving it from the tool name. With
        ``record=False`` an admitted call is added to the history only by
        :meth:`commit`, once the caller knows it ran."""
        return self._evaluate(
            event, offered_only=offered_only, library=library, record=record
        )

    def commit(self, outcome: GuardOutcome, conversation_id: str) -> None:
        """Add a call decided with ``record=False`` to its history."""
        if not outcome.allowed or outcome.pending is None:
            return
        with self._lock_for(conversation_id):
            self.traces.append(outcome.plugin_id, conversation_id, outcome.pending)

    @property
    def gateway(self) -> Any:
        """The MCP gateway (:mod:`sponsio.daemon.gateway`), started on
        first use."""
        with self._gateway_lock:
            if self._gateway is None:
                from sponsio.daemon.gateway import Gateway

                self._gateway = Gateway(self)
            return self._gateway

    def close_gateway(self) -> None:
        with self._gateway_lock:
            gateway, self._gateway = self._gateway, None
            egress, self._egress = self._egress, None
        if gateway is not None:
            gateway.close()
        if egress is not None:
            egress.close_all()

    @property
    def egress(self) -> Any:
        """The egress proxy (:mod:`sponsio.daemon.egress`), on first use."""
        with self._gateway_lock:
            if self._egress is None:
                from sponsio.daemon.egress import Egress

                self._egress = Egress(self, self.run_dir)
            return self._egress

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
        outcome, warning = self._evaluate_warned(event, mode)
        stdout, code = render_reply(event, outcome)
        return self._result(outcome, stdout, code, warning)

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
        outcome, warning = self._evaluate_warned(event, mode)
        rewrite_deny = os.environ.get("SPONSIO_CURSOR_REWRITE_DENY", "").lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        stdout, code = render_cursor_reply(
            outcome, hook_event, rewrite_deny=rewrite_deny, cursor_payload=payload
        )
        return self._result(outcome, stdout, code, warning)

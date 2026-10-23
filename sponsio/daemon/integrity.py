"""Tamper evidence: what the daemon enforces, and what changed under it.

At Tier 1 the agent shares the daemon's OS user, so it can edit rule
files and engine modules. This module does not stop that; it makes it
visible and keeps it from silently weakening enforcement:

* Every compiled contract gets a stable id (:func:`ruleset`), a hash of
  its agent, source layer (``pack_source``), formula, strategy and
  parameters. Settings that change decisions without being contracts
  (``defaults``, ``tools``, ``tool_policy``, ``runtime``) get ids too.
* :class:`RulePolicy` decides what a changed library serves. A new
  ruleset that keeps every loaded id is applied and audited as
  ``ruleset_strengthened``. One that drops or changes any id is held:
  the old snapshot keeps serving, a ``config_drift`` record lists what
  would go, and only ``sponsio rules reload`` (admin socket) applies it.
  Each applied ruleset is recorded under ``<state>/rules/``, so a daemon
  that restarts onto a library weaker than the last approved one denies
  that bucket's calls until a reload, instead of enforcing less.
* :class:`IntegrityWatcher` polls the library files, their packs and the
  engine modules on the hook path (mtime, then hash, every 2 s) and
  writes ``config_drift`` for each file that changed.

Records go to the daemon's audit log; the next hook reply carries a
one-line warning (:meth:`RulePolicy.take_warning`).
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from sponsio.daemon import audit

_logger = logging.getLogger("sponsio.daemon.integrity")

CONFIG_DRIFT = "config_drift"
RULESET_STRENGTHENED = "ruleset_strengthened"
RULESET_RELOADED = "ruleset_reloaded"
TRACE_DELETED = "trace_deleted"
TAMPER_ATTEMPT = "tamper_attempt"

IDS_ALGO = "sponsio-ruleset-v1"
"""Name of the id algorithm, for anything that compares id sets made by
another process (or, later, the cloud)."""

SELF_PROTECTION_PACKS = (
    "sponsio:capability/self-modify",
    "sponsio:capability/host-config-integrity",
)
"""Packs whose denials are tamper attempts (D8)."""

ENGINE_PATHS = (
    "guard_stdin.py",
    "config.py",
    "core.py",
    "hook_client.py",
    "runtime",
    "patterns",
    "formulas",
    "tracer",
    "integrations",
    "daemon",
    "shim",
)
"""Engine modules on the hook path, relative to the ``sponsio`` package."""

_SETTINGS = ("defaults", "tools", "tool_policy", "runtime", "top_level_mode")


def _canonical(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: _canonical(getattr(value, f.name))
            for f in dataclasses.fields(value)
        }
    if isinstance(value, dict):
        return {
            str(k): _canonical(v)
            for k, v in sorted(value.items(), key=lambda i: str(i[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _digest(obj: Any) -> str:
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=repr)
    return hashlib.sha256(text.encode()).hexdigest()[:24]


def ruleset(config: Any) -> dict[str, str]:
    """``{id: label}`` for every contract and decision-relevant setting
    in a parsed config. ``None`` (a library that did not load) is the
    empty set. ``desc`` is a label, not part of the id."""
    if config is None:
        return {}
    out: dict[str, str] = {}
    for agent_id, agent in sorted(getattr(config, "agents", {}).items()):
        for entry in agent.contracts:
            body = _canonical(entry)
            label = entry.desc or "contract"
            if isinstance(body, dict):
                body.pop("desc", None)
            out[_digest({"agent": agent_id, "contract": body})] = f"{agent_id}: {label}"
    for name in _SETTINGS:
        value = getattr(config, name, None)
        if value in (None, {}, [], ()):
            continue
        out[_digest({"setting": name, "value": _canonical(value)})] = (
            f"setting `{name}`"
        )
    return out


@dataclass(frozen=True)
class RulesetDiff:
    added: dict[str, str]
    removed: dict[str, str]

    @property
    def weakens(self) -> bool:
        return bool(self.removed)

    def summary(self, limit: int = 5) -> str:
        parts = []
        if self.removed:
            names = list(self.removed.values())
            more = f" (+{len(names) - limit} more)" if len(names) > limit else ""
            parts.append(
                f"{len(names)} removed or changed: " + "; ".join(names[:limit]) + more
            )
        if self.added:
            parts.append(f"{len(self.added)} added")
        return ", ".join(parts) or "no contract changed"


def diff(old: dict[str, str], new: dict[str, str]) -> RulesetDiff:
    return RulesetDiff(
        added={k: v for k, v in new.items() if k not in old},
        removed={k: v for k, v in old.items() if k not in new},
    )


def sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds")


# ---------------------------------------------------------------------------
# The approved rulesets
# ---------------------------------------------------------------------------


class ApprovedRulesets:
    """The last ruleset applied per bucket, in ``<state>/rules/<bucket>.json``."""

    def __init__(self, state_dir: Path) -> None:
        self.dir = Path(state_dir) / "rules"

    def _path(self, bucket: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in bucket)
        return self.dir / f"{safe}.json"

    def get(self, bucket: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._path(bucket).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, bucket: str, ids: dict[str, str], snap: Any, how: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        record = {
            "bucket": bucket,
            "ids_algo": IDS_ALGO,
            "ids": ids,
            "content_hash": getattr(snap, "content_hash", None),
            "path": getattr(snap, "path", None),
            "applied_at": _now(),
            "how": how,
        }
        path = self._path(bucket)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
        os.replace(tmp, path)


# ---------------------------------------------------------------------------
# What a changed library serves
# ---------------------------------------------------------------------------


@dataclass
class Held:
    """A change waiting for ``sponsio rules reload``."""

    snapshot: Any
    diff: RulesetDiff
    since: str = field(default_factory=_now)


class RulePolicy:
    """The ``on_change`` policy of the daemon's snapshot store (Q1).

    ``watch`` applies every change (for Sponsio contributors), still
    auditing it. ``held_snapshot`` builds what a bucket serves when the
    daemon restarted onto a library weaker than its approved ruleset and
    has no older snapshot in memory to keep serving.
    """

    def __init__(
        self,
        state_dir: Path,
        *,
        watch: bool = False,
        held_snapshot: Callable[[Any, RulesetDiff], Any] | None = None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.approved = ApprovedRulesets(self.state_dir)
        self.watch = watch
        self._held_snapshot = held_snapshot
        self._lock = threading.Lock()
        self.held: dict[str, Held] = {}
        self._warning: str | None = None

    # ---- warnings for the next hook reply ----

    def warn(self, text: str) -> None:
        with self._lock:
            self._warning = text

    def take_warning(self) -> str | None:
        with self._lock:
            text, self._warning = self._warning, None
            return text

    # ---- the policy ----

    def __call__(self, old: Any, new: Any) -> Any:
        bucket = new.plugin_id
        new_ids = ruleset(new.config)
        if old is None:
            record = self.approved.get(bucket)
            if record is None or record.get("ids_algo") != IDS_ALGO:
                self.approved.put(bucket, new_ids, new, "first load")
                return new
            base = record.get("ids") or {}
        else:
            base = ruleset(old.config)
        d = diff(base, new_ids)
        if not d.weakens or self.watch:
            self._apply(bucket, new, new_ids, d, old)
            return new
        self._hold(bucket, new, d)
        if old is not None:
            return old
        if self._held_snapshot is not None:
            return self._held_snapshot(new, d)
        return new

    def _apply(
        self, bucket: str, new: Any, ids: dict[str, str], d: RulesetDiff, old: Any
    ) -> None:
        with self._lock:
            self.held.pop(bucket, None)
        self.approved.put(
            bucket, ids, new, "watch" if self.watch and d.weakens else "auto"
        )
        if d.weakens:
            audit.append(
                self.state_dir,
                CONFIG_DRIFT,
                bucket=bucket,
                path=new.path,
                applied=True,
                removed=sorted(d.removed.values()),
                added=sorted(d.added.values()),
                summary=d.summary(),
            )
        elif d.added:
            audit.append(
                self.state_dir,
                RULESET_STRENGTHENED,
                bucket=bucket,
                path=new.path,
                added=sorted(d.added.values()),
                content_hash=new.content_hash,
            )

    def _hold(self, bucket: str, new: Any, d: RulesetDiff) -> None:
        with self._lock:
            previous = self.held.get(bucket)
            self.held[bucket] = Held(new, d)
        if previous is not None and previous.snapshot.content_hash == new.content_hash:
            return
        audit.append(
            self.state_dir,
            CONFIG_DRIFT,
            bucket=bucket,
            path=new.path,
            applied=False,
            removed=sorted(d.removed.values()),
            added=sorted(d.added.values()),
            summary=d.summary(),
            load_error=str(new.error) if getattr(new, "error", None) else None,
        )
        self.warn(
            f"Sponsio: the rules for {bucket} changed on disk and would weaken "
            f"enforcement ({d.summary(limit=2)}); the previous rules still apply. "
            f"Run `sponsio rules diff` and `sponsio rules reload` from your own terminal."
        )

    def reload(self, bucket: str | None, peer: dict | None) -> list[dict[str, Any]]:
        """Apply held changes (all, or one bucket's). The caller drops its
        cached snapshots so the next call loads what is on disk."""
        with self._lock:
            names = [bucket] if bucket else sorted(self.held)
            taken = {n: self.held.pop(n) for n in names if n in self.held}
        done = []
        for name, held in taken.items():
            ids = ruleset(held.snapshot.config)
            self.approved.put(name, ids, held.snapshot, "reload")
            record = audit.append(
                self.state_dir,
                RULESET_RELOADED,
                bucket=name,
                peer=peer,
                removed=sorted(held.diff.removed.values()),
                added=sorted(held.diff.added.values()),
                content_hash=held.snapshot.content_hash,
            )
            done.append(record or {"bucket": name})
        return done

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                name: {
                    "since": h.since,
                    "path": h.snapshot.path,
                    "removed": sorted(h.diff.removed.values()),
                    "added": sorted(h.diff.added.values()),
                }
                for name, h in self.held.items()
            }


# ---------------------------------------------------------------------------
# Polling the files
# ---------------------------------------------------------------------------


def engine_files(package_root: Path) -> list[Path]:
    out: list[Path] = []
    for rel in ENGINE_PATHS:
        p = package_root / rel
        if p.is_file():
            out.append(p)
        elif p.is_dir():
            out.extend(
                sorted(q for q in p.rglob("*.py") if "__pycache__" not in q.parts)
            )
    return out


class IntegrityWatcher:
    """Hash a set of files at start; report each change as ``config_drift``.

    ``files`` is called on every poll, so new library files (a pack an
    edit just included) join the watch. ``on_drift`` (optional) runs
    with each record, e.g. to queue the hook warning.
    """

    def __init__(
        self,
        state_dir: Path,
        files: Callable[[], list[Path]],
        *,
        interval_s: float = 2.0,
        on_drift: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self._files = files
        self.interval_s = interval_s
        self._on_drift = on_drift
        self._known: dict[str, tuple[tuple, str | None]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.poll(record=False)

    @staticmethod
    def _stat(path: Path) -> tuple:
        try:
            st = path.stat()
        except OSError:
            return ()
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def poll(self, *, record: bool = True) -> list[dict[str, Any]]:
        found = []
        for path in self._files():
            key = str(path)
            sig = self._stat(path)
            known = self._known.get(key)
            if known is not None and known[0] == sig:
                continue
            digest = sha256_file(path) if sig else None
            self._known[key] = (sig, digest)
            if known is None or known[1] == digest or not record:
                continue
            rec = audit.append(
                self.state_dir,
                CONFIG_DRIFT,
                file=key,
                change="deleted" if digest is None else "modified",
                old_sha256=known[1],
                new_sha256=digest,
            )
            rec = rec or {"file": key}
            found.append(rec)
            if self._on_drift is not None:
                self._on_drift(rec)
        return found

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.wait(self.interval_s):
                try:
                    self.poll()
                except Exception:  # noqa: BLE001 - the watch must not die
                    _logger.exception("integrity poll failed")

        self._thread = threading.Thread(
            target=loop, name="sponsio-integrity", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_s + 1)

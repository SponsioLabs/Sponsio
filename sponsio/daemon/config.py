"""The daemon's own config: ``<state_dir>/daemon.yaml``.

Every knob that shapes a hook decision lives here, not in an environment
variable. A hook's environment comes from the host's settings, which the
agent under guard can edit (``env`` in Claude Code's ``settings.json``),
so ``SPONSIO_GUARD_MODE=observe`` there used to switch the guard to
observe. The daemon now reads no environment variable for a decision,
not even its own: a daemon started from a developer's shell with
``SPONSIO_MODE=observe`` (an SDK setting) does not shadow the hooks.

The file::

    mode: enforce            # hosts not listed below
    hosts:
      claude-code: {mode: observe}
    on_error: deny           # what an evaluation error becomes
    unconfigured: null       # deny | allow | null (the host library decides)
    plugin_root: null        # null: the --plugin-root the daemon started with
    trace_ttl_hours: 24
    cloud:                   # compare loaded rules with the published book
      project: my-project
      buckets: {_host_claude_code: my-agent}
      verify_interval_s: 300
    audit:                   # also send every audit record to an OTLP/HTTP endpoint
      otlp_endpoint: https://otlp.example.com/v1/traces
      otlp_headers: {x-api-key: "..."}
    signing:                 # rules must carry a signature by this key
      public_key: "<base64 Ed25519 public key>"
      buckets: ["*"]         # or a list of buckets
    egress:                  # hosts the egress proxy may reach (Tier 3)
      allow: [api.anthropic.com, "*.github.com"]

It is changed through the admin socket (``mode.set``; ``sponsio host
install --mode``); with no daemon running, ``sponsio host install``
writes it directly, which is possible only for a user who owns the
state dir. A missing file, and any key that is missing or not valid,
takes the strict default: ``enforce``, ``deny``. A file that does not
parse at all is treated as missing.

The in-process SDK still reads ``SPONSIO_MODE``: the developer owns that
process. Only the hook path is decided here.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from sponsio.guard_stdin import GuardPolicy

CONFIG_FILE = "daemon.yaml"
MODES = ("enforce", "observe")

_logger = logging.getLogger("sponsio.daemon.config")


def config_path(state_dir: Path) -> Path:
    return Path(state_dir) / CONFIG_FILE


@dataclass(frozen=True)
class DaemonConfig:
    mode: str = "enforce"
    hosts: dict[str, str] = field(default_factory=dict)
    """Host name -> mode, for hosts whose mode differs from ``mode``."""
    on_error: str = "deny"
    unconfigured: str | None = None
    plugin_root: Path | None = None
    trace_ttl_hours: float = 24.0
    cloud_project: str | None = None
    cloud_buckets: dict[str, str] = field(default_factory=dict)
    """Host bucket -> the cloud agent whose published book it should match."""
    cloud_verify_interval_s: float = 300.0
    audit_otlp_endpoint: str | None = None
    audit_otlp_headers: dict[str, str] = field(default_factory=dict)
    signing_public_key: str | None = None
    signing_buckets: tuple[str, ...] = ()
    """Buckets whose rule files must be signed; ``("*",)`` for all."""
    egress_allow: tuple[str, ...] = ()
    """Host patterns the egress proxy may connect to (``*.pypi.org``)."""

    def requires_signature(self, bucket: str) -> bool:
        if not self.signing_public_key:
            return False
        return "*" in self.signing_buckets or bucket in self.signing_buckets

    def mode_for(self, host: str | None) -> str:
        return self.hosts.get(host or "", self.mode)

    def policy_for(self, host: str | None) -> GuardPolicy:
        return GuardPolicy(
            mode=self.mode_for(host),
            on_error=self.on_error,
            unconfigured=self.unconfigured,
            trace_ttl_hours=self.trace_ttl_hours,
        )

    @classmethod
    def from_dict(cls, raw: Any) -> "DaemonConfig":
        """Valid keys taken, anything else left at its strict default."""
        if not isinstance(raw, dict):
            return cls()
        kw: dict[str, Any] = {}
        if raw.get("mode") in MODES:
            kw["mode"] = raw["mode"]
        hosts = raw.get("hosts")
        if isinstance(hosts, dict):
            kw["hosts"] = {
                str(h): v["mode"]
                for h, v in hosts.items()
                if isinstance(v, dict) and v.get("mode") in MODES
            }
        if raw.get("on_error") in ("deny", "allow"):
            kw["on_error"] = raw["on_error"]
        if raw.get("unconfigured") in ("deny", "allow"):
            kw["unconfigured"] = raw["unconfigured"]
        if isinstance(raw.get("plugin_root"), str) and raw["plugin_root"]:
            kw["plugin_root"] = Path(raw["plugin_root"]).expanduser()
        ttl = raw.get("trace_ttl_hours")
        if isinstance(ttl, (int, float)) and not isinstance(ttl, bool) and ttl > 0:
            kw["trace_ttl_hours"] = float(ttl)
        cloud = raw.get("cloud")
        if isinstance(cloud, dict):
            if isinstance(cloud.get("project"), str) and cloud["project"]:
                kw["cloud_project"] = cloud["project"]
            buckets = cloud.get("buckets")
            if isinstance(buckets, dict):
                kw["cloud_buckets"] = {
                    str(b): str(a)
                    for b, a in buckets.items()
                    if isinstance(a, str) and a
                }
            iv = cloud.get("verify_interval_s")
            if isinstance(iv, (int, float)) and not isinstance(iv, bool) and iv >= 30:
                kw["cloud_verify_interval_s"] = float(iv)
        aud = raw.get("audit")
        if isinstance(aud, dict):
            ep = aud.get("otlp_endpoint")
            if isinstance(ep, str) and ep.startswith(("http://", "https://")):
                kw["audit_otlp_endpoint"] = ep
            headers = aud.get("otlp_headers")
            if isinstance(headers, dict):
                kw["audit_otlp_headers"] = {str(k): str(v) for k, v in headers.items()}
        egress = raw.get("egress")
        if isinstance(egress, dict) and isinstance(egress.get("allow"), list):
            kw["egress_allow"] = tuple(
                str(h).strip().lower() for h in egress["allow"] if str(h).strip()
            )
        signing = raw.get("signing")
        if isinstance(signing, dict):
            key = signing.get("public_key")
            if isinstance(key, str) and key.strip():
                kw["signing_public_key"] = key.strip()
                buckets = signing.get("buckets", ["*"])
                if isinstance(buckets, str):
                    buckets = [buckets]
                if isinstance(buckets, list):
                    kw["signing_buckets"] = tuple(str(b) for b in buckets) or ("*",)
        return cls(**kw)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "hosts": {h: {"mode": m} for h, m in sorted(self.hosts.items())},
            "on_error": self.on_error,
            "unconfigured": self.unconfigured,
            "plugin_root": str(self.plugin_root) if self.plugin_root else None,
            "trace_ttl_hours": self.trace_ttl_hours,
            "cloud": {
                "project": self.cloud_project,
                "buckets": dict(sorted(self.cloud_buckets.items())),
                "verify_interval_s": self.cloud_verify_interval_s,
            }
            if self.cloud_buckets
            else None,
            "audit": {
                "otlp_endpoint": self.audit_otlp_endpoint,
                "otlp_headers": dict(self.audit_otlp_headers),
            }
            if self.audit_otlp_endpoint
            else None,
            "signing": {
                "public_key": self.signing_public_key,
                "buckets": list(self.signing_buckets),
            }
            if self.signing_public_key
            else None,
            "egress": {"allow": list(self.egress_allow)} if self.egress_allow else None,
        }


def load(state_dir: Path) -> DaemonConfig:
    import yaml

    try:
        text = config_path(state_dir).read_text(encoding="utf-8")
    except FileNotFoundError:
        return DaemonConfig()
    except OSError as e:
        _logger.warning("cannot read %s: %s; using strict defaults", state_dir, e)
        return DaemonConfig()
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        _logger.warning("%s does not parse (%s); using strict defaults", CONFIG_FILE, e)
        return DaemonConfig()
    return DaemonConfig.from_dict(raw)


def save(state_dir: Path, config: DaemonConfig) -> Path:
    """Write ``config`` atomically, owner-only."""
    import yaml

    path = config_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "# Sponsio daemon config. Change it through the admin socket\n"
        "# (`sponsio host install --mode ...`), not by hand while a daemon runs.\n"
        + yaml.safe_dump(config.to_dict(), sort_keys=False)
    )
    fd, tmp = tempfile.mkstemp(prefix=f".{CONFIG_FILE}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def set_host_mode(state_dir: Path, host: str, mode: str) -> DaemonConfig:
    """Persist ``host``'s mode. A mode equal to the default is not
    listed, so ``mode`` changes still reach that host."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    current = load(state_dir)
    hosts = dict(current.hosts)
    if mode == current.mode:
        hosts.pop(host, None)
    else:
        hosts[host] = mode
    updated = replace(current, hosts=hosts)
    save(state_dir, updated)
    return updated


class ConfigFile:
    """The config as the daemon serves it: re-read when the file changes
    (a stat per call), so an admin write takes effect on the next call."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self._lock = threading.Lock()
        self._sig: tuple | None = None
        self._config = DaemonConfig()

    def _signature(self) -> tuple | None:
        try:
            st = config_path(self.state_dir).stat()
        except FileNotFoundError:
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def current(self) -> DaemonConfig:
        sig = self._signature()
        with self._lock:
            if sig != self._sig:
                self._config = load(self.state_dir) if sig else DaemonConfig()
                self._sig = sig
            return self._config

    def set_host_mode(self, host: str, mode: str) -> DaemonConfig:
        with self._lock:
            updated = set_host_mode(self.state_dir, host, mode)
            self._config = updated
            self._sig = self._signature()
            return updated

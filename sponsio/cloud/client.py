"""One client for every cloud call.

Pull, push, and (later) trace upload and session summary all go through this
object so there is a single place that owns credentials, timeouts, retries and
the failure policy. Four separate helpers would mean four subtly different
answers to "what happens when the network is down", and that question has
exactly one correct answer here.

Standard library only, on purpose: ``sponsio`` core must not grow a hard
dependency for a code path most users never touch.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://app.sponsio.dev"
DEFAULT_TIMEOUT = 10.0
# Two retries on transport errors and 5xx. Construction is allowed to block,
# but not for long: a slow cloud must not become a slow agent start.
DEFAULT_RETRIES = 2

CREDENTIALS_PATH = Path.home() / ".sponsio" / "credentials"


def write_api_key(
    key: str, *, url: str | None = None, path: Path | None = None
) -> Path:
    """Persist a key for future runs, readable only by this user.

    The endpoint is stored with it. A key is only valid against the service
    that issued it, so saving the key alone lets a key verified against a
    local or staging server be sent to production on the next run.

    0600 matters: this file is a bearer credential, and the default umask on
    a shared box would leave it world-readable.
    """
    target = path or CREDENTIALS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Sponsio credentials. Keep private.", f"api_key={key}"]
    if url:
        lines.append(f"url={url}")
    target.write_text("\n".join(lines) + "\n")
    target.chmod(0o600)
    return target


def read_saved_url() -> str | None:
    """The endpoint ``sponsio login`` verified the saved key against."""
    try:
        raw = CREDENTIALS_PATH.read_text()
    except OSError:
        return None
    for line in raw.splitlines():
        name, _, value = line.strip().partition("=")
        if name.strip() == "url" and value.strip():
            return value.strip()
    return None


class CloudError(RuntimeError):
    """A cloud call did not succeed. Callers decide whether that is fatal."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class PulledRulebook:
    """A rulebook document plus the provenance the console needs back."""

    yaml_text: str
    versions: str | None = None
    sha: str | None = None
    agent: str | None = None


def read_api_key() -> str | None:
    """``SPONSIO_API_KEY`` first, then ``~/.sponsio/credentials``.

    The env var wins so CI and one-off runs can override a logged-in machine
    without touching files.
    """
    key = os.environ.get("SPONSIO_API_KEY", "").strip()
    if key:
        return key
    try:
        raw = CREDENTIALS_PATH.read_text()
    except OSError:
        return None
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, value = line.partition("=")
        if name.strip() == "api_key" and value.strip():
            return value.strip()
    return None


def base_url() -> str:
    """Env var, then the endpoint login saved, then production."""
    explicit = os.environ.get("SPONSIO_API_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    saved = read_saved_url()
    return (saved or DEFAULT_BASE_URL).rstrip("/")


class CloudClient:
    """Talks to the Sponsio Cloud API. Never used on the enforcement path."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ) -> None:
        self.api_key = api_key if api_key is not None else read_api_key()
        self.url = (url or base_url()).rstrip("/")
        self.timeout = timeout
        self.retries = retries

    @property
    def configured(self) -> bool:
        """No key means no cloud. Not an error — it is the default mode."""
        return bool(self.api_key)

    # -- transport ---------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        if not self.configured:
            raise CloudError("no API key: set SPONSIO_API_KEY or run `sponsio login`")

        req = urllib.request.Request(f"{self.url}{path}", data=body, method=method)
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("User-Agent", "sponsio-sdk")
        if content_type:
            req.add_header("Content-Type", content_type)

        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return (
                        resp.status,
                        resp.read(),
                        {k.lower(): v for k, v in resp.headers.items()},
                    )
            except urllib.error.HTTPError as exc:
                # 4xx is an answer, not a failure to reach us: retrying a 401
                # or a 404 only makes startup slower.
                if exc.code < 500:
                    return (
                        exc.code,
                        exc.read(),
                        {k.lower(): v for k, v in exc.headers.items()},
                    )
                last = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
            if attempt < self.retries:
                time.sleep(0.25 * (2**attempt))
        raise CloudError(f"cannot reach {self.url}: {last}")

    @staticmethod
    def _detail(payload: bytes, fallback: str) -> str:
        try:
            parsed = json.loads(payload.decode())
        except (ValueError, UnicodeDecodeError):
            return fallback
        if isinstance(parsed, dict):
            return str(parsed.get("detail") or parsed.get("error") or fallback)
        return fallback

    # -- identity ----------------------------------------------------------

    def whoami(self) -> dict:
        """Check a key and report what it reaches.

        Worth a round trip from ``login`` and ``doctor``: a wrong key should
        fail while the user is looking at the terminal, not two days later
        inside an agent run.
        """
        status, payload, _ = self._request("GET", "/v1/whoami")
        if status in (401, 403):
            raise CloudError("key rejected", status=status)
        if status != 200:
            raise CloudError(
                self._detail(payload, f"whoami failed ({status})"), status=status
            )
        try:
            return json.loads(payload.decode())
        except ValueError as exc:
            raise CloudError("whoami returned a non-JSON body") from exc

    # -- rulebook ----------------------------------------------------------

    def pull_rulebook(
        self, project: str, *, agent: str | None = None, version: int | None = None
    ) -> PulledRulebook:
        """Fetch a project's rulebook.

        Without ``agent`` this returns every agent merged into one document —
        a guard binds by ``agent_id`` and loads only its own section, so one
        request serves a whole repo.
        """
        query = []
        if project:
            query.append(f"project={project}")
        if agent:
            query.append(f"agent={agent}")
        if version is not None:
            query.append(f"version={version}")
        path = "/v1/rulebook" + ("?" + "&".join(query) if query else "")

        status, payload, headers = self._request("GET", path)
        if status == 404:
            # The signal for "nothing published yet" — the caller may publish
            # its local yaml instead of treating this as an error.
            raise CloudError(self._detail(payload, "no rulebook published"), status=404)
        if status != 200:
            raise CloudError(
                self._detail(payload, f"pull failed ({status})"), status=status
            )
        return PulledRulebook(
            yaml_text=payload.decode(),
            versions=headers.get("x-rulebook-versions")
            or headers.get("x-rulebook-version"),
            sha=headers.get("x-rulebook-sha"),
            agent=headers.get("x-rulebook-agent"),
        )

    # -- sessions ----------------------------------------------------------

    def ingest_session(self, project: str, payload: dict) -> dict:
        """Send a run (or an update to one) to the cloud.

        Idempotent on the session key server-side, so a retry after a blip
        cannot double a run. Callers on the hot path must not block on this:
        the run-phase rule is outbound-only and best-effort, and losing
        telemetry must never change what the agent does.
        """
        path = "/v1/sessions/ingest" + (f"?project={project}" if project else "")
        status, body, _ = self._request(
            "POST",
            path,
            body=json.dumps(payload).encode(),
            content_type="application/json",
        )
        if status != 200:
            raise CloudError(
                self._detail(body, f"ingest failed ({status})"), status=status
            )
        try:
            return json.loads(body.decode())
        except ValueError as exc:
            raise CloudError("ingest returned a non-JSON body") from exc

    def extract_rules(
        self,
        policy_text: str,
        *,
        tools: list[str] | None = None,
        agent: str | None = None,
        project: str | None = None,
    ) -> dict:
        """Parse a policy document on the cloud's strong-model tier.

        The hosted twin of ``sponsio scan --llm``: the document is sent to
        the service (explicitly — calling this is the opt-in), the model
        call runs on the service's key and is metered against the
        workspace's monthly budget, and only rules the real parser
        compiles come back. Nothing is armed: review, edit, push.
        """
        path = "/v1/rulebook/extract" + (f"?project={project}" if project else "")
        body = {"policy_text": policy_text, "tools": tools, "agent": agent}
        status, payload, _ = self._request(
            "POST",
            path,
            body=json.dumps(body).encode(),
            content_type="application/json",
        )
        if status != 200:
            raise CloudError(
                self._detail(payload, f"extract failed ({status})"), status=status
            )
        try:
            return json.loads(payload.decode())
        except ValueError as exc:
            raise CloudError("extract returned a non-JSON body") from exc

    def push_rulebook(self, project: str, yaml_text: str) -> dict:
        """Publish a local yaml as the next version of each agent's book."""
        path = "/v1/rulebook/push" + (f"?project={project}" if project else "")
        status, payload, _ = self._request(
            "POST", path, body=yaml_text.encode(), content_type="application/x-yaml"
        )
        if status != 200:
            raise CloudError(
                self._detail(payload, f"push failed ({status})"), status=status
            )
        try:
            return json.loads(payload.decode())
        except ValueError as exc:
            raise CloudError("push returned a non-JSON body") from exc

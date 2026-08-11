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

DEFAULT_BASE_URL = "https://api.sponsio.dev"
DEFAULT_TIMEOUT = 10.0
# Two retries on transport errors and 5xx. Construction is allowed to block,
# but not for long: a slow cloud must not become a slow agent start.
DEFAULT_RETRIES = 2

CREDENTIALS_PATH = Path.home() / ".sponsio" / "credentials"


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
    return os.environ.get("SPONSIO_API_URL", DEFAULT_BASE_URL).rstrip("/")


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
        self, method: str, path: str, *, body: bytes | None = None, content_type: str | None = None
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
                    return resp.status, resp.read(), {k.lower(): v for k, v in resp.headers.items()}
            except urllib.error.HTTPError as exc:
                # 4xx is an answer, not a failure to reach us: retrying a 401
                # or a 404 only makes startup slower.
                if exc.code < 500:
                    return exc.code, exc.read(), {k.lower(): v for k, v in exc.headers.items()}
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

    # -- rulebook ----------------------------------------------------------

    def pull_rulebook(self, project: str, *, agent: str | None = None,
                      version: int | None = None) -> PulledRulebook:
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
            raise CloudError(self._detail(payload, f"pull failed ({status})"), status=status)
        return PulledRulebook(
            yaml_text=payload.decode(),
            versions=headers.get("x-rulebook-versions") or headers.get("x-rulebook-version"),
            sha=headers.get("x-rulebook-sha"),
            agent=headers.get("x-rulebook-agent"),
        )

    def push_rulebook(self, project: str, yaml_text: str) -> dict:
        """Publish a local yaml as the next version of each agent's book."""
        path = "/v1/rulebook/push" + (f"?project={project}" if project else "")
        status, payload, _ = self._request(
            "POST", path, body=yaml_text.encode(), content_type="application/x-yaml"
        )
        if status != 200:
            raise CloudError(self._detail(payload, f"push failed ({status})"), status=status)
        try:
            return json.loads(payload.decode())
        except ValueError as exc:
            raise CloudError("push returned a non-JSON body") from exc

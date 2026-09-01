"""Evidence middleware end to end, with a mocked OpenAI response.

No network, no API key, no OpenAI account: the "model output" is a
hand-built ChatCompletion-shaped object whose structured JSON claims the
wrong ZIP for an address, and the evidence backend is a canned client
returning the verdict the real service would produce. Everything between
those two mocks is the real pipeline:

    OpenAIGuard(evidence=...) -> check_response -> observe_llm_call
      -> evidence middleware (extract -> verify_batch -> trace events)
      -> stopping CheckResult -> in-place block-notice rewrite

Run:
    python examples/evidence_middleware_demo.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sponsio.cloud.evidence import EvidenceClient, EvidenceResult  # noqa: E402
from sponsio.integrations.openai import OpenAIGuard  # noqa: E402


class CannedEvidenceClient(EvidenceClient):
    """Stands in for the cloud API; returns a fixed MISMATCH verdict."""

    def __init__(self) -> None:  # no CloudClient — nothing does I/O
        pass

    def verify_batch(self, claims, *, session_id=None):
        return [
            EvidenceResult(
                predicate="address_zip_agreement",
                verdict="MISMATCH",
                action="block",
                values=("94704",),
                source="geocode:census",
                correction="94704",
            )
            for _ in claims
        ]


def mock_completion(content: str):
    """The minimal ChatCompletion shape check_response consumes."""
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=None,
    )


def main() -> int:
    guard = OpenAIGuard(
        contracts=[],
        verbose=True,  # evidence verdict lines render via TerminalReporter
        init_banner=False,
        evidence={
            "client": CannedEvidenceClient(),
            "on_error": "block",
            "claims": [
                {
                    "predicate": "address_zip_agreement",
                    "claim_field": "zip_code",
                    "inputs": {
                        "street": {"field": "street", "source": "model_output"},
                        "city": {"field": "city", "source": "model_output"},
                        "state": {"field": "state", "source": "model_output"},
                    },
                }
            ],
        },
    )

    assistant_output = json.dumps(
        {
            "street": "2120 Oxford St",
            "city": "Berkeley",
            "state": "CA",
            "zip_code": "94103",  # wrong: that's a San Francisco zip
        }
    )
    response = mock_completion(assistant_output)

    print("--- assistant output (before) ---")
    print(assistant_output)

    guard.check_response(response)
    guard._apply_evidence_notices(response)

    print("--- assistant output (after evidence enforcement) ---")
    print(response.choices[0].message.content)

    check = guard.last_llm_checks[0]
    return 0 if check.evidence_stopped else 1


if __name__ == "__main__":
    sys.exit(main())

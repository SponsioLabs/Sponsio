"""Cloud evidence verification, end to end from the OSS runtime.

Sends two factual claims to the Sponsio Cloud evidence API and prints
the verdicts through the standard terminal reporter:

* ``date_weekday_agreement`` — claims 2000-01-01 was a Friday (it was a
  Saturday, so the service answers MISMATCH with the correction);
* ``city_zip_candidates`` — claims 94720 is THE zip of Berkeley, CA
  (Berkeley has many zips, so the service answers UNDERDETERMINED with
  the candidate set).

Credentials come from the normal config path: ``SPONSIO_API_KEY`` /
``SPONSIO_API_URL`` env vars, falling back to ``~/.sponsio/credentials``
written by ``sponsio login``. This script is the cross-repo integration
check for the thin client — run it against a local Sponsio-cloud stack:

    SPONSIO_API_KEY=sk_sp_v1_... SPONSIO_API_URL=http://127.0.0.1:8899 \
        python examples/evidence_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sponsio.cloud.client import CloudClient, CloudError  # noqa: E402
from sponsio.runtime.terminal import TerminalReporter  # noqa: E402


def main() -> int:
    client = CloudClient()
    if not client.configured:
        print(
            "No API key. Set SPONSIO_API_KEY (and SPONSIO_API_URL for a "
            "local stack) or run `sponsio login`.",
            file=sys.stderr,
        )
        return 2

    reporter = TerminalReporter(verbosity=1)
    claims = [
        {
            "predicate": "date_weekday_agreement",
            "value": "Friday",
            "inputs": {"date": ("2000-01-01", "user_utterance")},
        },
        {
            "predicate": "city_zip_candidates",
            "value": "94720",
            "inputs": {
                "city": ("Berkeley", "user_utterance"),
                "state": ("CA", "user_utterance"),
            },
        },
    ]

    try:
        for claim in claims:
            result = client.evidence.verify(
                claim["predicate"],
                value=claim["value"],
                inputs=claim["inputs"],
            )
            reporter.report_evidence(result)
    except CloudError as exc:
        # No verdict was produced — the fail-closed reading is "block".
        print(f"evidence call failed (treat as block): {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

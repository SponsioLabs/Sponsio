"""Shadow-mode session report generator.

``sponsio report`` reads the JSONL files that :class:`SessionLogger`
writes (``~/.sponsio/sessions/<agent_id>/<ts>_<pid>.jsonl``), aggregates
per-agent / per-contract statistics, and renders a Markdown, HTML, or
JSON report suitable for pasting into Slack, GitHub issues, or CI
artifacts.

The module is:

* **Read-only.** It never writes to the session directory.
* **Zero-dep.** No Jinja, no pandas, no requests. Pure stdlib.
* **Pure where possible.** Each layer (reader → aggregator → renderer)
  is independently testable.

Public API::

    from sponsio.reporting import load_events, aggregate, render

    events = list(load_events(since="24h", agent="support_bot"))
    report = aggregate(events)
    print(render(report, fmt="markdown"))
"""

from __future__ import annotations

from sponsio.reporting.aggregator import (
    ContractStat,
    Report,
    SessionStat,
    aggregate,
)
from sponsio.reporting.reader import SessionEvent, load_events, parse_since
from sponsio.reporting.renderer import render, render_html, render_json, render_markdown

__all__ = [
    "ContractStat",
    "Report",
    "SessionEvent",
    "SessionStat",
    "aggregate",
    "load_events",
    "parse_since",
    "render",
    "render_html",
    "render_json",
    "render_markdown",
]

"""What may leave the machine.

Enforcement is local. A deterministic contract is evaluated in this
process, against this trace, with no network call and no model, which
means the content of a tool argument is needed *here* and is not needed
anywhere else. What the cloud needs to render a dashboard, count a
finding and bill a check is the shape of the run: which tool, in what
order, with what verdict.

That gap is the whole of this module. Four levels, decreasing in what
crosses the boundary:

``full``      arguments as the SDK already truncates them (the default,
              and the right one for a team watching its own agents)
``shape``     key names and value types, no values:
              ``{"amount": <float>, "to": <str:9>}``
``hashed``    a stable digest per argument set. Two calls with the same
              arguments still look the same, which is what duplicate and
              loop detection read, while the arguments themselves do not
              leave
``metadata``  no arguments at all, and no model output text: the model
              turn is still recorded, with its claim verdicts, but the
              values those claims were checked against and any corrected
              wording stay behind
``tool_calls`` the action lane only. Tool name, order and verdict. No
              model turns, no claim verdicts, no delegation notes. This is
              the level a customer means by "only send us the tool calls"

A platform whose customers are in healthcare or finance can run
``metadata`` or ``tool_calls`` and still get every deterministic finding
the cloud derives, because such a finding is a statement about which
tools ran in which order, and no level changes that. The cost is evidence
detail in the dashboard, and nothing else.

One thing no level can do is make cloud claim verification content-free:
verifying a claim means sending the claim. ``tool_calls`` therefore
refuses to coexist with an evidence configuration, at guard construction
and again at attach, rather than quietly uploading through a side door
the bridge does not own.

Set it with ``SPONSIO_PRIVACY`` or ``bridge.attach(..., privacy="shape")``.
The environment variable wins, so an operator can tighten a deployment
without editing the code that ships in it.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

LEVELS = ("full", "shape", "hashed", "metadata", "tool_calls")
STRICTEST = LEVELS[-1]
DEFAULT = "full"


def resolve(explicit: str | None = None) -> str:
    """The level in force. Environment beats argument, deliberately.

    The person who decides what may leave a machine is the operator of
    that machine, not the author of the agent running on it.
    """
    env = os.environ.get("SPONSIO_PRIVACY", "").strip().lower()
    if env in LEVELS:
        return env
    if env:
        # An unrecognised value is far more likely to be a typo in a
        # deployment that meant to tighten than a request to loosen, so
        # it fails closed, to the strictest level there is.
        return STRICTEST
    if explicit in LEVELS:
        return explicit
    return DEFAULT


def _type_of(value: Any) -> str:
    if value is None:
        return "<null>"
    if isinstance(value, bool):
        return "<bool>"
    if isinstance(value, int):
        return "<int>"
    if isinstance(value, float):
        return "<float>"
    if isinstance(value, str):
        return f"<str:{len(value)}>"
    if isinstance(value, (list, tuple)):
        return f"<list:{len(value)}>"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k!s}: {_type_of(v)}" for k, v in value.items()) + "}"
    return f"<{type(value).__name__}>"


def shape(args: Any) -> str:
    """The structure of an argument set, with every value replaced.

    Lengths survive for strings and lists because "the model passed a
    40 000-character path" is a debugging fact and not content, and it is
    the one an oversize-argument rule fires on.
    """
    if args is None:
        return ""
    if isinstance(args, dict):
        return "{" + ", ".join(f'"{k}": {_type_of(v)}' for k, v in args.items()) + "}"
    return _type_of(args)


def digest(args: Any) -> str:
    """A stable short hash of an argument set.

    Sorted keys so two calls that differ only in dict ordering agree, and
    truncated to 12 hex characters because this is an equality token, not
    a signature.
    """
    if args is None:
        return ""
    try:
        canonical = json.dumps(args, sort_keys=True, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        canonical = str(args)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()[:12]


def preview(args: Any, level: str, *, full_preview: str) -> str:
    """What the ``argsPreview`` field should carry at ``level``.

    ``full_preview`` is what the existing truncating projection produced;
    at ``full`` it is passed through unchanged so this module cannot
    change today's behaviour for anyone who has not asked it to.
    """
    if level == "full":
        return full_preview
    if level == "shape":
        return shape(args)
    if level == "hashed":
        return digest(args)
    return ""


def say(text: str, level: str) -> str:
    """What a model turn may quote of itself.

    At ``metadata`` the sentence goes entirely; at every other level it
    is what it was. A response is the most content-bearing field in the
    payload, so it moves one level earlier than arguments do.
    """
    if level in ("metadata", "tool_calls"):
        return ""
    return text


def keeps_step(step_type: str, level: str) -> bool:
    """Whether a step of this type is recorded at all at ``level``.

    Only ``tool_calls`` drops whole steps. Every other level keeps the
    shape of the run and empties fields; this one keeps only the action
    lane, because that is what the customer asked for by name.
    """
    if level == "tool_calls":
        return step_type == "tool_call"
    return True


def claims(rows: list, level: str) -> list:
    """Claim verdicts as ``level`` allows them to leave.

    A verdict (MISMATCH, VERIFIED) is metadata. The authoritative value
    the claim was checked against and the corrected sentence are content,
    and at ``metadata`` they go the same way arguments do. ``tool_calls``
    never reaches here: the whole output step is dropped first.
    """
    if level in ("full", "shape", "hashed"):
        return rows
    out = []
    for row in rows:
        kept = dict(row)
        kept["evidence"] = ""
        kept["fix"] = ""
        out.append(kept)
    return out


def describe(level: str) -> dict[str, Any]:
    """A stamp for the run, so a reader knows what they are not seeing.

    A dashboard showing empty arguments has to be able to say whether
    the agent passed none or the deployment declined to send them. One
    field answers that, and its absence would make every redacted trace
    ambiguous.
    """
    return {
        "level": level,
        "sendsArguments": level == "full",
        "sendsArgumentShapes": level in ("full", "shape"),
        "sendsOutputText": level in ("full", "shape", "hashed"),
        "sendsClaimValues": level in ("full", "shape", "hashed"),
        "sendsOutputLane": level != "tool_calls",
    }


class PrivacyConflict(ValueError):
    """A configuration that cannot keep the promise its privacy level makes."""


def refuse_evidence_under_tool_calls(guard: Any, level: str) -> None:
    """``tool_calls`` and cloud claim verification cannot both be true.

    Verification sends the claim's value and text to the cloud; that is
    what verifying means. A deployment that asked for tool calls only and
    also configured evidence has asked for two incompatible things, and
    the right answer is to say so before the first run, not to honour one
    silently. Called at guard construction (environment level) and at
    attach (resolved level), so neither path can slip past the other.
    """
    if level != "tool_calls":
        return
    if getattr(guard, "_evidence_config", None) is None:
        return
    raise PrivacyConflict(
        "SPONSIO_PRIVACY=tool_calls sends only tool calls, but this guard has "
        "an evidence configuration, and verifying a claim means sending its "
        "value to the cloud. Remove the evidence section or choose "
        "SPONSIO_PRIVACY=metadata."
    )

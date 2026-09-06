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
``metadata``  no arguments at all, and no model output text

A platform whose customers are in healthcare or finance can run
``metadata`` and still get every finding the cloud derives, because a
deterministic finding is a statement about which tools ran in which
order, and none of the four levels changes that. The cost is evidence
detail in the dashboard, and nothing else.

Set it with ``SPONSIO_PRIVACY`` or ``bridge.attach(..., privacy="shape")``.
The environment variable wins, so an operator can tighten a deployment
without editing the code that ships in it.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

LEVELS = ("full", "shape", "hashed", "metadata")
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
        # it fails closed rather than silently sending everything.
        return "metadata"
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
    if level == "metadata":
        return ""
    return text


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
        "sendsOutputText": level != "metadata",
    }

"""Workflow prompt templates for ``sponsio prompt <flow>``.

These markdown files are read by the host agent driving the
``sponsio`` skill's W1 (initial setup / onboard) and W3b (refresh
from traces) workflows.  Same pattern as
:mod:`sponsio.plugin.prompts`: agent gets the prompt + structured
context (via ``--emit-context`` / ``--emit-traces``) and applies the
prompt in its own LLM context — no extra API key, no extra round
trip.
"""

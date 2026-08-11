"""Stream a guarded run into a console.

    import sponsio, sponsio.bridge
    guard = sponsio.Sponsio(config="sponsio://alpha", agent_id="quant")
    run = sponsio.bridge.attach(guard)
    ...
    run.finish()

This reads the guard's span tree (``agent_turn`` → ``contract_check`` →
``guarantee`` / ``violation`` / ``enforcement``) and projects each tool call
into one view-model step, which is the shape the console renders. It is a
projection of the spans, not a second source of truth: every field here
traces back to something the runtime already recorded.

Two rules this module exists to respect:

* **The run phase is outbound-only.** Sending a session never blocks the
  agent and never waits for an answer. If the network is down the run is
  unaffected; losing telemetry must not change what an agent does.
* **Nothing is invented.** A field the spans do not carry is left out rather
  than guessed, because a console that displays a plausible fiction is worse
  than one that displays a gap.

Checkpoint pause (the console's Pause button) is a **local development**
feature and is off by default: it is a two-way exchange during a run, which
the cloud path deliberately does not have.
"""

from sponsio.bridge.session import BridgeSession, attach

__all__ = ["BridgeSession", "attach"]

"""Discriminative feedback generation for sto constraint violations.

Generates targeted re-prompts that help agents fix sto constraint violations.
Priority: user-provided template > registered template > generic fallback.
"""

from __future__ import annotations

from sponsio.runtime.evaluators import StoResult


_GENERIC_TEMPLATE = (
    "Constraint '{name}' violated (confidence: {score:.2f}). "
    "{evidence}. Please regenerate ensuring: {suggestion}."
)


class FeedbackGenerator:
    """Generates discriminative feedback for sto constraint violations.

    Feedback is a targeted re-prompt injected into the agent to help it
    fix a sto constraint violation on retry.
    """

    def generate(
        self,
        prop_name: str,
        result: StoResult,
        template: str | None = None,
    ) -> str:
        """Generates feedback for a sto constraint violation.

        Template priority:
            1. Explicit ``template`` argument (user-provided at call site).
            2. Generic fallback template.

        Templates support placeholders: ``{name}``, ``{score}``,
        ``{evidence}``, ``{suggestion}``.

        Args:
            prop_name: Name of the violated constraint.
            result: The StoResult from evaluation.
            template: Optional override template.

        Returns:
            A formatted feedback string ready for agent re-prompting.
        """
        tmpl = template or _GENERIC_TEMPLATE
        # Use manual replacement instead of .format() to prevent
        # format string injection from user-provided templates.
        safe_values = {
            "name": str(prop_name),
            "score": f"{result.score:.2f}",
            "evidence": str(result.evidence),
            "suggestion": str(result.suggestion),
        }
        result_str = tmpl
        for key, val in safe_values.items():
            result_str = result_str.replace("{" + key + "}", val)
            result_str = result_str.replace("{" + key + ":.2f}", val)
        return result_str

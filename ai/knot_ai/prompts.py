"""Prompt construction for the AI service.

STUB. Real impl will assemble a prompt from the user input plus the
schema context fetched from knot (published classes/slots/sources).
"""

from __future__ import annotations


def build_prompt(user_input: str, schema_context: dict | None = None) -> str:
    """STUB. Returns a placeholder prompt string."""
    # TODO: real templating with schema context (classes, slots, sources).
    _ = schema_context
    return f"User wants: {user_input}\n\n# TODO: schema-grounded prompt."

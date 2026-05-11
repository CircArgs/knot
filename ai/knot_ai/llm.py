"""LLM client abstraction.

STUB. Real impl will dispatch on `Settings.llm_provider` between
anthropic / openai / local-model backends.
"""

from __future__ import annotations


class LLMClient:
    """Stub client. Replace with provider-specific impl."""

    def __init__(self, provider: str = "stub", api_key: str | None = None) -> None:
        self.provider = provider
        self.api_key = api_key

    async def complete(self, prompt: str) -> str:
        # TODO: dispatch on self.provider; for now echo the prompt.
        return f"[stub:{self.provider}] {prompt[:80]}"

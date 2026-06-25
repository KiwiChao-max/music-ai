"""LLM service for music understanding / generation prompts (Milestone 2)."""


class LlmService:
    """Thin wrapper around an LLM provider (OpenAI, Anthropic, local...)."""

    async def generate(self, prompt: str) -> str:
        raise NotImplementedError

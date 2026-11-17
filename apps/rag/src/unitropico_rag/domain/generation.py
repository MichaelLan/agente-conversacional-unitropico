from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TokenUsage:
    """Carry reported counts only; unknown counts and monetary costs stay unknown."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class GeneratedText:
    """Carry a candidate, never a grounded answer or validated citations."""

    content: str
    usage: TokenUsage | None = None


class GenerationError(Exception):
    """Expose a safe code and whether consumption may already have occurred."""

    def __init__(self, code: str, request_attempted: bool = False):
        """Store only controlled metadata, not upstream exceptions or payloads."""
        super().__init__(code)
        self.code = code
        self.request_attempted = request_attempted


class TextGenerator(Protocol):
    """Define the asynchronous generation boundary for future orchestration."""

    async def complete(self, system_prompt: str, user_content: str) -> GeneratedText:
        """Generate one unvalidated candidate or raise a controlled error."""
        ...

from dataclasses import dataclass, field
from typing import Literal, Protocol

ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]


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


@dataclass(frozen=True)
class StructuredOutput:
    """Name and strict JSON Schema requested from the provider for the candidate."""

    name: str
    schema: dict[str, object] = field(hash=False)


class GenerationError(Exception):
    """Expose a safe code and whether consumption may already have occurred."""

    def __init__(self, code: str, request_attempted: bool = False):
        """Store only controlled metadata, not upstream exceptions or payloads."""
        super().__init__(code)
        self.code = code
        self.request_attempted = request_attempted


class TextGenerator(Protocol):
    """Define the asynchronous generation boundary used by answer orchestration."""

    async def complete(self, system_prompt: str, user_content: str) -> GeneratedText:
        """Generate one unvalidated candidate or raise a controlled error."""
        ...

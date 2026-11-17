from collections.abc import Awaitable, Callable


class QueryService:
    """Orchestrate only the injected deterministic transformation, without SDKs."""

    def __init__(self, transform: Callable[[str], Awaitable[str]]) -> None:
        """Receive the asynchronous adapter explicitly at composition time."""
        self._transform = transform

    async def query(self, question: str) -> str:
        """Transform a validated question without retaining conversation state."""
        return await self._transform(question)

import asyncio
import json

from unitropico_api.domain.retrieval import MalformedUpstream, RetrievalQuery


class RagServiceBindingClient:
    """Send retrieval requests through an injected native Workers service binding."""

    def __init__(self, binding: object) -> None:
        """Retain only the request-scoped binding, never environment or settings."""
        self._binding = binding

    async def query(self, request: RetrievalQuery) -> object:
        """Return Python JSON; leave schema validation to the coordinator."""
        async with asyncio.timeout(10):
            response = await self._binding.fetch(
                "https://rag.internal/retrieval/query",
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request.model_dump_json(),
            )
            status = response.status
            if isinstance(status, bool) or not isinstance(status, int):
                raise RuntimeError() from None
            if not 200 <= status < 300:
                raise RuntimeError() from None
            try:
                return await response.json()
            except json.JSONDecodeError:
                raise MalformedUpstream() from None

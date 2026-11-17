from typing import Protocol

from pydantic import ValidationError

from unitropico_api.domain.retrieval import (
    MalformedUpstream,
    RetrievalQuery,
    RetrievalResponse,
)


class AsyncRagClient(Protocol):
    """Define an asynchronous dependency port independent of HTTP and SDKs."""

    async def query(self, request: RetrievalQuery) -> object:
        """Return decoded upstream JSON from an injected asynchronous transport."""
        ...


class QueryCoordinationService:
    """Coordinate an optional RAG port and revalidate its untrusted response."""

    def __init__(self, client: AsyncRagClient | None = None) -> None:
        """Receive only the explicit port, never global settings or an adapter."""
        self._client = client

    async def query(self, request: RetrievalQuery) -> RetrievalResponse:
        """Fail closed on dependency failure and distinguish malformed JSON."""
        unavailable = RetrievalResponse(status="unavailable", evidence=[])
        if self._client is None:
            return unavailable
        try:
            payload = await self._client.query(request)
        except MalformedUpstream:
            raise
        except Exception:
            return unavailable
        if payload is None:
            return unavailable
        try:
            return RetrievalResponse.model_validate(payload)
        except ValidationError:
            raise MalformedUpstream() from None

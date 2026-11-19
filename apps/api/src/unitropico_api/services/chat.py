from typing import Protocol

from pydantic import ValidationError

from unitropico_api.core.logger import get_logger
from unitropico_api.domain.chat import ChatRequest, ChatResponse
from unitropico_api.domain.errors import MalformedUpstream, UpstreamUnavailable

_log = get_logger()


class AnswerClient(Protocol):
    """Port to the RAG answer endpoint, independent of HTTP and SDKs."""

    async def answer(self, request: ChatRequest) -> object:
        """Return decoded upstream JSON from an injected transport."""
        ...


class ChatService:
    """Forward chat requests to RAG and strictly revalidate its response."""

    def __init__(self, client: AnswerClient | None = None) -> None:
        """Receive only the explicit port."""
        self._client = client

    async def answer(self, request: ChatRequest) -> ChatResponse:
        """Return a validated response or raise a domain upstream error."""
        if self._client is None:
            _log.warning("upstream unavailable route=answer reason=no_client")
            raise UpstreamUnavailable("no_client")
        try:
            payload = await self._client.answer(request)
        except UpstreamUnavailable as error:
            _log.warning("upstream unavailable route=answer reason=%s", error.reason)
            raise
        except (OSError, TimeoutError) as error:
            reason = type(error).__name__
            _log.warning("upstream unavailable route=answer reason=%s", reason)
            raise UpstreamUnavailable(reason) from None
        except MalformedUpstream:
            _log.warning("upstream invalid route=answer")
            raise
        try:
            return ChatResponse.model_validate(payload)
        except ValidationError:
            _log.warning("upstream invalid route=answer")
            raise MalformedUpstream() from None

import asyncio
from collections.abc import Awaitable, Callable, MutableMapping
from contextvars import ContextVar
from typing import Any, Protocol

from pydantic import BaseModel

from unitropico_api.domain.chat import ChatRequest
from unitropico_api.domain.errors import MalformedUpstream, UpstreamUnavailable
from unitropico_api.domain.retrieval import RetrievalQuery

MAX_REFERRAL_LENGTH = 1000


class BindingResponse(Protocol):
    """Subset of a Workers fetch response used by the client."""

    status: int

    async def json(self) -> object:
        """Decode the response body as JSON."""
        ...


class ServiceBinding(Protocol):
    """Subset of a Workers service binding used by the client."""

    async def fetch(
        self, url: str, *, method: str, headers: dict[str, str], body: str
    ) -> BindingResponse:
        """Send one HTTP request to the bound service."""
        ...


class RagServiceBindingClient:
    """Send retrieval and answer requests through a native service binding."""

    def __init__(
        self,
        binding: ServiceBinding,
        *,
        base_url: str = "https://rag.internal",
        retrieval_timeout: float = 10,
        answer_timeout: float = 45,
    ) -> None:
        """Receive the binding and transport limits; never settings or env."""
        self._binding = binding
        self._base_url = base_url.rstrip("/")
        self._retrieval_timeout = retrieval_timeout
        self._answer_timeout = answer_timeout

    async def query(self, request: RetrievalQuery) -> object:
        """Return decoded retrieval JSON; schema validation is the caller's."""
        return await self._post(
            "/retrieval/query", request, self._retrieval_timeout, read_referral=False
        )

    async def answer(self, request: ChatRequest) -> object:
        """Return decoded answer JSON; the timeout covers retrieval plus LLM."""
        return await self._post(
            "/answer", request, self._answer_timeout, read_referral=True
        )

    async def _post(
        self, path: str, request: BaseModel, timeout: float, *, read_referral: bool
    ) -> object:
        """Post JSON and map transport failures to UpstreamUnavailable."""
        try:
            async with asyncio.timeout(timeout):
                try:
                    response = await self._binding.fetch(
                        self._base_url + path,
                        method="POST",
                        headers={"Content-Type": "application/json"},
                        body=request.model_dump_json(),
                    )
                except Exception as error:  # JS exceptions have no common base.
                    raise UpstreamUnavailable(type(error).__name__) from None
                status = response.status
                if isinstance(status, bool) or not isinstance(status, int):
                    raise UpstreamUnavailable("InvalidStatus")
                if not 200 <= status < 300:
                    referral = None
                    if read_referral and status == 503:
                        referral = await _safe_referral(response)
                    raise UpstreamUnavailable(f"http_{status}", referral)
                try:
                    return await response.json()
                except ValueError:
                    raise MalformedUpstream() from None
        except TimeoutError:
            raise UpstreamUnavailable("TimeoutError") from None


async def _safe_referral(response: BindingResponse) -> str | None:
    """Extract a plausible referral from an unavailable body, else None."""
    try:
        body = await response.json()
    except Exception:  # Body is optional diagnostics only.
        return None
    if isinstance(body, dict):
        referral = body.get("referral")
        if (
            isinstance(referral, str)
            and referral.strip()
            and len(referral) <= MAX_REFERRAL_LENGTH
        ):
            return referral
    return None


_current_env: ContextVar[object | None] = ContextVar("rag_env", default=None)


def request_binding(name: str) -> object | None:
    """Return a binding of the current request's env, or None if absent."""
    return getattr(_current_env.get(), name, None)


class RequestScopedRagClient:
    """Resolve the RAG binding lazily from the environment of the current request."""

    def __init__(
        self,
        *,
        binding_name: str = "RAG",
        base_url: str,
        retrieval_timeout: float,
        answer_timeout: float,
    ) -> None:
        """Keep transport limits; the binding itself is looked up per call."""
        self._binding_name = binding_name
        self._limits = {
            "base_url": base_url,
            "retrieval_timeout": retrieval_timeout,
            "answer_timeout": answer_timeout,
        }

    def _client(self) -> RagServiceBindingClient:
        """Build a client around the current request's binding or fail closed."""
        binding = request_binding(self._binding_name)
        if binding is None:
            raise UpstreamUnavailable("BindingMissing")
        return RagServiceBindingClient(binding, **self._limits)

    async def query(self, request: RetrievalQuery) -> object:
        """Delegate retrieval to the request's binding."""
        return await self._client().query(request)

    async def answer(self, request: ChatRequest) -> object:
        """Delegate answering to the request's binding."""
        return await self._client().answer(request)


Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Any]]
Send = Callable[[Any], Awaitable[None]]


class RequestEnvMiddleware:
    """ASGI middleware exposing `scope["env"]` to request-scoped adapters."""

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]) -> None:
        """Wrap the composed ASGI application."""
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Bind the request env for the duration of the call."""
        token = _current_env.set(scope.get("env"))
        try:
            await self._app(scope, receive, send)
        finally:
            _current_env.reset(token)

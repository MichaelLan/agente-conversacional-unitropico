from collections.abc import Awaitable, Callable


class FetchTransportError(OSError):
    """Signal a runtime fetch failure using only the original exception type name."""


class _WorkersResponse:
    """Expose integer status and async JSON decoding over a Workers response."""

    def __init__(self, status: int, response: object):
        """Keep the normalized status and the underlying runtime response."""
        self.status = status
        self._response = response

    async def json(self) -> object:
        """Decode the body; runtime-specific errors become ValueError."""
        try:
            return await self._response.json()  # type: ignore[attr-defined]
        except ValueError:
            raise
        except Exception as error:  # Pyodide JsException is not importable on host.
            raise ValueError(type(error).__name__) from None


class WorkersFetch:
    """Adapt ``workers.fetch`` (Pyodide) to the callable GatewayClient expects.

    The SDK function is injected so this module never imports the ``js`` bridge.
    """

    def __init__(self, fetch: Callable[..., Awaitable[object]]):
        """Receive the runtime fetch function at composition time."""
        self._fetch = fetch

    async def __call__(
        self,
        url: str,
        *,
        method: str,
        headers: dict[str, str],
        body: str,
        redirect: str,
    ) -> _WorkersResponse:
        """Forward one request; map runtime exceptions to FetchTransportError."""
        try:
            response = await self._fetch(
                url, method=method, headers=headers, body=body, redirect=redirect
            )
            status = response.status  # type: ignore[attr-defined]
        except Exception as error:  # JS-side failures surface as opaque types.
            raise FetchTransportError(type(error).__name__) from None
        return _WorkersResponse(status if type(status) is int else -1, response)

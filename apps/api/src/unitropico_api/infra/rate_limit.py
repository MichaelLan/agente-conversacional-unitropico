from collections.abc import Callable, Mapping
from typing import Protocol

from unitropico_api.domain.errors import RateLimiterUnavailable


class RatelimitBinding(Protocol):
    """Subset of the Workers Rate Limiting binding (`env.X.limit({key})`)."""

    async def limit(self, *, key: str) -> object:
        """Count one request for `key`; resolves to `{success: bool}`."""
        ...


def _success(result: object) -> object:
    """Read `success` from a converted mapping or an attribute-style proxy."""
    if isinstance(result, Mapping):
        return result.get("success")
    return getattr(result, "success", None)


class WorkersRateLimiter:
    """RateLimiter adapter over a Rate Limiting binding resolved per call.

    Limit and period live in wrangler.jsonc (`ratelimits[].simple`); the
    adapter only forwards the key.
    """

    def __init__(self, binding: Callable[[], RatelimitBinding | None]) -> None:
        """Receive a provider of the current request's binding."""
        self._binding = binding

    async def allow(self, key: str) -> bool:
        """Map `{success}` to a bool; a missing binding or error is unavailable."""
        binding = self._binding()
        if binding is None:
            raise RateLimiterUnavailable("BindingMissing")
        try:
            # The SDK wrapper turns keyword arguments into the options object.
            result = await binding.limit(key=key)
        except Exception as error:  # JS exceptions have no common base.
            raise RateLimiterUnavailable(type(error).__name__) from None
        success = _success(result)
        if not isinstance(success, bool):
            raise RateLimiterUnavailable("InvalidResult")
        return success

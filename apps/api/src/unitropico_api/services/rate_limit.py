import hashlib
import hmac
from typing import Literal, Protocol

from unitropico_api.core.logger import get_logger
from unitropico_api.domain.errors import RateLimiterUnavailable

_log = get_logger()

ANONYMOUS_KEY = "anonymous"
# Used when the optional RATE_LIMIT_SALT secret is absent; it only keeps raw IPs
# out of the limiter, it is not a secret.
DEFAULT_SALT = "unitropico-rate-limit-v1"

Decision = Literal["allowed", "limited", "unavailable"]


class RateLimiter(Protocol):
    """Port to a per-key request quota; the quota itself lives in the adapter."""

    async def allow(self, key: str) -> bool:
        """Count one request for `key`; False when its quota is exhausted.

        Raises:
            RateLimiterUnavailable: the limiter is missing or failed.
        """
        ...


def client_key(client_ip: str | None, salt: str) -> str:
    """Derive a stable, non-reversible client key; never the IP itself.

    HMAC-SHA256 of the IP under `salt`, truncated to 32 hex characters. Without
    an IP (local development) every client shares the `anonymous` key.
    """
    ip = (client_ip or "").strip()
    if not ip:
        return ANONYMOUS_KEY
    digest = hmac.new(salt.encode(), ip.encode(), hashlib.sha256).hexdigest()
    return digest[:32]


class RateLimitGuard:
    """Apply a limiter to one route, failing open unless the limit is required."""

    def __init__(
        self,
        limiter: RateLimiter | None,
        *,
        route: str,
        salt: str = DEFAULT_SALT,
        required: bool = False,
    ) -> None:
        """Receive the port and plain policy values; never settings or env."""
        self._limiter = limiter
        self._route = route
        self._salt = salt or DEFAULT_SALT
        self._required = required

    async def check(self, client_ip: str | None) -> Decision:
        """Return the decision for this client; logs never include IP or key."""
        try:
            if self._limiter is None:
                raise RateLimiterUnavailable("no_limiter")
            allowed = await self._limiter.allow(client_key(client_ip, self._salt))
        except RateLimiterUnavailable as error:
            mode = "closed" if self._required else "open"
            _log.warning(
                "rate limiter unavailable route=%s reason=%s fail=%s",
                self._route,
                error.reason,
                mode,
            )
            return "unavailable" if self._required else "allowed"
        if not allowed:
            _log.info("rate limited route=%s", self._route)
            return "limited"
        return "allowed"

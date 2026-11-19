import hashlib
import hmac
import ipaddress
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


def normalize_client(client_ip: str | None) -> str:
    """Canonical network of a client: IPv4 as is, IPv6 reduced to its /64.

    A client usually controls a whole /64, so keying on the full IPv6 address
    would let it rotate addresses to evade the limit. IPv4-mapped IPv6 counts
    as IPv4. Missing or invalid input yields `anonymous`.
    """
    try:
        address = ipaddress.ip_address((client_ip or "").strip())
    except ValueError:
        return ANONYMOUS_KEY
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        prefix = int(address) >> 64 << 64
        return f"{ipaddress.IPv6Address(prefix)}/64"
    return str(address)


def client_key(client_ip: str | None, salt: str) -> str:
    """Derive a stable, non-reversible client key; never the IP itself.

    HMAC-SHA256 of the normalized client (full IPv4 or IPv6 /64) under `salt`,
    truncated to 32 hex characters. Without a valid IP every client shares the
    `anonymous` key.
    """
    network = normalize_client(client_ip)
    if network == ANONYMOUS_KEY:
        return ANONYMOUS_KEY
    digest = hmac.new(salt.encode(), network.encode(), hashlib.sha256).hexdigest()
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

class MalformedUpstream(Exception):
    """Signal a rejected dependency payload without exposing its contents."""


class UpstreamUnavailable(Exception):
    """Signal a missing or failing dependency; carries only safe diagnostics."""

    def __init__(self, reason: str, referral: str | None = None) -> None:
        """Keep a content-free reason (type or status) and an optional referral."""
        super().__init__(reason)
        self.reason = reason
        self.referral = referral


class RateLimiterUnavailable(Exception):
    """Signal a missing or failing rate limiter; carries only a safe reason."""

    def __init__(self, reason: str) -> None:
        """Keep a content-free reason (binding missing, error type)."""
        super().__init__(reason)
        self.reason = reason

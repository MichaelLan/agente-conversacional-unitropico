import posixpath
import re
from collections.abc import Callable

from fastapi import Depends, FastAPI, Request, params
from fastapi.responses import JSONResponse

from unitropico_api.services.rate_limit import RateLimitGuard

CLIENT_IP_HEADER = "cf-connecting-ip"
RETRY_AFTER_SECONDS = 60
RATE_LIMITED_MESSAGE = (
    "Has realizado muchas consultas seguidas. Espera un momento e inténtalo de nuevo."
)
PUBLIC_POST_PATHS = frozenset({"/chat", "/retrieval/query"})
# /chat allows 500 + 6 x 2000 code points (~50 KB of UTF-8 at worst); 64 KiB
# leaves room for JSON overhead while refusing oversized bodies before buffering.
MAX_PUBLIC_BODY_BYTES = 64 * 1024
_SLASHES = re.compile(r"/{2,}")

Body = dict[str, object]


def error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    """Public error envelope, same shape as the admin one."""
    return {"error": {"code": code, "message": message}}


class PublicRejection(Exception):
    """Short-circuit a public route with a ready status, headers and body."""

    def __init__(self, status: int, body: Body, headers: dict[str, str]) -> None:
        """Keep the response parts."""
        super().__init__(status)
        self.status, self.body, self.headers = status, body, headers


def rate_limit(
    guard: RateLimitGuard, unavailable: Callable[[], Body]
) -> params.Depends:
    """Build a route dependency that consults the guard before the handler runs.

    429 `rate_limited` with `Retry-After` when limited; with a required limiter
    that is unavailable, 503 with the route's own `unavailable` body.
    """

    async def enforce(request: Request) -> None:
        """Key on CF-Connecting-IP (hashed by the guard, never logged)."""
        decision = await guard.check(request.headers.get(CLIENT_IP_HEADER))
        if decision == "limited":
            raise PublicRejection(
                429,
                error_body("rate_limited", RATE_LIMITED_MESSAGE),
                {"Retry-After": str(RETRY_AFTER_SECONDS)},
            )
        if decision == "unavailable":
            raise PublicRejection(503, unavailable(), {})

    return Depends(enforce)


def install_public_rejection_handler(app: FastAPI) -> None:
    """Render PublicRejection as JSON with its headers."""

    async def handler(_: Request, error: Exception) -> JSONResponse:
        """Return the prepared response."""
        assert isinstance(error, PublicRejection)
        return JSONResponse(error.body, status_code=error.status, headers=error.headers)

    app.add_exception_handler(PublicRejection, handler)


def early_public_body_rejection(
    method: str,
    path: str,
    header: Callable[[str], str | None],
    max_bytes: int = MAX_PUBLIC_BODY_BYTES,
) -> tuple[int, dict[str, str], dict[str, dict[str, str]]] | None:
    """Refuse oversized public POST bodies from the headers alone.

    The Workers ASGI bridge buffers the body before FastAPI runs. For POST
    /chat and /retrieval/query: 413 when Content-Length exceeds `max_bytes`,
    411 when it is missing (or invalid) on a chunked body. Returns
    (status, headers, body) or None to continue.
    """
    normalized = posixpath.normpath(_SLASHES.sub("/", path))
    if method.upper() != "POST" or normalized not in PUBLIC_POST_PATHS:
        return None
    length = (header("content-length") or "").strip()
    if length.isascii() and length.isdigit():
        if int(length) > max_bytes:
            return (
                413,
                {},
                error_body(
                    "payload_too_large",
                    f"La solicitud supera el límite de {max_bytes} bytes.",
                ),
            )
        return None
    chunked = "chunked" in (header("transfer-encoding") or "").lower()
    if length or chunked:
        return (
            411,
            {},
            error_body("length_required", "Se requiere el encabezado Content-Length."),
        )
    return None

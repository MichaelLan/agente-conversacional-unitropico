"""Minimal JSON-over-HTTP client on the standard library (``urllib``)."""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class HttpResult:
    """Outcome of one POST: status (``None`` on transport error) and timing."""

    status: int | None
    body: dict | None
    latency_ms: float
    error: str | None = None


class JsonClient:
    """POST JSON to ``base_url + path`` and measure client-side latency.

    Redirects are not followed blindly: ``urllib`` follows them by default, so
    the opener installs a handler that refuses them (an evaluation should hit
    exactly the declared endpoint).
    """

    def __init__(self, base_url: str, timeout: float = 60.0) -> None:
        """Store the base URL (without trailing slash) and per-request timeout."""
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url debe empezar por http:// o https://")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect())

    def post(self, path: str, payload: dict) -> HttpResult:
        """Send one request; never raises for HTTP or network failures."""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "unitropico-eval/0.1",
            },
        )
        started = time.perf_counter()
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            raw = error.read()
            status = error.code
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            elapsed = (time.perf_counter() - started) * 1000
            return HttpResult(None, None, elapsed, f"{type(error).__name__}: {error}")
        elapsed = (time.perf_counter() - started) * 1000
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError, UnicodeDecodeError:
            return HttpResult(status, None, elapsed, "respuesta no JSON")
        if body is not None and not isinstance(body, dict):
            return HttpResult(status, None, elapsed, "JSON no es un objeto")
        return HttpResult(status, body, elapsed)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None

"""Firecrawl Parse v2 adapter (``POST /v2/parse``, multipart ``file`` + ``options``).

``workers.fetch`` in workers-runtime-sdk 1.9.6 cannot send ``FormData`` (it
sends ``str(form)``, or a different boundary with ``form.js_object``). The
verified pattern is ``js.fetch`` with ``FormData.js_object`` as body and the
init converted with ``to_js(..., dict_converter=Object.fromEntries)``; the
runtime sets the multipart ``Content-Type`` and boundary itself. The runtime
objects are injected (``FirecrawlRuntime``) so tests never import ``js``.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from unitropico_ingestion.core.logger import logger
from unitropico_ingestion.domain.ingestion import ErrorCode, IngestionError

REQUEST_TIMEOUT_MS = 300_000
"""Firecrawl server-side timeout (documented maximum)."""

CLIENT_TIMEOUT_SECONDS = 330.0
"""Client wait: the server timeout plus upload/transfer margin."""

_RETRYABLE_STATUS = frozenset({408, 409, 425, 429})


@dataclass(frozen=True)
class FirecrawlRuntime:
    """Pyodide/Workers objects needed to send a multipart request."""

    fetch: Callable[..., Any]
    to_js: Callable[..., Any]
    from_entries: Any
    form_data: Callable[[], Any]
    blob: Callable[..., Any]


def pyodide_runtime() -> FirecrawlRuntime:
    """Import the runtime objects lazily (only inside the Worker)."""
    import js
    from js import Object
    from pyodide.ffi import to_js
    from workers import Blob, FormData

    return FirecrawlRuntime(
        fetch=js.fetch,
        to_js=to_js,
        from_entries=Object.fromEntries,
        form_data=FormData,
        blob=Blob,
    )


def parse_options(max_pages: int) -> dict:
    """Return the ``options`` JSON: OCR on every page, per-page Markdown."""
    return {
        "formats": ["markdown"],
        "timeout": REQUEST_TIMEOUT_MS,
        "parsers": [
            {
                "type": "pdf",
                "mode": "ocr",
                "maxPages": max_pages,
                "pages": True,
                "blocks": True,
                "pageMarkers": True,
            }
        ],
    }


class FirecrawlOcrClient:
    """Implement ``OcrClient``; one request per call, retries belong to the step."""

    def __init__(
        self,
        *,
        api_key: str | None,
        runtime: FirecrawlRuntime | Callable[[], FirecrawlRuntime],
        url: str,
        max_pages: int,
        timeout: float = CLIENT_TIMEOUT_SECONDS,
    ):
        """Store configuration; a missing key fails only when OCR is needed."""
        self._key = api_key
        self._runtime = runtime
        self._url = url
        self._max_pages = max_pages
        self._timeout = timeout

    def _resolve_runtime(self) -> FirecrawlRuntime:
        if isinstance(self._runtime, FirecrawlRuntime):
            return self._runtime
        self._runtime = self._runtime()
        return self._runtime

    async def parse(self, pdf: bytes, *, filename: str) -> bytes:
        """Send the PDF and return the raw JSON body on HTTP 200.

        4xx (except 408/409/425/429) raise a permanent ``ocr_failed``; 5xx,
        throttling, timeouts and transport errors raise a retryable one.
        """
        if not self._key:
            raise IngestionError(
                ErrorCode.CONFIG_MISSING, "firecrawl_api_key", permanent=True
            )
        runtime = self._resolve_runtime()
        form = runtime.form_data()
        form.append(
            "file",
            runtime.blob([pdf], content_type="application/pdf"),
            filename=filename,
        )
        form["options"] = json.dumps(parse_options(self._max_pages))
        init = runtime.to_js(
            {
                "method": "POST",
                "body": form.js_object,
                "headers": {"Authorization": f"Bearer {self._key}"},
                "redirect": "manual",
            },
            dict_converter=runtime.from_entries,
        )
        try:
            async with asyncio.timeout(self._timeout):
                response = await runtime.fetch(self._url, init)
                status = response.status
                body = await response.text()
        except TimeoutError:
            logger.warning("ocr_request_failed reason=timeout")
            raise IngestionError(ErrorCode.OCR_FAILED, "timeout") from None
        except Exception as error:  # JS errors are opaque on the host
            name = type(error).__name__
            logger.warning("ocr_request_failed reason=%s", name)
            raise IngestionError(ErrorCode.OCR_FAILED, f"transport={name}") from None
        if type(status) is not int:
            raise IngestionError(ErrorCode.OCR_FAILED, "invalid_status")
        if status != 200:
            permanent = 400 <= status < 500 and status not in _RETRYABLE_STATUS
            logger.warning("ocr_request_failed status=%s", status)
            raise IngestionError(
                ErrorCode.OCR_FAILED, f"status={status}", permanent=permanent
            )
        if not isinstance(body, str):
            raise IngestionError(ErrorCode.OCR_FAILED, "invalid_body", permanent=True)
        return body.encode("utf-8")

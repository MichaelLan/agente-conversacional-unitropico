"""Parse a Firecrawl Parse v2 response into physical pages.

Expected shape (``parsers: [{"type": "pdf", "mode": "ocr", "pages": true,
"blocks": true, "pageMarkers": true}]``)::

    {"success": true,
     "data": {"markdown": "...<!-- page 2 -->...",
              "pages": [{"pageNumber": 1, "markdown": "..."}, ...],
              "blocks": [{"pageNumber": 1, "status": "ok", "items": [...]}, ...],
              "metadata": {"numPages": 2, "totalPages": 2, ...}}}
"""

import json

from unitropico_ingestion.domain.ingestion import (
    ErrorCode,
    IngestionError,
    OcrPage,
    OcrResult,
)
from unitropico_ingestion.services.preprocess import pages_from_markdown


def _invalid(detail: str) -> IngestionError:
    return IngestionError(ErrorCode.OCR_FAILED, detail, permanent=True)


def load_response(raw: bytes) -> dict:
    """Decode the raw body and require ``success: true`` with a ``data`` object."""
    try:
        payload = json.loads(raw)
    except ValueError, TypeError:
        raise _invalid("invalid_json") from None
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise _invalid("unsuccessful_response")
    if not isinstance(payload.get("data"), dict):
        raise _invalid("missing_data")
    return payload


def _positive_int(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def parse_ocr_response(raw: bytes) -> OcrResult:
    """Return pages keyed by physical page, the page count and failed pages.

    ``data.pages`` (one entry per physical page) is preferred; without it the
    document Markdown is split on ``<!-- page N -->`` markers. A result whose
    ``totalPages`` exceeds ``numPages`` was truncated by ``maxPages`` and is
    rejected rather than indexed partially.
    """
    data = load_response(raw)["data"]
    pages: list[OcrPage] = []
    raw_pages = data.get("pages")
    if isinstance(raw_pages, list) and raw_pages:
        for item in raw_pages:
            if not isinstance(item, dict):
                raise _invalid("invalid_page")
            number, markdown = item.get("pageNumber"), item.get("markdown")
            if _positive_int(number) is None or not isinstance(markdown, str):
                raise _invalid("invalid_page")
            pages.append(OcrPage(number, markdown))
    elif isinstance(data.get("markdown"), str):
        pages = [
            OcrPage(page.page_number, page.text)
            for page in pages_from_markdown(data["markdown"])
        ]
    else:
        raise _invalid("missing_pages")
    numbers = [page.page_number for page in pages]
    if len(set(numbers)) != len(numbers):
        raise _invalid("duplicate_page")

    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    parsed = _positive_int(metadata.get("numPages"))
    total = _positive_int(metadata.get("totalPages"))
    if parsed is not None and total is not None and total > parsed:
        raise _invalid(f"truncated pages={parsed} total={total}")

    failed: list[int] = []
    blocks = data.get("blocks")
    if isinstance(blocks, list):
        for group in blocks:
            if (
                isinstance(group, dict)
                and group.get("status") == "failed"
                and _positive_int(group.get("pageNumber")) is not None
            ):
                failed.append(group["pageNumber"])

    page_count = max([total or 0, parsed or 0, *numbers, 0])
    if page_count == 0:
        raise IngestionError(ErrorCode.OCR_EMPTY, "no_pages", permanent=True)
    return OcrResult(
        pages=tuple(sorted(pages, key=lambda page: page.page_number)),
        page_count=page_count,
        failed_pages=tuple(sorted(set(failed))),
    )

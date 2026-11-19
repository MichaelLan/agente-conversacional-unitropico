import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence

from unitropico_ingestion.domain.chunking import PageText
from unitropico_ingestion.services.headings import is_structural_heading

PAGE_MARKER = re.compile(r"<!--\s*page\s+(\d+)\s*-->", re.IGNORECASE)
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))
_HORIZONTAL_SPACE = re.compile(r"[ \t\f\v  -   　]+")
_MIN_LETTERS_FOR_BOILERPLATE = 4


def pages_from_markdown(
    markdown: str,
    *,
    first_page: int = 1,
    page_map: Mapping[int, int] | None = None,
) -> list[PageText]:
    """Split Markdown with ``<!-- page N -->`` markers into physical pages.

    Text before the first marker belongs to ``first_page``. ``page_map``
    translates fragment page numbers (as written in the markers) to physical
    pages, e.g. when only pages 13–14 of a PDF were sent as a 2-page fragment.
    """
    pages: list[PageText] = []
    current, start = first_page, 0
    for match in PAGE_MARKER.finditer(markdown):
        pages.append(
            PageText(_physical(current, page_map), markdown[start : match.start()])
        )
        current, start = int(match.group(1)), match.end()
    pages.append(PageText(_physical(current, page_map), markdown[start:]))
    return [page for page in pages if page.text.strip()]


def _physical(page: int, page_map: Mapping[int, int] | None) -> int:
    """Map a fragment page to its physical page when a map is given."""
    return page_map.get(page, page) if page_map else page


def normalize_text(text: str) -> str:
    """Apply NFC, drop page markers and invisible chars, collapse spaces per line.

    Line structure is kept (tables and headings depend on it); runs of blank
    lines collapse into one.
    """
    text = unicodedata.normalize("NFC", text).translate(_INVISIBLE)
    text = PAGE_MARKER.sub("\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = [_HORIZONTAL_SPACE.sub(" ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def normalize_pages(pages: Sequence[PageText]) -> list[PageText]:
    """Normalize every page, drop pages without visible text and sort by page."""
    if len({page.page_number for page in pages}) != len(pages):
        raise ValueError("duplicate page_number")
    normalized = (PageText(p.page_number, normalize_text(p.text)) for p in pages)
    return sorted((p for p in normalized if p.text), key=lambda p: p.page_number)


def boilerplate_key(line: str) -> str:
    """Return a comparison key for running headers/footers (case/space-blind)."""
    return " ".join(line.casefold().split())


def strip_repeated_lines(
    pages: Sequence[PageText],
    *,
    min_pages: int = 3,
    min_fraction: float = 0.25,
) -> list[PageText]:
    """Remove running headers/footers repeated across pages, keeping the first.

    A line is boilerplate when its key appears on at least ``min_pages`` pages
    and on at least ``min_fraction`` of the non-empty pages. Table rows, lines
    with fewer than four letters and structural headings are never removed,
    because repeated table headers and short markers carry meaning.
    """
    if len(pages) < min_pages:
        return list(pages)
    counts: Counter[str] = Counter()
    for page in pages:
        counts.update({boilerplate_key(line) for line in page.text.split("\n")})
    threshold = max(min_pages, min_fraction * len(pages))
    repeated = {
        key
        for key, count in counts.items()
        if count >= threshold
        and sum(char.isalpha() for char in key) >= _MIN_LETTERS_FOR_BOILERPLATE
    }
    seen: set[str] = set()
    result: list[PageText] = []
    for page in pages:
        kept: list[str] = []
        for line in page.text.split("\n"):
            key = boilerplate_key(line)
            removable = (
                key in repeated
                and key in seen
                and not line.startswith(("|", "<"))
                and not is_structural_heading(line)
            )
            seen.add(key)
            if not removable:
                kept.append(line)
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
        if text:
            result.append(PageText(page.page_number, text))
    return result


def prepare_pages(
    pages: Sequence[PageText], *, strip_boilerplate: bool = True
) -> list[PageText]:
    """Normalize pages and optionally strip running headers and footers."""
    normalized = normalize_pages(pages)
    return strip_repeated_lines(normalized) if strip_boilerplate else normalized

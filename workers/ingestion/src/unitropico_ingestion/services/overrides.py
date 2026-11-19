"""Apply the administrator's full-page text overrides to prepared pages.

Rules (documented in the README):

* An override replaces the whole *prepared* text of its page (after
  ``Chunker.prepare``), before the literal corrections of that page, which are
  then matched against the override text.
* The override text is prepared by the same chunker preparation, called with
  that single page: normalization (NFC, invisible characters and page markers
  removed, horizontal spaces collapsed) and, for the structural chunker, HTML
  tables rendered as Markdown. Running headers/footers are NOT removed from
  it: that rule needs the other pages (a line repeated across pages) and the
  administrator wrote this text on purpose, so it is kept as written.
* A page without OCR text (e.g. a scanned diagram) gains the override as a new
  page, in page order. An override whose prepared text is empty (only page
  markers or an empty table) leaves the page without text.
* An override beyond the OCR ``page_count`` (possible only when it was
  recorded before the first extraction, while the page count was unknown)
  fails the version with the permanent ``correction_not_applicable``.

Errors carry only ids, page numbers and counts, never document text.
"""

from collections.abc import Callable, Sequence

from unitropico_ingestion.domain.chunking import PageText
from unitropico_ingestion.domain.corrections import AppliedOverride, PageOverride
from unitropico_ingestion.domain.ingestion import ErrorCode, IngestionError

Prepare = Callable[[Sequence[PageText]], list[PageText]]


def prepare_override(override: PageOverride, prepare: Prepare) -> str:
    """Return the override text prepared as a single page ("" when empty)."""
    prepared = prepare([PageText(override.page_number, override.text)])
    return prepared[0].text if prepared else ""


def apply_overrides(
    pages: Sequence[PageText],
    overrides: Sequence[PageOverride],
    *,
    prepare: Prepare,
    page_count: int,
) -> tuple[list[PageText], list[AppliedOverride]]:
    """Return the pages with overrides applied and the applied overrides.

    ``pages`` are prepared pages; without overrides they are returned
    unchanged. ``prepare`` is ``Chunker.prepare``.
    """
    if not overrides:
        return list(pages), []
    texts = {page.page_number: page.text for page in pages}
    applied: list[AppliedOverride] = []
    for override in sorted(overrides, key=lambda o: o.page_number):
        if override.page_number > page_count:
            raise IngestionError(
                ErrorCode.CORRECTION_NOT_APPLICABLE,
                f"override={override.id} page={override.page_number} "
                f"page_count={page_count}",
                permanent=True,
            )
        texts[override.page_number] = prepare_override(override, prepare)
        applied.append(AppliedOverride(override.id, override.page_number))
    result = [PageText(number, text) for number, text in sorted(texts.items()) if text]
    return result, applied

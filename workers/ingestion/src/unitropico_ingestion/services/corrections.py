"""Apply the administrator's literal OCR corrections to page text.

Matching rules (documented in the README):

* The page text is first normalized with ``preprocess.normalize_text`` (the
  same NFC, invisible-character, page-marker and horizontal-space rules the
  chunkers apply), so the result is identical to what the chunker would see.
* ``find_text`` is normalized the same way and compared literally: case,
  accents and punctuation must match exactly. The only relaxation is
  whitespace: any run of whitespace in ``find_text`` (spaces or line breaks)
  matches any run of whitespace in the page. This lets the administrator copy
  text from a chunk, where wrapped lines are joined with spaces.
* ``find_text`` must occur exactly once in its page, counting overlapping
  occurrences; otherwise the version fails with ``correction_not_applicable``.
* Corrections of the same page are applied in order (creation time, then id),
  each one on the text left by the previous ones.

Errors carry only ids, page numbers and counts, never document text.
"""

import re
import unicodedata
from collections.abc import Sequence

from unitropico_ingestion.domain.chunking import PageText
from unitropico_ingestion.domain.corrections import AppliedCorrection, TextCorrection
from unitropico_ingestion.domain.ingestion import ErrorCode, IngestionError
from unitropico_ingestion.services.preprocess import normalize_text


def _pattern(find_text: str) -> re.Pattern[str] | None:
    tokens = normalize_text(find_text).split()
    if not tokens:  # e.g. only invisible characters or a page marker
        return None
    return re.compile(r"\s+".join(map(re.escape, tokens)))


def count_matches(text: str, find_text: str) -> int:
    """Count occurrences of ``find_text`` in ``text``, overlapping included."""
    pattern = _pattern(find_text)
    if pattern is None:
        return 0
    return sum(1 for _ in re.finditer(f"(?=(?:{pattern.pattern}))", text))


def _not_applicable(correction: TextCorrection, matches: int) -> IngestionError:
    return IngestionError(
        ErrorCode.CORRECTION_NOT_APPLICABLE,
        f"correction={correction.id} page={correction.page_number} matches={matches}",
        permanent=True,
    )


def apply_corrections(
    pages: Sequence[PageText], corrections: Sequence[TextCorrection]
) -> tuple[list[PageText], list[AppliedCorrection]]:
    """Return the corrected pages and the applied corrections, in order.

    Pages without corrections are returned unchanged, so a version without
    active corrections produces exactly the same chunks as before. Raises a
    permanent ``IngestionError(correction_not_applicable)`` when a correction
    matches zero or several times (or targets a page absent from the OCR).
    """
    if not corrections:
        return list(pages), []
    by_page = {page.page_number: page for page in pages}
    texts: dict[int, str] = {}
    applied: list[AppliedCorrection] = []
    for correction in corrections:
        number = correction.page_number
        if number not in texts:
            page = by_page.get(number)
            texts[number] = normalize_text(page.text) if page is not None else ""
        text = texts[number]
        matches = count_matches(text, correction.find_text)
        if matches != 1:
            raise _not_applicable(correction, matches)
        pattern = _pattern(correction.find_text)
        found = pattern.search(text) if pattern is not None else None
        if found is None:  # unreachable when matches == 1
            raise _not_applicable(correction, 0)
        replacement = unicodedata.normalize("NFC", correction.replace_text)
        texts[number] = text[: found.start()] + replacement + text[found.end() :]
        applied.append(AppliedCorrection(correction.id, number))
    corrected = [
        PageText(page.page_number, texts[page.page_number])
        if page.page_number in texts
        else page
        for page in pages
    ]
    return corrected, applied

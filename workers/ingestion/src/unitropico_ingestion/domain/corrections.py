from dataclasses import dataclass


@dataclass(frozen=True)
class TextCorrection:
    """Active literal OCR correction of one physical page (``text_corrections``).

    Recorded by the administrator through the API; the ingestion ``chunk`` step
    applies it to the page text before chunking.
    """

    id: str
    page_number: int
    find_text: str
    replace_text: str

    def __post_init__(self) -> None:
        """Reject values the schema would never store."""
        if type(self.page_number) is not int or self.page_number < 1:
            raise ValueError("page_number must be a positive int")
        if not isinstance(self.find_text, str) or not self.find_text.strip():
            raise ValueError("find_text must be a nonblank str")
        if not isinstance(self.replace_text, str):
            raise TypeError("replace_text must be a str")


@dataclass(frozen=True)
class PageOverride:
    """Active full-text replacement of one physical page (``page_overrides``).

    The chunk step replaces the prepared page text with ``text`` prepared the
    same way (one page), before the literal corrections of that page.
    """

    id: str
    page_number: int
    text: str

    def __post_init__(self) -> None:
        """Reject values the schema would never store."""
        if type(self.page_number) is not int or self.page_number < 1:
            raise ValueError("page_number must be a positive int")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("text must be a nonblank str")


@dataclass(frozen=True)
class CorrectionSet:
    """Active corrections and page overrides read in one snapshot with the revision.

    ``revision`` is ``document_versions.corrections_rev`` (bumped by every
    create or logical delete of a correction or an override). The chunk step
    passes it back when it writes the chunks, so the "corrections pending" flag
    is cleared only if nothing changed since this read.
    """

    revision: int
    corrections: tuple[TextCorrection, ...] = ()
    overrides: tuple[PageOverride, ...] = ()

    def __post_init__(self) -> None:
        """At most one override per page (unique index in D1)."""
        pages = [override.page_number for override in self.overrides]
        if len(set(pages)) != len(pages):
            raise ValueError("duplicate override page_number")


@dataclass(frozen=True)
class AppliedOverride:
    """Trace of one page override applied during chunking."""

    id: str
    page_number: int


@dataclass(frozen=True)
class AppliedCorrection:
    """Trace of one correction applied during chunking."""

    id: str
    page_number: int

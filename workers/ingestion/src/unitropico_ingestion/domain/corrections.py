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
class AppliedCorrection:
    """Trace of one correction applied during chunking."""

    id: str
    page_number: int

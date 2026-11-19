from bisect import bisect_right
from collections.abc import Sequence

from unitropico_ingestion.domain.chunking import Chunk, PageText, build_chunk
from unitropico_ingestion.services.preprocess import prepare_pages

FIXED_CHUNKER_VERSION = "fixed-v1"
_PAGE_SEPARATOR = "\n\n"


class FixedSizeChunker:
    """Baseline: fixed-size character windows with overlap, ignoring structure.

    Shares the structural chunker's preprocessing (normalization, marker and
    boilerplate removal) so evaluations isolate the segmentation strategy.
    Windows end at the last space when possible; ``section`` is always None.
    """

    def __init__(
        self,
        *,
        size: int = 1000,
        overlap: int = 150,
        strip_boilerplate: bool = True,
    ) -> None:
        """Validate window size and overlap (characters)."""
        if size < 50:
            raise ValueError("size must be at least 50")
        if not 0 <= overlap < size // 2:
            raise ValueError("require 0 <= overlap < size // 2")
        self.size = size
        self.overlap = overlap
        self.strip_boilerplate = strip_boilerplate

    @property
    def version(self) -> str:
        """Return the constant identifying this baseline."""
        return FIXED_CHUNKER_VERSION

    def prepare(self, pages: Sequence[PageText]) -> list[PageText]:
        """Return the prepared pages (normalized, without boilerplate)."""
        return prepare_pages(pages, strip_boilerplate=self.strip_boilerplate)

    def chunk(self, pages: Sequence[PageText], *, version_id: str) -> list[Chunk]:
        """Split the concatenated pages into overlapping windows."""
        return self.chunk_prepared(self.prepare(pages), version_id=version_id)

    def chunk_prepared(
        self, prepared: Sequence[PageText], *, version_id: str
    ) -> list[Chunk]:
        """Chunk pages already returned by ``prepare`` (possibly corrected)."""
        text = _PAGE_SEPARATOR.join(page.text for page in prepared)
        starts: list[int] = []
        offset = 0
        for page in prepared:
            starts.append(offset)
            offset += len(page.text) + len(_PAGE_SEPARATOR)
        numbers = [page.page_number for page in prepared]

        def page_at(position: int) -> int:
            return numbers[bisect_right(starts, position) - 1]

        chunks: list[Chunk] = []
        for start, end in self._windows(text):
            raw = text[start:end]
            lead = len(raw) - len(raw.lstrip())
            body = raw.strip()
            first = start + lead
            chunks.append(
                build_chunk(
                    version_id=version_id,
                    chunker_version=self.version,
                    ordinal=len(chunks),
                    text=body,
                    section=None,
                    page_start=page_at(first),
                    page_end=page_at(first + len(body) - 1),
                )
            )
        return chunks

    def _windows(self, text: str) -> list[tuple[int, int]]:
        """Return [start, end) windows snapped to spaces, with overlap."""
        windows: list[tuple[int, int]] = []
        start = 0
        while start < len(text):
            end = min(start + self.size, len(text))
            if end < len(text):
                space = text.rfind(" ", start + self.size // 2, end)
                newline = text.rfind("\n", start + self.size // 2, end)
                end = max(space, newline) if max(space, newline) > start else end
            if text[start:end].strip():
                windows.append((start, end))
            if end >= len(text):
                break
            next_start = max(end - self.overlap, start + 1)
            # Move forward to a word start so windows never begin mid-word.
            while 0 < next_start < end and not text[next_start - 1].isspace():
                next_start += 1
            start = next_start if next_start < end else end
        return windows

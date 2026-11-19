from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

MAX_CHUNK_ID_BYTES = 64
"""Vectorize rejects vector ids longer than 64 bytes."""


@dataclass(frozen=True)
class PageText:
    """Extracted text or Markdown of one physical PDF page (1-based)."""

    page_number: int
    text: str

    def __post_init__(self) -> None:
        """Reject non-positive or non-integer physical page numbers."""
        if type(self.page_number) is not int or self.page_number < 1:
            raise ValueError("page_number must be a positive int")
        if not isinstance(self.text, str):
            raise TypeError("text must be a str")


@dataclass(frozen=True)
class Chunk:
    """Retrievable text unit with its exact digest and physical page range.

    ``section`` is the readable path of the most specific heading detected in the
    source text, or None when no heading was detected; it is never inferred.
    """

    id: str
    ordinal: int
    text: str
    text_sha256: str
    section: str | None
    page_start: int
    page_end: int
    chunker_version: str

    def __post_init__(self) -> None:
        """Enforce the invariants the RAG evidence contract relies on."""
        if not self.text.strip():
            raise ValueError("chunk text must not be blank")
        if self.text_sha256 != text_sha256(self.text):
            raise ValueError("text_sha256 does not match text")
        if not 0 < self.page_start <= self.page_end:
            raise ValueError("invalid page range")
        if len(self.id.encode("utf-8")) > MAX_CHUNK_ID_BYTES:
            raise ValueError("chunk id exceeds 64 bytes")
        if self.section is not None and not self.section.strip():
            raise ValueError("section must be None or nonblank")


class Chunker(Protocol):
    """Split the pages of one document version into ordered chunks."""

    @property
    def version(self) -> str:
        """Return the constant identifying the chunking strategy and its rules."""
        ...

    def prepare(self, pages: Sequence[PageText]) -> list[PageText]:
        """Return the prepared pages the chunker segments (corrections target)."""
        ...

    def chunk_prepared(
        self, prepared: Sequence[PageText], *, version_id: str
    ) -> list[Chunk]:
        """Chunk pages returned by ``prepare``; deterministic."""
        ...

    def chunk(self, pages: Sequence[PageText], *, version_id: str) -> list[Chunk]:
        """Return ``chunk_prepared(prepare(pages))``; pages without text yield none."""
        ...


def text_sha256(text: str) -> str:
    """Return the hex SHA-256 of the UTF-8 encoded text."""
    return sha256(text.encode("utf-8")).hexdigest()


def make_chunk_id(
    version_id: str, chunker_version: str, ordinal: int, digest: str
) -> str:
    """Build a deterministic ASCII chunk id that always fits in 64 bytes.

    Scheme: ``{v16}:{ordinal:04d}:{t12}`` where ``v16`` is the first 16 hex chars
    of SHA-256(``version_id`` + U+001F + ``chunker_version``) and ``t12`` the
    first 12 hex chars of the chunk text SHA-256. Hashing the version id keeps
    the length fixed (35 bytes up to ordinal 9999) whatever its length, and
    including the chunker version keeps ids of different strategies disjoint
    for the same document version.
    """
    if not version_id.strip():
        raise ValueError("version_id must not be blank")
    if ordinal < 0:
        raise ValueError("ordinal must be non-negative")
    scope = sha256(f"{version_id}\x1f{chunker_version}".encode()).hexdigest()
    return f"{scope[:16]}:{ordinal:04d}:{digest[:12]}"


def build_chunk(
    *,
    version_id: str,
    chunker_version: str,
    ordinal: int,
    text: str,
    section: str | None,
    page_start: int,
    page_end: int,
) -> Chunk:
    """Create a validated chunk computing its digest and id."""
    digest = text_sha256(text)
    return Chunk(
        id=make_chunk_id(version_id, chunker_version, ordinal, digest),
        ordinal=ordinal,
        text=text,
        text_sha256=digest,
        section=section,
        page_start=page_start,
        page_end=page_end,
        chunker_version=chunker_version,
    )

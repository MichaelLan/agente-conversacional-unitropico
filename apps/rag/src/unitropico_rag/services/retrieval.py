from collections.abc import Sequence
from hashlib import sha256
from typing import Protocol

from unitropico_rag.domain.evidence import (
    ChunkCandidate,
    RegistryEntry,
    RetrievalQuery,
    RetrievalResult,
)


class AsyncRetriever(Protocol):
    """Retrieve ordered candidates without coupling orchestration to an SDK."""

    async def retrieve(self, question: str, limit: int) -> Sequence[ChunkCandidate]:
        """Return candidates from the configured retrieval adapter."""
        ...


class AsyncEvidenceCatalog(Protocol):
    """Read authoritative publication and provenance metadata on every query."""

    async def get_current(self, chunk_id: str) -> RegistryEntry | None:
        """Return the current chunk record or None when no longer registered."""
        ...


def is_admissible(candidate: ChunkCandidate, entry: RegistryEntry | None) -> bool:
    """Require exact provenance, valid physical location and current eligibility."""
    if not isinstance(entry, RegistryEntry):
        return False
    for name in (
        "document_id",
        "version_id",
        "chunk_id",
        "source_sha256",
        "page",
        "section",
    ):
        value = getattr(candidate, name)
        if value != getattr(entry, name):
            return False
    return (
        entry.status == "published"
        and entry.requires_review is False
        and entry.formally_superseded is False
        and all(
            isinstance(value, str) and value.strip()
            for value in (
                candidate.document_id,
                candidate.version_id,
                candidate.chunk_id,
            )
        )
        and isinstance(candidate.source_sha256, str)
        and len(candidate.source_sha256) == 64
        and all(char in "0123456789abcdef" for char in candidate.source_sha256)
        and type(candidate.page) is int
        and type(entry.page) is int
        and candidate.page > 0
        and (
            candidate.section is None
            or (isinstance(candidate.section, str) and bool(candidate.section.strip()))
        )
        and isinstance(candidate.text, str)
        and bool(candidate.text.strip())
        and sha256(candidate.text.encode("utf-8")).hexdigest() == entry.text_sha256
    )


class RetrievalService:
    """Filter retrieval against current catalog state without caches or generation."""

    def __init__(
        self,
        retriever: AsyncRetriever | None = None,
        catalog: AsyncEvidenceCatalog | None = None,
    ) -> None:
        """Receive optional adapters explicitly; missing adapters mean unavailable."""
        self._retriever, self._catalog = retriever, catalog

    async def query(self, request: RetrievalQuery) -> RetrievalResult:
        """Revalidate unique chunks; discard all evidence on dependency failure."""
        if not isinstance(request, RetrievalQuery):
            raise TypeError("request must be RetrievalQuery")
        if self._retriever is None or self._catalog is None:
            return RetrievalResult("unavailable")
        evidence: list[ChunkCandidate] = []
        seen: set[tuple[str, str, str]] = set()
        try:
            candidates = await self._retriever.retrieve(request.question, request.limit)
            for candidate in candidates:
                if not isinstance(candidate, ChunkCandidate):
                    return RetrievalResult("unavailable")
                key = (candidate.document_id, candidate.version_id, candidate.chunk_id)
                if key in seen:
                    continue
                seen.add(key)
                entry = await self._catalog.get_current(candidate.chunk_id)
                if is_admissible(candidate, entry):
                    evidence.append(candidate)
                    if len(evidence) == request.limit:
                        break
        except Exception:
            return RetrievalResult("unavailable")
        return RetrievalResult(
            "evidence_found" if evidence else "abstained", tuple(evidence)
        )

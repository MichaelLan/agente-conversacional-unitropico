from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from langchain_core.retrievers import BaseRetriever

from unitropico_rag.core.logger import logger
from unitropico_rag.domain.evidence import (
    ChunkCandidate,
    Evidence,
    RegistryEntry,
    RetrievalQuery,
    RetrievalResult,
    is_admissible,
    to_evidence,
)
from unitropico_rag.services.retriever import (
    AsyncRetriever,
    ChunkRetriever,
    InvalidCandidateError,
    document_candidate,
)

__all__ = [
    "AsyncBatchEvidenceCatalog",
    "AsyncEvidenceCatalog",
    "AsyncRetriever",
    "RetrievalService",
]


class AsyncEvidenceCatalog(Protocol):
    """Read authoritative publication and provenance metadata on every query."""

    async def get_current(self, chunk_id: str) -> RegistryEntry | None:
        """Return the current chunk record or None when no longer registered."""
        ...


@runtime_checkable
class AsyncBatchEvidenceCatalog(Protocol):
    """Optional catalog extension: revalidate all candidates in one read."""

    async def get_current_many(
        self, chunk_ids: Sequence[str]
    ) -> Mapping[str, RegistryEntry]:
        """Return current entries by chunk id; unregistered ids are absent."""
        ...


class RetrievalService:
    """Filter retrieval against current catalog state without caches or generation.

    Requests ``limit * oversampling`` candidates so that inadmissible chunks do
    not starve the result, then truncates to ``limit`` admissible items.
    Retrieval goes through a LangChain ``BaseRetriever`` (``ainvoke`` only); a
    plain ``AsyncRetriever`` is wrapped in ``ChunkRetriever``.
    """

    def __init__(
        self,
        retriever: BaseRetriever | AsyncRetriever | None = None,
        catalog: AsyncEvidenceCatalog | None = None,
        *,
        oversampling: int = 3,
    ) -> None:
        """Receive optional adapters explicitly; missing adapters mean unavailable."""
        if type(oversampling) is not int or oversampling < 1:
            raise ValueError("oversampling must be a positive integer")
        if retriever is not None and not isinstance(retriever, BaseRetriever):
            retriever = ChunkRetriever(base=retriever)
        self._retriever: BaseRetriever | None = retriever
        self._catalog = catalog
        self._oversampling = oversampling

    @property
    def retriever(self) -> BaseRetriever | None:
        """Return the LangChain retriever used for candidates (None if absent)."""
        return self._retriever

    async def query(self, request: RetrievalQuery) -> RetrievalResult:
        """Revalidate unique chunks; discard all evidence on dependency failure."""
        if not isinstance(request, RetrievalQuery):
            raise TypeError("request must be RetrievalQuery")
        if self._retriever is None or self._catalog is None:
            return RetrievalResult("unavailable")
        evidence: list[Evidence] = []
        phase = "retriever"
        # Adapter ports may raise anything; fail closed without partial evidence.
        try:
            documents = await self._retriever.ainvoke(
                request.question, k=request.limit * self._oversampling
            )
            candidates = [document_candidate(document) for document in documents]
            unique: list[ChunkCandidate] = []
            seen: set[tuple[str, str, str]] = set()
            for candidate in candidates:
                key = (candidate.document_id, candidate.version_id, candidate.chunk_id)
                if key not in seen:
                    seen.add(key)
                    unique.append(candidate)
            phase = "catalog"
            entries: Mapping[str, RegistryEntry] | None = None
            if unique and isinstance(self._catalog, AsyncBatchEvidenceCatalog):
                entries = await self._catalog.get_current_many(
                    [candidate.chunk_id for candidate in unique]
                )
            for candidate in unique:
                if entries is not None:
                    entry = entries.get(candidate.chunk_id)
                else:
                    entry = await self._catalog.get_current(candidate.chunk_id)
                if is_admissible(candidate, entry):
                    evidence.append(to_evidence(candidate, entry))  # type: ignore[arg-type]
                    if len(evidence) == request.limit:
                        break
        except InvalidCandidateError:
            logger.warning("retrieval_failed phase=invalid_candidate")
            return RetrievalResult("unavailable")
        except Exception as error:
            logger.warning(
                "retrieval_failed phase=%s error=%s", phase, type(error).__name__
            )
            return RetrievalResult("unavailable")
        return RetrievalResult(
            "evidence_found" if evidence else "abstained", tuple(evidence)
        )

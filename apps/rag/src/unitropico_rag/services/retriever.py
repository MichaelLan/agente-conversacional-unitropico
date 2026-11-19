"""LangChain ``BaseRetriever`` boundary over the project's async retrievers.

The wrapper is deliberately thin: it delegates to an ``AsyncRetriever``
(``HybridRetriever`` in production, which covers hybrid, lexical-only and
semantic-only modes) and converts each candidate into a ``Document`` whose
metadata carries provenance plus the fusion trace (RRF score, per-channel rank
and native score). Catalog revalidation stays in ``RetrievalService``.

Only the async path exists: Python Workers have no threads, and LangChain's
sync entry points may dispatch to a thread pool.
"""

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from langchain_core.callbacks import (
    AsyncCallbackManagerForRetrieverRun,
    CallbackManagerForRetrieverRun,
)
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict, Field

from unitropico_rag.domain.evidence import ChunkCandidate
from unitropico_rag.services.fusion import FusedCandidate

ASYNC_ONLY_MESSAGE = (
    "async-only LangChain component: Python Workers have no threads; "
    "use ainvoke/abatch/astream"
)

# Provenance keys copied verbatim between ChunkCandidate and Document.metadata.
PROVENANCE_KEYS = (
    "document_id",
    "version_id",
    "chunk_id",
    "source_sha256",
    "page_start",
    "page_end",
    "section",
)


class InvalidCandidateError(ValueError):
    """A retriever produced something that is not a chunk candidate."""


class AsyncRetriever(Protocol):
    """Retrieve ordered candidates without coupling orchestration to an SDK."""

    async def retrieve(self, question: str, limit: int) -> Sequence[ChunkCandidate]:
        """Return candidates from the configured retrieval adapter."""
        ...


@runtime_checkable
class FusedRetriever(Protocol):
    """Optional extension: candidates with RRF score and per-channel trace."""

    @property
    def channels(self) -> tuple[str, ...]:
        """Configured channel names, in fusion order."""
        ...

    async def retrieve_fused(
        self, question: str, limit: int
    ) -> Sequence[FusedCandidate]:
        """Return fused candidates, best first."""
        ...


def candidate_document(
    candidate: object,
    *,
    rrf_score: float | None = None,
    channels: dict[str, tuple[int, float] | None] | None = None,
) -> Document:
    """Convert a candidate into a ``Document``; values are copied, not validated.

    Admissibility (types, hashes, page ranges) is decided later against the
    catalog, so even malformed field values survive the round trip unchanged.
    ``channels`` maps each configured channel to ``(rank, score)`` or None.
    """
    if not isinstance(candidate, ChunkCandidate):
        raise InvalidCandidateError("candidate")
    metadata: dict[str, object] = {
        key: getattr(candidate, key) for key in PROVENANCE_KEYS
    }
    metadata["rrf_score"] = rrf_score
    for name, hit in (channels or {}).items():
        metadata[f"rank_{name}"] = hit[0] if hit else None
        metadata[f"score_{name}"] = hit[1] if hit else None
    try:
        return Document(
            id=candidate.chunk_id if isinstance(candidate.chunk_id, str) else None,
            page_content=candidate.text,
            metadata=metadata,
        )
    except ValueError:  # pydantic rejects a non-string text
        raise InvalidCandidateError("text") from None


def document_candidate(document: object) -> ChunkCandidate:
    """Rebuild the candidate from a ``Document`` produced by ``ChunkRetriever``."""
    if not isinstance(document, Document) or any(
        key not in document.metadata for key in PROVENANCE_KEYS
    ):
        raise InvalidCandidateError("document")
    return ChunkCandidate(
        **{key: document.metadata[key] for key in PROVENANCE_KEYS},
        text=document.page_content,
    )


class ChunkRetriever(BaseRetriever):
    """Expose an ``AsyncRetriever`` as a LangChain retriever (async only).

    ``ainvoke(question, k=n)`` returns up to ``n`` documents (default ``k``).
    With a ``FusedRetriever`` base, metadata includes ``rrf_score`` and
    ``rank_<channel>``/``score_<channel>`` for every configured channel (None
    when the channel did not return that chunk).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    base: Any = Field(exclude=True)
    k: int = Field(default=5, ge=1)

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun,
        k: int | None = None,
    ) -> list[Document]:
        """Refuse the sync path (it may need threads)."""
        raise NotImplementedError(ASYNC_ONLY_MESSAGE)

    async def _aget_relevant_documents(
        self,
        query: str,
        *,
        run_manager: AsyncCallbackManagerForRetrieverRun,
        k: int | None = None,
    ) -> list[Document]:
        """Delegate to the base retriever and convert its candidates."""
        limit = self.k if k is None else k
        if isinstance(self.base, FusedRetriever):
            names = self.base.channels
            fused = await self.base.retrieve_fused(query, limit)
            documents = []
            for item in fused:
                if not isinstance(item, FusedCandidate):
                    raise InvalidCandidateError("fused")
                documents.append(
                    candidate_document(
                        item.candidate,
                        rrf_score=item.rrf_score,
                        channels={
                            name: (
                                (item.channels[name].rank, item.channels[name].score)
                                if name in item.channels
                                else None
                            )
                            for name in names
                        },
                    )
                )
            return documents
        candidates = await self.base.retrieve(query, limit)
        return [candidate_document(candidate) for candidate in candidates]

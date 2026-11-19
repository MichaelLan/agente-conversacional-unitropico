"""Semantic channel: question embedding plus a Vectorize nearest-neighbour query.

The real return shape of ``VECTORIZE.query`` from Python Workers is NOT verified
(Vectorize has no local emulation); parsing accepts dicts or attribute objects
and rejects anything else. Vectorize ids are D1 ``chunks.id``; its metadata is
never trusted, D1 revalidates every hit.
"""

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Protocol

from unitropico_rag.domain.evidence import RankedChunk

# Vectorize caps topK at 50 with values/metadata "all" (100 otherwise); stay at 50.
MAX_TOP_K = 50


class VectorIndexError(ValueError):
    """Signal a malformed Vectorize response."""


class VectorIndex(Protocol):
    """Nearest-neighbour lookup by vector."""

    async def query(
        self, vector: Sequence[float], top_k: int
    ) -> list[tuple[str, float]]:
        """Return ``(chunk_id, score)`` pairs, best first."""
        ...


class QueryEmbedder(Protocol):
    """Async query embedding, structurally the LangChain ``Embeddings`` surface."""

    async def aembed_query(self, text: str) -> list[float]:
        """Return the vector for one query text."""
        ...


def _field(item: object, name: str) -> object:
    """Read a key from a dict or an attribute from a runtime object."""
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


class VectorizeIndex:
    """Adapt the ``VECTORIZE`` binding to ``VectorIndex``.

    No metadata filter: vectors are upserted while the version is still
    ``ready``/``review_required`` and publication only changes D1, so a
    ``status`` filter would hide every published chunk.
    """

    def __init__(self, binding: object, *, max_top_k: int = MAX_TOP_K):
        """Receive the runtime binding and the topK cap."""
        if type(max_top_k) is not int or not 1 <= max_top_k <= MAX_TOP_K:
            raise ValueError("max_top_k must be within 1..50")
        self._binding, self._max_top_k = binding, max_top_k

    async def query(
        self, vector: Sequence[float], top_k: int
    ) -> list[tuple[str, float]]:
        """Query the index and return validated ``(id, score)`` pairs."""
        if type(top_k) is not int or top_k < 1:
            raise ValueError("top_k must be a positive integer")
        result = await self._binding.query(  # type: ignore[attr-defined]
            list(vector),
            topK=min(top_k, self._max_top_k),
            returnValues=False,
            returnMetadata="none",
        )
        matches = _field(result, "matches")
        if isinstance(matches, tuple):
            matches = list(matches)
        if not isinstance(matches, list):
            raise VectorIndexError("matches")
        pairs = []
        for match in matches:
            chunk_id, score = _field(match, "id"), _field(match, "score")
            if (
                not isinstance(chunk_id, str)
                or not chunk_id.strip()
                or len(chunk_id.encode("utf-8")) > 64
                or type(score) not in (int, float)
                or not isfinite(score)
            ):
                raise VectorIndexError("match")
            pairs.append((chunk_id, float(score)))
        pairs.sort(key=lambda pair: -pair[1])  # stable: keeps index order on ties
        return pairs


class VectorRetriever:
    """Rank chunks by embedding similarity to the question."""

    def __init__(self, embeddings: QueryEmbedder, index: VectorIndex):
        """Receive LangChain-style embeddings (``GatewayEmbeddings``) and the index."""
        self._embeddings, self._index = embeddings, index

    async def search(self, question: str, limit: int) -> list[RankedChunk]:
        """Embed the question once and return up to ``limit`` hits."""
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        vector = await self._embeddings.aembed_query(question)
        pairs = await self._index.query(vector, limit)
        return [RankedChunk(chunk_id, score) for chunk_id, score in pairs[:limit]]

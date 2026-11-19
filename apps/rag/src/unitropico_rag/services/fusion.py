import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from unitropico_rag.core.logger import logger
from unitropico_rag.domain.evidence import ChunkCandidate, RankedChunk

DEFAULT_RRF_K = 60


class RankedRetriever(Protocol):
    """One retrieval channel returning ranked chunk ids (best first)."""

    async def search(self, question: str, limit: int) -> Sequence[RankedChunk]:
        """Return up to ``limit`` hits."""
        ...


class ChunkSource(Protocol):
    """Hydrate chunk ids into candidates with text and provenance."""

    async def load(self, chunk_ids: Sequence[str]) -> Mapping[str, ChunkCandidate]:
        """Return candidates by id; unknown ids are absent."""
        ...


class RetrievalChannelsError(RuntimeError):
    """Every configured retrieval channel failed."""


@dataclass(frozen=True)
class ChannelHit:
    """Position (1-based) and native score of a chunk inside one channel."""

    rank: int
    score: float


@dataclass(frozen=True)
class FusedCandidate:
    """A hydrated candidate with its RRF score and per-channel trace.

    ``channels`` has an entry only for channels that returned the chunk; scores
    are channel-native (bm25 lower is better, cosine higher) and not comparable.
    """

    candidate: ChunkCandidate
    rrf_score: float
    channels: Mapping[str, ChannelHit] = field(default_factory=dict)


def reciprocal_rank_fusion_scored(
    rankings: Sequence[Sequence[str]], *, k: int = DEFAULT_RRF_K
) -> list[tuple[str, float]]:
    """Fuse rankings by sum of ``1 / (k + rank)`` with 1-based ranks.

    Repeated ids inside one ranking count once (first position). Ties break by
    best single rank, then by first ranking that contains the id, then by id,
    so the output is deterministic. Returns ``(id, rrf_score)`` best first.
    """
    if type(k) is not int or k < 0:
        raise ValueError("k must be a non-negative integer")
    scores: dict[str, float] = {}
    best: dict[str, tuple[int, int]] = {}
    for channel, ranking in enumerate(rankings):
        seen: set[str] = set()
        for rank, chunk_id in enumerate(ranking, start=1):
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
            best[chunk_id] = min(best.get(chunk_id, (rank, channel)), (rank, channel))
    ordered = sorted(scores, key=lambda cid: (-scores[cid], *best[cid], cid))
    return [(chunk_id, scores[chunk_id]) for chunk_id in ordered]


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]], *, k: int = DEFAULT_RRF_K
) -> list[str]:
    """Return only the fused ids of ``reciprocal_rank_fusion_scored``."""
    return [chunk_id for chunk_id, _ in reciprocal_rank_fusion_scored(rankings, k=k)]


def _valid_hits(hits: object) -> bool:
    """Check a channel result is a sequence of RankedChunk."""
    return isinstance(hits, Sequence) and all(
        isinstance(hit, RankedChunk) and isinstance(hit.chunk_id, str) for hit in hits
    )


class HybridRetriever:
    """Run channels concurrently, fuse with RRF and hydrate the top ids.

    Implements ``AsyncRetriever``. With a single channel it is that channel's
    ranking. A failing channel is logged and skipped; if all fail, raises
    ``RetrievalChannelsError`` (the service then reports unavailable).
    Hydrated candidates are not admissibility-checked here; the retrieval
    service revalidates them against the catalog.
    """

    def __init__(
        self,
        source: ChunkSource,
        *,
        lexical: RankedRetriever | None = None,
        semantic: RankedRetriever | None = None,
        rrf_k: int = DEFAULT_RRF_K,
        channel_limit: int = 50,
    ):
        """Receive at least one channel, the chunk source and fusion parameters."""
        self._channels = [
            (name, channel)
            for name, channel in (("lexical", lexical), ("semantic", semantic))
            if channel is not None
        ]
        if not self._channels:
            raise ValueError("at least one retrieval channel is required")
        if type(rrf_k) is not int or rrf_k < 0:
            raise ValueError("rrf_k must be a non-negative integer")
        if type(channel_limit) is not int or channel_limit < 1:
            raise ValueError("channel_limit must be a positive integer")
        self._source, self._k, self._channel_limit = source, rrf_k, channel_limit

    @property
    def channels(self) -> tuple[str, ...]:
        """Return the configured channel names, in fusion order."""
        return tuple(name for name, _ in self._channels)

    async def retrieve(self, question: str, limit: int) -> list[ChunkCandidate]:
        """Return up to ``limit`` fused candidates, best first."""
        return [item.candidate for item in await self.retrieve_fused(question, limit)]

    async def retrieve_fused(self, question: str, limit: int) -> list[FusedCandidate]:
        """Return up to ``limit`` fused candidates with RRF score and channel ranks."""
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        per_channel = max(limit, self._channel_limit)
        results = await asyncio.gather(
            *(channel.search(question, per_channel) for _, channel in self._channels),
            return_exceptions=True,
        )
        rankings: list[list[str]] = []
        traces: dict[str, dict[str, ChannelHit]] = {}
        for (name, _), hits in zip(self._channels, results, strict=True):
            if isinstance(hits, BaseException) and not isinstance(hits, Exception):
                raise hits  # cancellation and interpreter exits propagate
            if isinstance(hits, Exception) or not _valid_hits(hits):
                error = type(hits).__name__ if isinstance(hits, Exception) else "shape"
                logger.warning(
                    "retrieval_channel_failed channel=%s error=%s", name, error
                )
                continue
            trace: dict[str, ChannelHit] = {}
            for rank, hit in enumerate(hits, start=1):  # type: ignore[arg-type]
                trace.setdefault(hit.chunk_id, ChannelHit(rank, hit.score))
            traces[name] = trace
            rankings.append([hit.chunk_id for hit in hits])  # type: ignore[union-attr]
        if not rankings:
            raise RetrievalChannelsError("all retrieval channels failed")
        fused = reciprocal_rank_fusion_scored(rankings, k=self._k)[:limit]
        if not fused:
            return []
        loaded = await self._source.load([chunk_id for chunk_id, _ in fused])
        return [
            FusedCandidate(
                loaded[chunk_id],
                score,
                {
                    name: trace[chunk_id]
                    for name, trace in traces.items()
                    if chunk_id in trace
                },
            )
            for chunk_id, score in fused
            if chunk_id in loaded
        ]

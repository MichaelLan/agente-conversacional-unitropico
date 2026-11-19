"""Reciprocal Rank Fusion and the hybrid retriever."""

import asyncio
from dataclasses import replace

import pytest
from test_retrieval import CANDIDATE

from unitropico_rag.domain.evidence import RankedChunk
from unitropico_rag.services.fusion import (
    HybridRetriever,
    RetrievalChannelsError,
    reciprocal_rank_fusion,
)


def test_rrf_overlap_beats_single_channel_top():
    """An id ranked in both channels outranks ids ranked first in only one."""
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["d", "b", "a"]], k=60)
    # b: 2/62; a: 1/61 + 1/63; d: 1/61; c: 1/63
    assert fused == ["a", "b", "d", "c"]


def test_rrf_scores_with_known_values():
    """Scores are sums of 1/(k+rank) with 1-based ranks."""
    assert reciprocal_rank_fusion([["x", "y"], ["y"]], k=0) == ["y", "x"]  # 1.5 vs 1
    assert reciprocal_rank_fusion([["x", "y"], ["y"]], k=1000) == ["y", "x"]


def test_rrf_ties_are_deterministic():
    """Equal scores break by best rank, then channel order, then id."""
    # a and d both 1/61: same best rank 1, lexical channel (0) first.
    assert reciprocal_rank_fusion([["a"], ["d"]]) == ["a", "d"]
    assert reciprocal_rank_fusion([["d"], ["a"]]) == ["d", "a"]
    # Symmetric overlap: x=(1,2) y=(2,1) tie on score and best rank 1; channel.
    assert reciprocal_rank_fusion([["x", "y"], ["y", "x"]]) == ["x", "y"]


def test_rrf_single_channel_and_duplicates():
    """One channel keeps its order; duplicates in a channel count once."""
    assert reciprocal_rank_fusion([["c", "a", "c", "b"]]) == ["c", "a", "b"]
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([["a"]], k=-1)


class Channel:
    """Fake ranked retriever."""

    def __init__(self, ids=(), fail=False, result=None):
        """Configure ids or a failure."""
        self.ids, self.fail, self.result, self.calls = ids, fail, result, []

    async def search(self, question, limit):
        """Record the call and return hits."""
        self.calls.append((question, limit))
        await asyncio.sleep(0)
        if self.fail:
            raise RuntimeError("SECRET channel")
        if self.result is not None:
            return self.result
        return [RankedChunk(i, float(-n)) for n, i in enumerate(self.ids)]


class Source:
    """Fake chunk source hydrating known ids."""

    def __init__(self, known=None):
        """Configure known ids (default: everything)."""
        self.known, self.calls = known, []

    async def load(self, chunk_ids):
        """Return candidates for known ids."""
        self.calls.append(list(chunk_ids))
        return {
            i: replace(CANDIDATE, chunk_id=i)
            for i in chunk_ids
            if self.known is None or i in self.known
        }


def retrieve(retriever, limit=5, question="beca"):
    """Run retrieve synchronously and return chunk ids."""
    return [c.chunk_id for c in asyncio.run(retriever.retrieve(question, limit))]


def test_hybrid_fuses_and_hydrates_top_limit():
    """Both channels run with the channel limit; top ids are hydrated in order."""
    lexical, semantic, source = Channel(["a", "b", "c"]), Channel(["d", "b"]), Source()
    retriever = HybridRetriever(source, lexical=lexical, semantic=semantic)
    assert retriever.channels == ("lexical", "semantic")
    assert retrieve(retriever, limit=2) == ["b", "a"]
    assert lexical.calls == semantic.calls == [("beca", 50)]
    assert source.calls == [["b", "a"]]


def test_hybrid_runs_channels_concurrently():
    """Channels are awaited together, not one after the other."""
    order = []

    class Slow(Channel):
        async def search(self, question, limit):
            order.append(("start", self.ids[0]))
            await asyncio.sleep(0.01)
            order.append(("end", self.ids[0]))
            return [RankedChunk(self.ids[0], 0.0)]

    retriever = HybridRetriever(Source(), lexical=Slow(["a"]), semantic=Slow(["b"]))
    retrieve(retriever)
    assert [step for step, _ in order[:2]] == ["start", "start"]


def test_hybrid_skips_unknown_ids_and_respects_large_limit():
    """Ids missing in D1 are dropped; limit above channel_limit widens channels."""
    lexical = Channel(["a", "ghost", "b"])
    retriever = HybridRetriever(Source({"a", "b"}), lexical=lexical, channel_limit=10)
    assert retrieve(retriever, limit=15) == ["a", "b"]
    assert lexical.calls == [("beca", 15)]


@pytest.mark.parametrize("failing", ["lexical", "semantic"])
def test_hybrid_degrades_when_one_channel_fails(failing, caplog):
    """A failed channel is logged by type and the other ranking is used."""
    channels = {
        "lexical": Channel(["a"], fail=failing == "lexical"),
        "semantic": Channel(["b"], fail=failing == "semantic"),
    }
    retriever = HybridRetriever(Source(), **channels)
    expected = ["b"] if failing == "lexical" else ["a"]
    assert retrieve(retriever, question="SECRET") == expected
    assert f"channel={failing} error=RuntimeError" in caplog.text
    assert "SECRET" not in caplog.text


def test_hybrid_invalid_channel_output_counts_as_failure(caplog):
    """A channel returning the wrong shape is treated as failed."""
    retriever = HybridRetriever(
        Source(), lexical=Channel(result=[("a", 1.0)]), semantic=Channel(["b"])
    )
    assert retrieve(retriever) == ["b"]
    assert "channel=lexical error=shape" in caplog.text


def test_hybrid_all_channels_fail_raises():
    """Both channels failing raises so the service reports unavailable."""
    retriever = HybridRetriever(
        Source(), lexical=Channel(fail=True), semantic=Channel(fail=True)
    )
    with pytest.raises(RetrievalChannelsError):
        retrieve(retriever)


def test_hybrid_empty_rankings_skip_hydration():
    """No hits means no catalog read."""
    source = Source()
    assert retrieve(HybridRetriever(source, lexical=Channel([]))) == []
    assert source.calls == []


def test_hybrid_propagates_cancellation():
    """Cancellation inside a channel is not swallowed as a channel failure."""

    class Cancelled(Channel):
        async def search(self, question, limit):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        retrieve(HybridRetriever(Source(), lexical=Cancelled(), semantic=Channel()))


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"lexical": Channel(), "rrf_k": -1},
        {"lexical": Channel(), "channel_limit": 0},
    ],
)
def test_invalid_configuration(kwargs):
    """At least one channel and valid parameters are required."""
    with pytest.raises(ValueError):
        HybridRetriever(Source(), **kwargs)

"""Vectorize adapter and semantic retriever with fakes.

The real return shape of ``env.VECTORIZE.query`` from Python Workers is not
verified locally (Vectorize has no local emulation); these fakes cover the
documented JS shape converted to dicts and an attribute-style object.
"""

import asyncio
import math
from types import SimpleNamespace

import pytest

from unitropico_rag.domain.evidence import RankedChunk
from unitropico_rag.infra.vectorize import (
    VectorIndexError,
    VectorizeIndex,
    VectorRetriever,
)


class Binding:
    """Fake VECTORIZE binding returning a canned result."""

    def __init__(self, result):
        """Store result and record calls."""
        self.result, self.calls = result, []

    async def query(self, vector, **options):
        """Record the call and return the canned result."""
        self.calls.append((vector, options))
        return self.result


def run(coroutine):
    """Run a coroutine synchronously."""
    return asyncio.run(coroutine)


def test_query_options_and_dict_matches():
    """TopK is capped at 50, no values/metadata, no status filter."""
    binding = Binding(
        {"count": 2, "matches": [{"id": "b", "score": 0.5}, {"id": "a", "score": 0.9}]}
    )
    pairs = run(VectorizeIndex(binding).query((0.1, 0.2), 80))
    assert pairs == [("a", 0.9), ("b", 0.5)]
    vector, options = binding.calls[0]
    assert vector == [0.1, 0.2] and isinstance(vector, list)
    assert options == {"topK": 50, "returnValues": False, "returnMetadata": "none"}


def test_attribute_matches_and_ties_keep_order():
    """Attribute-style results parse; equal scores keep index order."""
    matches = [SimpleNamespace(id="x", score=1), SimpleNamespace(id="y", score=1)]
    result = SimpleNamespace(matches=matches)
    assert run(VectorizeIndex(Binding(result)).query([0.0], 5)) == [
        ("x", 1.0),
        ("y", 1.0),
    ]


@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        {"matches": None},
        {"matches": [{"id": "", "score": 1.0}]},
        {"matches": [{"id": "x" * 65, "score": 1.0}]},
        {"matches": [{"id": 3, "score": 1.0}]},
        {"matches": [{"id": "a", "score": None}]},
        {"matches": [{"id": "a", "score": math.nan}]},
        {"matches": [{"id": "a", "score": True}]},
    ],
)
def test_malformed_results(result):
    """Unknown shapes raise instead of producing hits."""
    with pytest.raises(VectorIndexError):
        run(VectorizeIndex(Binding(result)).query([0.0], 5))


def test_invalid_top_k():
    """top_k and the cap must be valid."""
    with pytest.raises(ValueError):
        VectorizeIndex(Binding({}), max_top_k=51)
    with pytest.raises(ValueError):
        run(VectorizeIndex(Binding({"matches": []})).query([0.0], 0))


class Embedder:
    """Fake LangChain-style embeddings returning a fixed vector."""

    def __init__(self):
        """Record calls."""
        self.calls = []

    async def aembed_query(self, text):
        """Return one vector for the query."""
        self.calls.append([text])
        return [0.5, 0.5]


class Index:
    """Fake VectorIndex."""

    def __init__(self, pairs):
        """Store pairs and record calls."""
        self.pairs, self.calls = pairs, []

    async def query(self, vector, top_k):
        """Record and return pairs."""
        self.calls.append((vector, top_k))
        return self.pairs


def test_vector_retriever_embeds_question_once():
    """The question is embedded once and hits keep index order, truncated."""
    embedder, index = Embedder(), Index([("a", 0.9), ("b", 0.8), ("c", 0.7)])
    hits = run(VectorRetriever(embedder, index).search("¿Qué becas hay?", 2))
    assert hits == [RankedChunk("a", 0.9), RankedChunk("b", 0.8)]
    assert embedder.calls == [["¿Qué becas hay?"]]
    assert index.calls == [([0.5, 0.5], 2)]

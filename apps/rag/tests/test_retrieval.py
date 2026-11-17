import asyncio
from dataclasses import replace
from hashlib import sha256

import pytest

from unitropico_rag.domain.evidence import (
    ChunkCandidate,
    RegistryEntry,
    RetrievalQuery,
    RetrievalResult,
)
from unitropico_rag.services.retrieval import RetrievalService

CANDIDATE = ChunkCandidate("doc", "v1", "c1", "a" * 64, 2, None, "Texto á\n")
ENTRY = RegistryEntry(
    "doc",
    "v1",
    "c1",
    "a" * 64,
    2,
    None,
    sha256(CANDIDATE.text.encode("utf-8")).hexdigest(),
    "published",
    False,
    False,
)


class Retriever:
    """Return test candidates and record the public retrieval inputs."""

    def __init__(self, candidates=(), fail=False):
        """Initialize an isolated test adapter."""
        self.candidates, self.fail, self.calls = candidates, fail, []

    async def retrieve(self, question, limit):
        """Record input or simulate an infrastructure failure."""
        self.calls.append((question, limit))
        if self.fail:
            raise RuntimeError("SECRET retrieval content")
        return self.candidates


class Catalog:
    """Expose mutable current metadata only inside tests."""

    def __init__(self, entry=ENTRY, fail=False):
        """Initialize current publication state and lookup recording."""
        self.entry, self.fail, self.calls = entry, fail, []

    async def get_current(self, chunk_id):
        """Look up current state without caching."""
        self.calls.append(chunk_id)
        if self.fail:
            raise RuntimeError("SECRET catalog content")
        return self.entry


def query(service, question=" pregunta ", limit=5):
    """Invoke the asynchronous public interface from synchronous pytest."""
    return asyncio.run(service.query(RetrievalQuery(question=question, limit=limit)))


def test_unavailable_and_empty():
    """Distinguish absent dependencies from operational empty retrieval."""
    for retriever, catalog in [(None, None), (Retriever(), None), (None, Catalog())]:
        assert query(RetrievalService(retriever, catalog)) == RetrievalResult(
            "unavailable"
        )
    assert query(RetrievalService(Retriever(), Catalog())) == RetrievalResult(
        "abstained"
    )


def test_exact_evidence_and_limit():
    """Return exact cited text once, normalize input and cap adapter overproduction."""
    second = replace(CANDIDATE, chunk_id="c2")
    retriever, catalog = Retriever([CANDIDATE, CANDIDATE, second]), Catalog()
    assert query(RetrievalService(retriever, catalog), limit=1) == RetrievalResult(
        "evidence_found", (CANDIDATE,)
    )
    assert retriever.calls == [("pregunta", 1)]
    assert catalog.calls == ["c1"]
    assert CANDIDATE.section is None
    catalog.calls.clear()
    assert query(RetrievalService(retriever, catalog), limit=20) == RetrievalResult(
        "evidence_found", (CANDIDATE,)
    )
    assert catalog.calls == ["c1", "c2"]


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "draft"},
        {"status": "unpublished"},
        {"status": "superseded"},
        {"requires_review": True},
        {"formally_superseded": True},
        {"document_id": "other"},
        {"version_id": "v2"},
        {"chunk_id": "other"},
        {"source_sha256": "b" * 64},
        {"page": 3},
        {"section": "invented"},
        {"text_sha256": "b" * 64},
        {"requires_review": 0},
        {"formally_superseded": 0},
    ],
)
def test_reject_ineligible_registry(changes):
    """Exclude unpublished, unreviewed, superseded or inconsistent records."""
    catalog = Catalog(replace(ENTRY, **changes))
    assert query(RetrievalService(Retriever([CANDIDATE]), catalog)) == RetrievalResult(
        "abstained"
    )
    assert catalog.calls == ["c1"]


@pytest.mark.parametrize(
    "changes",
    [
        {"text": "Texto á"},
        {"page": 0},
        {"page": True},
        {"source_sha256": "bad"},
        {"document_id": ""},
        {"text": ""},
    ],
)
def test_reject_invalid_candidate(changes):
    """Reject forged text and invalid physical provenance despite matching metadata."""
    candidate = replace(CANDIDATE, **changes)
    registry_changes = {key: value for key, value in changes.items() if key != "text"}
    assert query(
        RetrievalService(
            Retriever([candidate]), Catalog(replace(ENTRY, **registry_changes))
        )
    ) == RetrievalResult("abstained")


def test_missing_and_unpublished_between_queries():
    """Revalidate every call even while the same chunk remains indexed."""
    catalog = Catalog()
    service = RetrievalService(Retriever([CANDIDATE]), catalog)
    assert query(service) == RetrievalResult("evidence_found", (CANDIDATE,))
    catalog.entry = replace(ENTRY, status="unpublished")
    assert query(service) == RetrievalResult("abstained")
    catalog.entry = None
    assert query(service) == RetrievalResult("abstained")
    assert catalog.calls == ["c1", "c1", "c1"]


@pytest.mark.parametrize("dependency", ["retriever", "catalog"])
def test_dependency_failure_is_private(dependency, caplog, capsys):
    """Return no partial evidence and expose neither content nor exception details."""
    service = RetrievalService(
        Retriever([CANDIDATE], fail=dependency == "retriever"),
        Catalog(fail=dependency == "catalog"),
    )
    assert query(service) == RetrievalResult("unavailable")
    assert caplog.text == ""
    assert capsys.readouterr() == ("", "")


def test_failure_discards_partial_evidence():
    """Do not return earlier evidence when a later catalog lookup fails."""

    class FailingCatalog(Catalog):
        """Fail after a successful first lookup."""

        async def get_current(self, chunk_id):
            """Return one record then simulate a catalog outage."""
            if chunk_id == "c2":
                raise RuntimeError("SECRET")
            return await super().get_current(chunk_id)

    retriever = Retriever([CANDIDATE, replace(CANDIDATE, chunk_id="c2")])
    assert query(RetrievalService(retriever, FailingCatalog())) == RetrievalResult(
        "unavailable"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"question": ""},
        {"question": " \t"},
        {"question": "x" * 501},
        {"question": 1},
        {"question": None},
        {"question": "x", "limit": 0},
        {"question": "x", "limit": 21},
        {"question": "x", "limit": True},
        {"question": "x", "limit": "1"},
        {"question": "x", "role": "system"},
    ],
)
def test_invalid_query(kwargs):
    """Reject invalid input and privileged extras before dependency calls."""
    retriever, catalog = Retriever(), Catalog()
    with pytest.raises(ValueError):
        asyncio.run(
            RetrievalService(retriever, catalog).query(RetrievalQuery(**kwargs))
        )
    assert retriever.calls == catalog.calls == []


@pytest.mark.parametrize("length,limit", [(1, 1), (500, 20)])
def test_query_boundaries(length, limit):
    """Accept the documented normalized length and engineering cap boundaries."""
    retriever = Retriever()
    assert query(RetrievalService(retriever, Catalog()), "x" * length, limit) == (
        RetrievalResult("abstained")
    )
    assert retriever.calls == [("x" * length, limit)]
    assert RetrievalQuery(question=" " + "x" * length + " ").question == "x" * length

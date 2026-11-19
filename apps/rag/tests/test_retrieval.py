import asyncio
from dataclasses import replace
from datetime import date
from hashlib import sha256

import pytest

from unitropico_rag.domain.evidence import (
    ChunkCandidate,
    RegistryEntry,
    RetrievalQuery,
    RetrievalResult,
    to_evidence,
)
from unitropico_rag.services.retrieval import RetrievalService

CANDIDATE = ChunkCandidate(
    document_id="doc",
    version_id="v1",
    chunk_id="c1",
    source_sha256="a" * 64,
    page_start=2,
    page_end=3,
    section=None,
    text="Texto de prueba sobre apoyos á\n",
)
ENTRY = RegistryEntry(
    document_id="doc",
    version_id="v1",
    chunk_id="c1",
    source_sha256="a" * 64,
    page_start=2,
    page_end=3,
    section=None,
    text_sha256=sha256(CANDIDATE.text.encode("utf-8")).hexdigest(),
    status="published",
    requires_review=False,
    formally_superseded=False,
    document_title="Reglamento sintético de prueba",
    version_label="2026-1",
    validity_label="Vigente durante 2026-1",
    effective_from=date(2026, 2, 1),
    effective_until=date(2026, 6, 30),
)
EVIDENCE = to_evidence(CANDIDATE, ENTRY)


def chunk(chunk_id, text=CANDIDATE.text):
    """Build a matching candidate/entry pair for another chunk id."""
    candidate = replace(CANDIDATE, chunk_id=chunk_id, text=text)
    entry = replace(
        ENTRY, chunk_id=chunk_id, text_sha256=sha256(text.encode()).hexdigest()
    )
    return candidate, entry


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

    def __init__(self, entry=ENTRY, fail=False, entries=None):
        """Initialize current publication state and lookup recording."""
        self.entry, self.fail, self.calls = entry, fail, []
        self.entries = entries or {}

    async def get_current(self, chunk_id):
        """Look up current state without caching."""
        self.calls.append(chunk_id)
        if self.fail:
            raise RuntimeError("SECRET catalog content")
        return self.entries.get(chunk_id, self.entry)


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


def test_evidence_carries_catalog_metadata():
    """Expose title, version label and validity from the catalog, not the index."""
    result = query(RetrievalService(Retriever([CANDIDATE]), Catalog()))
    assert result == RetrievalResult("evidence_found", (EVIDENCE,))
    item = result.evidence[0]
    assert (item.document_title, item.version_label) == (
        "Reglamento sintético de prueba",
        "2026-1",
    )
    assert (item.page_start, item.page_end, item.text) == (2, 3, CANDIDATE.text)


def test_oversampling_fills_limit_after_filtering():
    """Ask for limit*3 candidates so ineligible chunks do not starve the result."""
    c2, e2 = chunk("c2", "Segundo texto de prueba")
    c3, e3 = chunk("c3", "Tercer texto de prueba")
    catalog = Catalog(
        entries={"c1": replace(ENTRY, status="draft"), "c2": e2, "c3": e3}
    )
    retriever = Retriever([CANDIDATE, CANDIDATE, c2, c3])
    result = query(RetrievalService(retriever, catalog), limit=1)
    assert [item.chunk_id for item in result.evidence] == ["c2"]
    assert retriever.calls == [("pregunta", 3)]
    assert catalog.calls == ["c1", "c2"]
    result = query(RetrievalService(retriever, catalog, oversampling=2), limit=2)
    assert [item.chunk_id for item in result.evidence] == ["c2", "c3"]
    assert retriever.calls[-1] == ("pregunta", 4)


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
        {"page_start": 1},
        {"page_end": 4},
        {"section": "invented"},
        {"text_sha256": "b" * 64},
        {"requires_review": 0},
        {"formally_superseded": 0},
        {"document_title": " "},
        {"version_label": ""},
        {"effective_from": "2026-02-01"},
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
        {"page_start": 0, "page_end": 0},
        {"page_start": 4, "page_end": 3},
        {"page_start": True},
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
    assert query(service) == RetrievalResult("evidence_found", (EVIDENCE,))
    catalog.entry = replace(ENTRY, status="unpublished")
    assert query(service) == RetrievalResult("abstained")
    catalog.entry = None
    assert query(service) == RetrievalResult("abstained")
    assert catalog.calls == ["c1", "c1", "c1"]


@pytest.mark.parametrize("dependency", ["retriever", "catalog"])
def test_dependency_failure_is_logged_without_content(dependency, caplog):
    """Return no evidence; log phase and exception type, never content."""
    service = RetrievalService(
        Retriever([CANDIDATE], fail=dependency == "retriever"),
        Catalog(fail=dependency == "catalog"),
    )
    assert query(service, question="SECRET pregunta") == RetrievalResult("unavailable")
    assert f"phase={dependency}" in caplog.text
    assert "error=RuntimeError" in caplog.text
    assert "SECRET" not in caplog.text


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


def test_structural_impostor_is_unavailable(caplog):
    """A non-ChunkCandidate object with matching fields fails closed."""

    class Impostor:
        """Duck-typed lookalike of a candidate."""

        def __init__(self):
            """Copy candidate attributes."""
            self.__dict__.update(vars(CANDIDATE))

    service = RetrievalService(Retriever([CANDIDATE, Impostor()]), Catalog())
    assert query(service) == RetrievalResult("unavailable")
    assert "phase=invalid_candidate" in caplog.text


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
    with pytest.raises(ValueError):
        RetrievalQuery(**kwargs)


def test_query_boundaries():
    """Accept the documented normalized length and engineering cap boundaries."""
    retriever = Retriever()
    service = RetrievalService(retriever, Catalog())
    assert query(service, "x", 1) == RetrievalResult("abstained")
    assert query(service, "x" * 500, 20) == RetrievalResult("abstained")
    assert retriever.calls == [("x", 3), ("x" * 500, 60)]
    assert RetrievalQuery(question=" " + "x" * 500 + " ").question == "x" * 500


class BatchCatalog(Catalog):
    """Catalog exposing the optional batch lookup."""

    def __init__(self, *args, **kwargs):
        """Also record batch calls."""
        super().__init__(*args, **kwargs)
        self.batches = []

    async def get_current_many(self, chunk_ids):
        """Return entries for registered ids in one call."""
        self.batches.append(list(chunk_ids))
        if self.fail:
            raise RuntimeError("SECRET catalog content")
        return {
            i: self.entries.get(i, self.entry)
            for i in chunk_ids
            if self.entries.get(i, self.entry) is not None
        }


def test_batch_catalog_revalidates_in_one_read():
    """Batch-capable catalogs get one lookup for all unique candidates."""
    c2, e2 = chunk("c2", "Segundo texto de prueba")
    catalog = BatchCatalog(entries={"c1": replace(ENTRY, status="draft"), "c2": e2})
    retriever = Retriever([CANDIDATE, CANDIDATE, c2])
    result = query(RetrievalService(retriever, catalog))
    assert [item.chunk_id for item in result.evidence] == ["c2"]
    assert catalog.batches == [["c1", "c2"]] and catalog.calls == []


def test_batch_catalog_missing_and_failure():
    """Absent ids are not admissible; a batch failure is unavailable."""
    catalog = BatchCatalog(entry=None)
    assert query(RetrievalService(Retriever([CANDIDATE]), catalog)) == (
        RetrievalResult("abstained")
    )
    failing = BatchCatalog(fail=True)
    assert query(RetrievalService(Retriever([CANDIDATE]), failing)) == (
        RetrievalResult("unavailable")
    )
    empty = BatchCatalog()
    assert query(RetrievalService(Retriever([]), empty)) == RetrievalResult("abstained")
    assert empty.batches == []

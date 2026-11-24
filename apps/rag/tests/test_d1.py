"""D1 adapters against the real migration and canonical SQL on sqlite3."""

import asyncio
from datetime import date

import pytest
from d1_support import (
    BECA_TEXT,
    TRANSPORTE_TEXT,
    SqliteD1,
    canonical_query,
    digest,
    seeded,
)

from unitropico_rag.domain.evidence import RankedChunk, is_admissible
from unitropico_rag.infra.d1 import (
    REGISTRY_BY_CHUNK_ID_SQL,
    REGISTRY_BY_CHUNK_IDS_SQL,
    SEARCH_CHUNKS_SQL,
    D1EvidenceCatalog,
    D1LexicalRetriever,
    D1ResultError,
    query_rows,
)


def run(coroutine):
    """Run a coroutine from synchronous pytest."""
    return asyncio.run(coroutine)


def normalized(sql: str) -> str:
    """Collapse whitespace for drift comparison."""
    return " ".join(sql.split())


@pytest.mark.parametrize(
    "name,copy",
    [
        ("search_chunks", SEARCH_CHUNKS_SQL),
        ("registry_by_chunk_ids", REGISTRY_BY_CHUNK_IDS_SQL),
        ("registry_by_chunk_id", REGISTRY_BY_CHUNK_ID_SQL),
    ],
)
def test_sql_matches_canonical_queries(name, copy):
    """The adapter's SQL is the canonical infra/d1/queries.sql block."""
    assert normalized(copy) == normalized(canonical_query(name))


def test_lexical_ranks_published_chunks_only():
    """FTS hits exclude drafts; accent-insensitive; ranked by bm25 ascending."""
    retriever = D1LexicalRetriever(seeded(), synonyms=None)
    hits = run(retriever.search("¿Hasta cuándo es la inscripcion a la beca?", 10))
    assert [hit.chunk_id for hit in hits] == ["chunk-beca"]
    assert isinstance(hits[0], RankedChunk) and hits[0].score < 0
    both = run(retriever.search("beca transporte estudiantes", 10))
    assert {hit.chunk_id for hit in both} == {"chunk-beca", "chunk-transporte"}
    assert [hit.score for hit in both] == sorted(hit.score for hit in both)
    assert len(run(retriever.search("beca transporte estudiantes", 1))) == 1


def test_lexical_unsearchable_question_skips_query():
    """build_fts5_query → None means no D1 call and no hits."""
    db = seeded()
    assert run(D1LexicalRetriever(db).search("¿?¡! ---", 5)) == []
    assert db.calls == []


def test_lexical_operator_injection_is_inert():
    """FTS operators in the question are plain terms, not syntax."""
    hits = run(D1LexicalRetriever(seeded()).search('beca" OR NEAR(x*) -- ;', 5))
    # "beca" also expands to "apoyo" (transport chunk), backfilled after.
    assert [hit.chunk_id for hit in hits] == ["chunk-beca", "chunk-transporte"]
    exact = D1LexicalRetriever(seeded(), synonyms=None)
    assert [h.chunk_id for h in run(exact.search('beca" OR NEAR(x*) -- ;', 5))] == [
        "chunk-beca"
    ]


def test_lexical_synonyms_backfill_after_exact_hits():
    """Synonym-only hits come after exact hits; both queries are bound."""
    db = seeded()
    hits = run(D1LexicalRetriever(db).search("¿Qué becas o beca hay?", 10))
    assert [hit.chunk_id for hit in hits] == ["chunk-beca", "chunk-transporte"]
    assert len(db.calls) == 2
    assert all(sql == SEARCH_CHUNKS_SQL for sql, _ in db.calls)
    assert '"apoyo"' not in db.calls[0][1][0] and '"apoyo"' in db.calls[1][1][0]
    assert [h.chunk_id for h in run(D1LexicalRetriever(db).search("beca", 1))] == [
        "chunk-beca"
    ]


def test_lexical_without_expansion_runs_one_query():
    """No synonym trigger: a single reference query."""
    db = seeded()
    run(D1LexicalRetriever(db).search("transporte", 10))
    assert len(db.calls) == 1


def test_lexical_hides_formally_superseded_version():
    """A published replacement hides the version it supersedes."""
    db = seeded()
    db.add_version("ver-2027", "doc-beca", label="2027-1")
    db.add_chunk("chunk-2027", "ver-2027", "Nueva beca de alimentación 2027.")
    db.conn.execute(
        "UPDATE document_versions SET supersedes_version_id = 'ver-2026'"
        " WHERE id = 'ver-2027'"
    )
    db.make_ready("ver-2027")
    db.publish("ver-2027")
    hits = run(D1LexicalRetriever(db).search("beca alimentación", 10))
    assert [hit.chunk_id for hit in hits] == ["chunk-2027"]
    entry = run(D1EvidenceCatalog(db).get_current("chunk-beca"))
    assert entry.formally_superseded is True


def test_catalog_entry_carries_catalog_metadata():
    """Registry rows map to RegistryEntry with typed flags and dates."""
    db = seeded()
    entry = run(D1EvidenceCatalog(db).get_current("chunk-beca"))
    assert entry.document_title == "Reglamento de Bienestar Universitario"
    assert (entry.version_label, entry.section) == ("2026-1", "Capítulo II")
    assert (entry.page_start, entry.page_end) == (3, 4)
    assert (entry.effective_from, entry.effective_until) == (
        date(2026, 2, 1),
        date(2026, 6, 30),
    )
    assert entry.status == "published"
    assert entry.requires_review is False and entry.formally_superseded is False
    assert entry.text_sha256 == digest(BECA_TEXT)
    assert entry.source_sha256 == db.version_source("ver-2026")
    assert entry.validity_label is None
    assert run(D1EvidenceCatalog(db).get_current("missing")) is None


def test_catalog_batch_uses_one_json_parameter():
    """Batch lookups bind a single JSON array; missing ids are absent."""
    db = seeded()
    catalog = D1EvidenceCatalog(db)
    entries = run(
        catalog.get_current_many(["chunk-beca", "chunk-draft", "nope", "chunk-beca"])
    )
    assert set(entries) == {"chunk-beca", "chunk-draft"}
    assert entries["chunk-draft"].status == "ready"
    assert db.calls == [
        (REGISTRY_BY_CHUNK_IDS_SQL, ('["chunk-beca", "chunk-draft", "nope"]',))
    ]


def test_catalog_batches_over_100_ids():
    """More than 100 ids split into json_each batches of at most 100."""
    db = seeded()
    ids = [f"id-{n}" for n in range(150)] + ["chunk-transporte"]
    entries = run(D1EvidenceCatalog(db).get_current_many(ids))
    assert set(entries) == {"chunk-transporte"}
    assert len(db.calls) == 2


def test_load_hydrates_candidates_admissible_against_catalog():
    """Hydrated candidates match their registry entries; drafts are not admissible."""
    db = seeded()
    catalog = D1EvidenceCatalog(db)
    candidates = run(catalog.load(["chunk-transporte", "chunk-draft"]))
    entries = run(catalog.get_current_many(["chunk-transporte", "chunk-draft"]))
    transporte = candidates["chunk-transporte"]
    assert transporte.text == TRANSPORTE_TEXT and transporte.section is None
    assert is_admissible(transporte, entries["chunk-transporte"])
    assert not is_admissible(candidates["chunk-draft"], entries["chunk-draft"])


def test_unpublish_is_visible_on_next_read():
    """No caching: an unpublished version is reported on the next lookup."""
    db = seeded()
    catalog = D1EvidenceCatalog(db)
    assert run(catalog.get_current("chunk-beca")).status == "published"
    db.set_status("ver-2026", "unpublished")
    assert run(catalog.get_current("chunk-beca")).status == "unpublished"
    assert run(D1LexicalRetriever(db).search("beca", 5)) == []


class _Result:
    """Attribute-style D1 result."""

    def __init__(self, results):
        """Store results."""
        self.results = results


class _Db:
    """Return a canned result for any statement."""

    def __init__(self, result):
        """Store the canned result."""
        self.result = result

    def prepare(self, sql):
        """Return self as the statement."""
        return self

    def bind(self, *params):
        """Ignore parameters."""
        return self

    async def all(self):
        """Return the canned result."""
        return self.result


def test_query_rows_accepts_attribute_results():
    """Runtime results exposing ``.results`` are accepted."""
    assert run(query_rows(_Db(_Result([{"a": 1}])), "SELECT 1")) == [{"a": 1}]


@pytest.mark.parametrize(
    "result",
    [None, {}, {"results": None}, {"results": [1]}, _Result("x")],
)
def test_query_rows_rejects_unexpected_shapes(result):
    """Unknown result shapes raise D1ResultError."""
    with pytest.raises(D1ResultError):
        run(query_rows(_Db(result), "SELECT 1"))


@pytest.mark.parametrize(
    "changes",
    [
        {"page_start": "3"},
        {"page_start": True},
        {"requires_review": 2},
        {"effective_from": "30/06/2026"},
        {"document_title": None},
    ],
)
def test_malformed_registry_row_raises(changes):
    """Rows violating the schema contract are rejected, not coerced."""
    db = seeded()
    row = (
        dict(db.conn.execute(REGISTRY_BY_CHUNK_ID_SQL, ("chunk-beca",)).fetchone())
        | changes
    )
    with pytest.raises(D1ResultError):
        run(D1EvidenceCatalog(_Db({"results": [row]})).get_current("chunk-beca"))


def test_malformed_score_raises():
    """A non-numeric bm25 score is rejected."""
    db = _Db({"results": [{"chunk_id": "c", "score": None}]})
    with pytest.raises(D1ResultError):
        run(D1LexicalRetriever(db).search("beca", 5))


def test_invalid_limits():
    """Limits and term caps must be positive integers."""
    with pytest.raises(ValueError):
        D1LexicalRetriever(SqliteD1(), max_terms=0)
    with pytest.raises(ValueError):
        run(D1LexicalRetriever(SqliteD1()).search("beca", 0))

import asyncio
import re
import sqlite3

import pytest
from fakes import QUERIES, FakeD1, read_repo_file

from unitropico_ingestion.domain.chunking import build_chunk
from unitropico_ingestion.infra import d1_repository
from unitropico_ingestion.infra.d1_repository import D1VersionRepository

PDF = b"%PDF-1.7 repo"


def _canonical(name: str) -> str:
    text = read_repo_file(QUERIES)
    block = text.split(f"-- name: {name}\n", 1)[1].split("-- name:", 1)[0]
    lines = [line for line in block.splitlines() if not line.startswith("--")]
    return " ".join(" ".join(lines).split())


def test_log_event_is_the_canonical_query():
    """Log event is the canonical query."""
    assert " ".join(d1_repository.LOG_EVENT.split()) == _canonical("log_event")


def test_no_statement_publishes():
    """No statement publishes."""
    sql = [
        v for k, v in vars(d1_repository).items() if k.isupper() and isinstance(v, str)
    ]
    assert not any(re.search(r"status\s*=\s*'published'", s) for s in sql)
    assert not any("published_at" in s for s in sql)


def _repo():
    db = FakeD1()
    db.seed(version_id="v1", pdf=PDF)
    return db, D1VersionRepository(db)


def test_state_machine_round_trip():
    """State machine round trip."""
    db, repo = _repo()

    async def flow():
        version = await repo.get_version("v1")
        assert version.status == "uploaded" and version.requires_review
        assert await repo.start_processing("v1")
        assert not await repo.start_processing("v1")  # already processing
        assert await repo.mark_extracted(
            "v1", page_count=3, extraction_key="ocr/x.json"
        )
        chunks = [
            build_chunk(
                version_id="v1",
                chunker_version="t",
                ordinal=i,
                text=f"texto {i}",
                section=None,
                page_start=1,
                page_end=3,
            )
            for i in range(250)
        ]
        await repo.replace_chunks("v1", chunker_version="t", chunks=chunks)
        await repo.replace_chunks("v1", chunker_version="t", chunks=chunks[:2])
        await repo.set_embedding_model("v1", "m")
        assert await repo.finalize("v1") == "review_required"
        assert await repo.finalize("v1") == "review_required"
        await repo.log_event("v1", step="review", status="started")

    asyncio.run(flow())
    row = db.version("v1")
    assert row["processing_attempts"] == 1 and row["chunker_version"] == "t"
    assert db.query("SELECT COUNT(*) n FROM chunks")[0]["n"] == 2
    assert db.query("SELECT COUNT(*) n FROM chunks_fts")[0]["n"] == 2


def test_mark_failed_clears_detail_and_accepts_processing_and_ready():
    """Mark failed moves processing/ready to failed and clears the old detail."""
    db, repo = _repo()
    db.conn.execute("UPDATE document_versions SET status='processing' WHERE id='v1'")
    assert asyncio.run(repo.mark_failed("v1", code="ocr_failed", detail="d"))
    assert db.version("v1")["status"] == "failed"
    db.conn.execute(
        "UPDATE document_versions SET status='processing', error_code=NULL "
        "WHERE id='v1'"
    )
    db.conn.execute("UPDATE document_versions SET status='extracted' WHERE id='v1'")
    db.conn.execute("UPDATE document_versions SET status='ready' WHERE id='v1'")
    assert asyncio.run(repo.mark_failed("v1", code="x", detail=""))
    assert db.version("v1")["error_detail"] is None


def test_start_processing_forces_requires_review():
    """START_PROCESSING sets requires_review=1 even if it was cleared before."""
    db, repo = _repo()
    db.conn.execute("UPDATE document_versions SET requires_review = 0 WHERE id='v1'")
    assert asyncio.run(repo.start_processing("v1"))
    assert db.version("v1")["requires_review"] == 1


def test_chunk_beyond_page_count_is_rejected_by_schema():
    """Chunk beyond page count is rejected by schema."""
    db, repo = _repo()

    async def flow():
        await repo.start_processing("v1")
        await repo.mark_extracted("v1", page_count=1, extraction_key="ocr/y.json")
        chunk = build_chunk(
            version_id="v1",
            chunker_version="t",
            ordinal=0,
            text="x",
            section=None,
            page_start=1,
            page_end=2,
        )
        await repo.replace_chunks("v1", chunker_version="t", chunks=[chunk])

    with pytest.raises(sqlite3.IntegrityError, match="chunk_page_out_of_range"):
        asyncio.run(flow())


def test_mark_failed_skips_published_and_unpublished():
    """Mark failed skips published and unpublished."""
    db, repo = _repo()
    for status in ("processing", "extracted", "ready"):
        db.conn.execute(
            "UPDATE document_versions SET status=? WHERE id='v1'", (status,)
        )
    db.conn.execute(
        "UPDATE document_versions SET page_count=1, extraction_r2_key='ocr/z', "
        "chunker_version='t', requires_review=0 WHERE id='v1'"
    )
    chunk = build_chunk(
        version_id="v1",
        chunker_version="t",
        ordinal=0,
        text="x",
        section=None,
        page_start=1,
        page_end=1,
    )
    asyncio.run(repo.replace_chunks("v1", chunker_version="t", chunks=[chunk]))
    db.conn.execute(
        "UPDATE document_versions SET status='published', "
        "published_at='2030-01-01T00:00:00Z' WHERE id='v1'"
    )
    assert not asyncio.run(repo.mark_failed("v1", code="x", detail="d"))
    db.conn.execute("UPDATE document_versions SET status='unpublished' WHERE id='v1'")
    assert not asyncio.run(repo.mark_failed("v1", code="x", detail="d"))
    assert db.version("v1")["error_code"] is None


class _AttrResult:
    """Attribute-style D1 result with float counters, as JS objects may arrive."""

    def __init__(self, changes):
        self.meta = type("Meta", (), {"changes": changes})()
        self.results = []


class _AttrStatement:
    """Statement returning attribute-style results."""

    def __init__(self, changes):
        self._changes = changes

    def bind(self, *args):
        """Ignore values."""
        return self

    async def run(self):
        """Return an attribute-style result."""
        return _AttrResult(self._changes)


class _AttrDb:
    """D1 double whose results are not dicts."""

    def __init__(self, changes):
        self._changes = changes

    def prepare(self, sql):
        """Return a statement."""
        return _AttrStatement(self._changes)


@pytest.mark.parametrize(
    ("changes", "expected"), [(1.0, True), (1, True), (0.0, False)]
)
def test_changes_accept_float_and_attribute_results(changes, expected):
    """JS numbers become floats; meta may be an attribute object."""
    repo = D1VersionRepository(_AttrDb(changes))
    assert asyncio.run(repo.start_processing("v1")) is expected

import asyncio
import json
from pathlib import Path

import pytest
from fakes import (
    FakeBlobStore,
    FakeD1,
    FakeEmbedder,
    FakeNonRetryableError,
    FakeOcr,
    FakeStep,
    FakeVectorIndex,
    SimulatedCrash,
)

from unitropico_ingestion.domain.ingestion import ErrorCode, IngestionError
from unitropico_ingestion.infra import d1_repository
from unitropico_ingestion.infra.d1_repository import D1VersionRepository
from unitropico_ingestion.services import ingestion
from unitropico_ingestion.services.chunker import StructuralChunker
from unitropico_ingestion.workflow import IngestionConfig, IngestionDeps, run_ingestion

FIXTURE = Path(__file__).parent / "fixtures/firecrawl_parse_response.json"
PDF = b"%PDF-1.7\n% documento sintetico de prueba\n" + b"x" * 64
VERSION_ID = "ver-0001"
DIMS = 8
MODEL = "openai/text-embedding-3-small"
OCR_VERSION = "firecrawl-parse-v2-ocr-v1"
OCR_MAX_PAGES = 1000
OCR_KEY_SUFFIX = f"{OCR_VERSION}-p{OCR_MAX_PAGES}.json"
SECRET = "fc-SUPERSECRET-123456"


class World:
    """One deployment: D1, R2, Vectorize and providers shared across runs."""

    def __init__(self, *, ocr: FakeOcr | None = None, requires_review: int = 1):
        """Store the configuration."""
        self.db = FakeD1()
        self.blobs = FakeBlobStore()
        self.index = FakeVectorIndex()
        self.ocr = ocr or FakeOcr(body=FIXTURE.read_bytes())
        self.embedder = FakeEmbedder(DIMS)
        source = self.db.seed(
            version_id=VERSION_ID, pdf=PDF, requires_review=requires_review
        )
        self.sha = source["sha256"]
        self.blobs.objects[source["key"]] = PDF
        self.chunker = StructuralChunker(max_chars=400, min_chars=50)

    def deps(self, **overrides) -> IngestionDeps:
        """Compose the ports, with optional overrides."""
        values = {
            "blobs": self.blobs,
            "ocr": self.ocr,
            "embedder": self.embedder,
            "repository": D1VersionRepository(self.db),
            "index": self.index,
            "chunker": self.chunker,
            "config": IngestionConfig(
                ocr_version=OCR_VERSION,
                ocr_max_pages=OCR_MAX_PAGES,
                embedding_model=MODEL,
                embedding_dimensions=DIMS,
                secrets=(SECRET,),
            ),
        }
        values.update(overrides)
        return IngestionDeps(**values)

    def payload(self) -> dict:
        """The params the API sends when creating instance ``ver_{version_id}``."""
        return {
            "version_id": VERSION_ID,
            "document_id": "doc-1",
            "source_r2_key": f"raw/{self.sha}.pdf",
            "source_sha256": self.sha,
        }

    def run(self, step: FakeStep | None = None, payload=None, **overrides) -> dict:
        """Run one Workflow instance to completion."""
        return asyncio.run(
            run_ingestion(
                payload or self.payload(),
                step or FakeStep(),
                self.deps(**overrides),
                non_retryable=FakeNonRetryableError,
            )
        )

    def fail(self, step: FakeStep | None = None, payload=None, **overrides) -> str:
        """Run expecting NonRetryableError; return its code."""
        with pytest.raises(FakeNonRetryableError) as info:
            self.run(step, payload, **overrides)
        return str(info.value)

    def chunk_ids(self) -> list[str]:
        """Return the D1 chunk ids of the version in order."""
        rows = self.db.query(
            "SELECT id FROM chunks WHERE version_id = ? ORDER BY ordinal", VERSION_ID
        )
        return [row["id"] for row in rows]

    def events(self) -> list[tuple]:
        """Return (step, status, error_code) of processing_events."""
        rows = self.db.query(
            "SELECT step, status, error_code FROM processing_events "
            "WHERE version_id = ? ORDER BY id",
            VERSION_ID,
        )
        return [(r["step"], r["status"], r["error_code"]) for r in rows]


def test_happy_path_ends_in_review_required_and_indexes_everything():
    """Happy path ends in review required and indexes everything."""
    world = World()
    step = FakeStep()
    result = world.run(step)

    version = world.db.version(VERSION_ID)
    assert version["status"] == "review_required"
    assert version["processing_attempts"] == 1
    assert version["page_count"] == 2
    assert version["extraction_r2_key"] == f"ocr/{world.sha}/{OCR_KEY_SUFFIX}"
    assert version["chunker_version"] == "structural-v1"
    assert version["published_at"] is None and version["error_code"] is None

    ids = world.chunk_ids()
    assert result["status"] == "review_required"
    assert result["chunk_count"] == len(ids) >= 3
    assert set(world.index.vectors) == set(ids)
    metadata = world.index.vectors[ids[0]].metadata
    assert metadata == {"version_id": VERSION_ID, "document_id": "doc-1"}
    assert all(len(v.values) == DIMS for v in world.index.vectors.values())

    # FTS is synchronized by the schema triggers.
    hits = world.db.query(
        "SELECT c.id FROM chunks_fts JOIN chunks c ON c.seq = chunks_fts.rowid "
        "WHERE chunks_fts MATCH '\"inscripcion\"'"
    )
    assert hits
    models = world.db.query("SELECT DISTINCT embedding_model AS m FROM chunks")
    assert models == [{"m": MODEL}]
    pages = world.db.query("SELECT MIN(page_start) a, MAX(page_end) b FROM chunks")
    assert pages == [{"a": 1, "b": 2}]

    assert step.executed == [
        "mark_processing",
        "ocr",
        "chunk",
        "embed_0",
        "index",
        "finalize",
    ]
    assert world.ocr.calls == 1 and world.embedder.calls == 1
    assert f"ocr/{world.sha}/{OCR_KEY_SUFFIX}" in world.blobs.objects
    assert f"chunks/{VERSION_ID}/structural-v1.jsonl" in world.blobs.objects
    assert (
        f"embeddings/{VERSION_ID}/structural-v1/{MODEL}/0.json" in world.blobs.objects
    )
    assert world.events() == [
        ("extract", "started", None),
        ("extract", "succeeded", None),
        ("chunk", "succeeded", None),
        ("index_fts", "succeeded", None),
        ("embed", "succeeded", None),
        ("index_vectors", "succeeded", None),
        ("review", "started", None),
    ]
    assert step.configs["ocr"]["retries"]["limit"] == 1


def test_new_extraction_always_requires_review_never_published():
    """Even a seed with requires_review=0 ends review_required, not published."""
    world = World(requires_review=0)
    assert world.run()["status"] == "review_required"
    version = world.db.version(VERSION_ID)
    assert version["requires_review"] == 1
    assert version["published_at"] is None


def test_published_version_is_refused_and_left_untouched():
    """Published version is refused and left untouched."""
    world = World()
    world.run()
    world.db.conn.execute(
        "UPDATE document_versions SET requires_review = 0 WHERE id = ?",
        (VERSION_ID,),
    )
    world.db.conn.execute(
        "UPDATE document_versions SET status = 'ready' WHERE id = ?", (VERSION_ID,)
    )
    world.db.conn.execute(
        "UPDATE document_versions SET status = 'published', "
        "published_at = '2030-01-01T00:00:00Z' WHERE id = ?",
        (VERSION_ID,),
    )
    assert world.fail() == "invalid_state"
    version = world.db.version(VERSION_ID)
    assert version["status"] == "published" and version["error_code"] is None


def test_reprocess_reuses_ocr_and_embeddings_and_replaces_chunks():
    """Reprocess reuses ocr and embeddings and replaces chunks."""
    world = World()
    world.run()
    first_ids = world.chunk_ids()
    world.ocr.calls = world.embedder.calls = 0

    # Same OCR and chunker: zero paid calls, same ids, nothing stale.
    result = world.run(FakeStep())
    assert world.ocr.calls == 0 and world.embedder.calls == 0
    assert result["ocr_reused"] is True and result["embedding_batches_reused"] == 1
    assert world.chunk_ids() == first_ids
    assert result["vectors_deleted"] == 0
    version = world.db.version(VERSION_ID)
    assert version["status"] == "review_required"
    assert version["processing_attempts"] == 2

    # New chunking rules: chunks and Vectorize ids are replaced, OCR still reused.
    result = world.run(
        FakeStep(), chunker=StructuralChunker(max_chars=150, min_chars=30)
    )
    new_ids = world.chunk_ids()
    assert world.ocr.calls == 0
    assert new_ids != first_ids
    assert set(world.index.vectors) == set(new_ids)
    stale = set(first_ids) - set(new_ids)
    assert stale and result["vectors_deleted"] == len(stale)
    assert sorted(stale) == sorted(i for batch in world.index.deletes for i in batch)
    count = world.db.query("SELECT COUNT(*) n FROM chunks")[0]["n"]
    fts = world.db.query("SELECT COUNT(*) n FROM chunks_fts")[0]["n"]
    assert count == fts == len(new_ids)
    world.db.conn.execute(
        "INSERT INTO chunks_fts (chunks_fts, rank) VALUES ('integrity-check', 1)"
    )


def test_reprocess_from_failed_goes_back_through_processing():
    """Reprocess from failed goes back through processing."""
    world = World(ocr=FakeOcr(error=IngestionError(ErrorCode.OCR_FAILED, "status=503")))
    assert world.fail() == "ocr_failed"
    assert world.db.version(VERSION_ID)["status"] == "failed"
    world.ocr.error, world.ocr.body = None, FIXTURE.read_bytes()
    assert world.run()["status"] == "review_required"
    version = world.db.version(VERSION_ID)
    assert version["error_code"] is None and version["error_detail"] is None
    assert version["processing_attempts"] == 2


def test_ocr_4xx_fails_without_retry():
    """Ocr 4xx fails without retry."""
    error = IngestionError(ErrorCode.OCR_FAILED, "status=402", permanent=True)
    world = World(ocr=FakeOcr(error=error))
    step = FakeStep()
    assert world.fail(step) == "ocr_failed"
    assert world.ocr.calls == 1 and step.attempts["ocr"] == 1
    version = world.db.version(VERSION_ID)
    assert version["status"] == "failed"
    assert version["error_code"] == "ocr_failed"
    assert version["error_detail"] == "step=ocr status=402"
    assert world.events()[-1] == ("extract", "failed", "ocr_failed")
    assert not world.index.vectors and not world.chunk_ids()
    assert not any(key.startswith("ocr/") for key in world.blobs.objects)


def test_ocr_transient_error_is_retried_once():
    """Ocr transient error is retried once."""
    error = IngestionError(ErrorCode.OCR_FAILED, "status=503")
    world = World(ocr=FakeOcr(error=error))
    step = FakeStep()
    assert world.fail(step) == "ocr_failed"
    assert world.ocr.calls == 2 and step.attempts["ocr"] == 2


def test_empty_ocr_fails_with_ocr_empty():
    """Empty ocr fails with ocr empty."""
    body = json.dumps(
        {
            "success": True,
            "data": {
                "markdown": "",
                "pages": [
                    {"pageNumber": 1, "markdown": "   "},
                    {"pageNumber": 2, "markdown": ""},
                ],
                "metadata": {"numPages": 2, "totalPages": 2},
            },
        }
    ).encode()
    world = World(ocr=FakeOcr(body=body))
    step = FakeStep()
    assert world.fail(step) == "ocr_empty"
    version = world.db.version(VERSION_ID)
    assert version["status"] == "failed" and version["error_code"] == "ocr_empty"
    assert step.attempts["chunk"] == 1  # permanent: not retried
    assert world.events()[-1] == ("chunk", "failed", "ocr_empty")
    assert world.embedder.calls == 0


def test_wrong_embedding_dimension_fails():
    """Wrong embedding dimension fails."""
    world = World()
    world.embedder = FakeEmbedder(dimensions=DIMS - 3)
    step = FakeStep()
    assert world.fail(step) == "embedding_failed"
    version = world.db.version(VERSION_ID)
    assert version["status"] == "failed"
    assert version["error_code"] == "embedding_failed"
    assert "dimension_mismatch expected=8 got=5" in version["error_detail"]
    assert step.attempts["embed_0"] == 1
    assert not world.index.vectors
    assert not any(k.startswith("embeddings/") for k in world.blobs.objects)


def test_transient_embedding_error_is_retried_then_succeeds():
    """Transient embedding error is retried then succeeds."""
    world = World()
    world.embedder = FakeEmbedder(DIMS, fail_times=2)
    step = FakeStep()
    assert world.run(step)["status"] == "review_required"
    assert step.attempts["embed_0"] == 3


def test_resume_after_crash_is_idempotent():
    """Resume after crash is idempotent."""
    world = World()
    cache: dict = {}
    for crash_point in ("ocr", "chunk", "embed_0", "index"):
        with pytest.raises(SimulatedCrash):
            world.run(FakeStep(cache=cache, crash_after=crash_point))
    step = FakeStep(cache=cache)
    result = world.run(step)
    assert step.executed == ["finalize"]
    assert result["status"] == "review_required"
    assert world.ocr.calls == 1 and world.embedder.calls == 1
    assert world.db.version(VERSION_ID)["processing_attempts"] == 1


@pytest.mark.parametrize(
    "step_name", ["mark_processing", "ocr", "chunk", "embed_0", "index", "finalize"]
)
def test_reexecuting_a_completed_step_body_is_idempotent(step_name):
    """The body ran but the engine lost its result: running it again is harmless."""
    reference = World()
    reference.run()

    world = World()
    cache: dict = {}
    with pytest.raises(SimulatedCrash):
        world.run(FakeStep(cache=cache, crash_after=step_name))
    del cache[step_name]
    step = FakeStep(cache=cache)
    result = world.run(step)

    assert step.executed[0] == step_name
    assert result["status"] == "review_required"
    assert world.chunk_ids() == reference.chunk_ids()
    assert set(world.index.vectors) == set(reference.index.vectors)
    assert world.ocr.calls == 1 and world.embedder.calls == 1
    version = world.db.version(VERSION_ID)
    assert version["status"] == "review_required"
    assert version["processing_attempts"] == 1


def test_error_detail_never_contains_secrets_or_exception_text():
    """Error detail never contains secrets or exception text."""

    class LeakyOcr(FakeOcr):
        async def parse(self, pdf, *, filename):
            raise RuntimeError(f"Authorization: Bearer {SECRET} texto del documento")

    world = World(ocr=LeakyOcr())
    assert world.fail() == "ocr_failed"
    detail = world.db.version(VERSION_ID)["error_detail"]
    assert detail == "step=ocr error=RuntimeError"

    leaky = IngestionError(ErrorCode.OCR_FAILED, f"key={SECRET}", permanent=True)
    world = World(ocr=FakeOcr(error=leaky))
    world.fail()
    detail = world.db.version(VERSION_ID)["error_detail"]
    assert SECRET not in detail and "redacted" in detail
    stored = world.db.query("SELECT detail FROM processing_events")
    assert all(SECRET not in (row["detail"] or "") for row in stored)


def test_invalid_params_and_missing_version_raise_non_retryable():
    """Invalid params and missing version raise non retryable."""
    world = World()
    with pytest.raises(FakeNonRetryableError, match="invalid_source"):
        asyncio.run(
            run_ingestion(
                {"version_id": " bad "},
                FakeStep(),
                world.deps(),
                non_retryable=FakeNonRetryableError,
            )
        )
    with pytest.raises(FakeNonRetryableError, match="invalid_source"):
        asyncio.run(
            run_ingestion(
                {"version_id": "ver-missing"},
                FakeStep(),
                world.deps(),
                non_retryable=FakeNonRetryableError,
            )
        )


def test_source_tampering_is_rejected_before_paying_ocr():
    """Source tampering is rejected before paying ocr."""
    world = World()
    world.blobs.objects[f"raw/{world.sha}.pdf"] = b"%PDF-1.7 other bytes"
    assert world.fail() == "invalid_source"
    assert world.ocr.calls == 0
    assert world.db.version(VERSION_ID)["error_detail"] == "step=ocr sha256_mismatch"


def test_many_chunks_use_several_embedding_batches():
    """Many chunks use several embedding batches."""
    articles = "\n\n".join(
        f"ARTÍCULO {n}. TEMA {n}. Texto ficticio número {n} para la prueba de lotes."
        for n in range(1, 131)
    )
    body = json.dumps(
        {
            "success": True,
            "data": {
                "pages": [{"pageNumber": 1, "markdown": articles}],
                "metadata": {"numPages": 1, "totalPages": 1},
            },
        }
    ).encode()
    world = World(ocr=FakeOcr(body=body))
    world.chunker = StructuralChunker(max_chars=120, min_chars=20)
    step = FakeStep()
    result = world.run(step)
    assert result["chunk_count"] > ingestion.EMBED_BATCH_SIZE
    batches = ingestion.batch_count(result["chunk_count"])
    assert batches == 2
    assert [s for s in step.executed if s.startswith("embed_")] == [
        "embed_0",
        "embed_1",
    ]
    assert world.embedder.calls == 2
    assert len(world.index.vectors) == result["chunk_count"]


def test_d1_transient_failure_in_second_chunk_batch_is_retried():
    """A failure after one committed batch is retried and leaves exact chunks."""
    clean = World()
    clean.run()
    expected = clean.chunk_ids()
    assert len(expected) > 1

    world = World()
    # 100 statements per batch: shrink so the chunks span at least two batches.
    original = d1_repository.STATEMENTS_PER_BATCH
    d1_repository.STATEMENTS_PER_BATCH = 2
    try:
        real_batch = world.db.batch
        calls = {"n": 0}

        async def flaky(statements):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("D1_ERROR: simulated")
            return await real_batch(statements)

        world.db.batch = flaky
        step = FakeStep()
        assert world.run(step)["status"] == "review_required"
    finally:
        d1_repository.STATEMENTS_PER_BATCH = original
    assert step.attempts["chunk"] == 2
    assert calls["n"] > 2  # the first batch committed, the second failed once
    assert world.chunk_ids() == expected
    count = world.db.query("SELECT COUNT(*) n FROM chunks")[0]["n"]
    assert count == len(expected)
    fts = world.db.query("SELECT COUNT(*) n FROM chunks_fts")[0]["n"]
    assert fts == len(expected)


def test_payload_ids_must_match_d1_before_paying_ocr():
    """Payload ids must match d1 before paying ocr."""
    world = World()
    payload = world.payload() | {"source_sha256": "f" * 64}
    assert world.fail(payload=payload) == "invalid_source"
    assert world.ocr.calls == 0
    version = world.db.version(VERSION_ID)
    assert version["status"] == "failed"
    assert version["error_detail"] == (
        "step=mark_processing payload_mismatch=source_sha256"
    )
    # Only version_id is required; a correct retry recovers from failed.
    assert world.run(payload={"version_id": VERSION_ID})["status"] == "review_required"


def test_api_restart_after_admin_review_goes_back_to_review_required():
    """API reprocess: requires_review=1, status untouched, instance restarted."""
    world = World()
    assert world.run()["status"] == "review_required"
    assert world.db.version(VERSION_ID)["processing_attempts"] == 1
    assert world.run(FakeStep())["status"] == "review_required"
    assert world.db.version(VERSION_ID)["processing_attempts"] == 2


def test_requires_review_reset_before_workflow_starts_does_not_skip_review():
    """A PATCH requires_review=false between reprocess and start is overridden."""
    world = World()
    assert world.run()["status"] == "review_required"
    world.db.conn.execute(
        "UPDATE document_versions SET requires_review = 0 WHERE id = ?",
        (VERSION_ID,),
    )
    world.db.conn.execute(
        "UPDATE document_versions SET status = 'ready' WHERE id = ?", (VERSION_ID,)
    )
    assert world.run(FakeStep())["status"] == "review_required"
    assert world.db.version(VERSION_ID)["requires_review"] == 1


def _truncated_body() -> bytes:
    body = json.loads(FIXTURE.read_bytes())
    metadata = body["data"].setdefault("metadata", {})
    metadata["numPages"] = 2
    metadata["totalPages"] = 50
    return json.dumps(body).encode()


def test_truncated_ocr_is_not_cached_and_reprocess_calls_ocr_again():
    """A truncated response is never stored; the next run pays a new OCR call."""
    world = World(ocr=FakeOcr(body=_truncated_body()))
    assert world.fail() == "ocr_failed"
    assert world.ocr.calls == 1
    assert not any(key.startswith("ocr/") for key in world.blobs.objects)
    world.ocr = FakeOcr(body=FIXTURE.read_bytes())
    assert world.run(FakeStep())["status"] == "review_required"
    assert world.ocr.calls == 1


def test_ocr_cache_key_includes_max_pages():
    """Changing OCR_MAX_PAGES does not reuse a response parsed with another cap."""
    world = World()
    world.run()
    assert world.ocr.calls == 1
    config = IngestionConfig(
        ocr_version=OCR_VERSION,
        ocr_max_pages=OCR_MAX_PAGES + 1,
        embedding_model=MODEL,
        embedding_dimensions=DIMS,
        secrets=(SECRET,),
    )
    world.run(FakeStep(), config=config)
    assert world.ocr.calls == 2
    assert f"ocr/{world.sha}/{OCR_VERSION}-p{OCR_MAX_PAGES + 1}.json" in (
        world.blobs.objects
    )


def test_mark_failed_failure_is_logged_and_leaves_processing(caplog):
    """If D1 is down for mark_failed the error type is logged; resume works."""

    class BadOcr(FakeOcr):
        async def parse(self, pdf, *, filename):
            raise IngestionError(ErrorCode.OCR_FAILED, "status=400", permanent=True)

    world = World(ocr=BadOcr(body=b""))
    original = D1VersionRepository.mark_failed

    async def broken(self, *args, **kwargs):
        raise RuntimeError("D1 down")

    D1VersionRepository.mark_failed = broken
    try:
        with caplog.at_level("ERROR"):
            assert world.fail() == "ocr_failed"
    finally:
        D1VersionRepository.mark_failed = original
    assert world.db.version(VERSION_ID)["status"] == "processing"
    assert any(
        "mark_failed_error" in r.getMessage() and "RuntimeError" in r.getMessage()
        for r in caplog.records
    )
    # The API allows reprocessing processing/extracted: the Workflow accepts both.
    world.ocr = FakeOcr(body=FIXTURE.read_bytes())
    assert world.run(FakeStep())["status"] == "review_required"
    world.db.conn.execute(
        "UPDATE document_versions SET status = 'processing' WHERE id = ?",
        (VERSION_ID,),
    )
    world.db.conn.execute(
        "UPDATE document_versions SET status = 'extracted' WHERE id = ?",
        (VERSION_ID,),
    )
    assert world.run(FakeStep())["status"] == "review_required"


def test_mark_extracted_false_is_a_permanent_invalid_state():
    """If mark_extracted changes nothing the run stops with invalid_state."""
    world = World()

    async def refuse(self, *args, **kwargs):
        return False

    original = D1VersionRepository.mark_extracted
    D1VersionRepository.mark_extracted = refuse
    try:
        assert world.fail() == "invalid_state"
    finally:
        D1VersionRepository.mark_extracted = original


def test_non_retryable_error_uses_stable_code_from_message():
    """NonRetryableError(code) raised inside a step keeps its stable code."""
    world = World()
    real = D1VersionRepository.get_version

    async def boom(self, version_id):
        raise FakeNonRetryableError("config_missing")

    D1VersionRepository.get_version = boom
    try:
        assert world.fail() == "config_missing"
    finally:
        D1VersionRepository.get_version = real
    assert world.db.version(VERSION_ID)["error_code"] == "config_missing"


def test_unattributed_engine_error_uses_stable_code_and_current_step():
    """An engine-level error without a recorded failure still gets its code/step."""

    class EngineFailsAtIndex(FakeStep):
        def do(self, name=None, *, concurrent=False, config=None):
            decorator = super().do(name, concurrent=concurrent, config=config)

            def wrap(func):
                if name != "index":
                    return decorator(func)

                async def failing():
                    raise FakeNonRetryableError("index_failed")

                return failing

            return wrap

    world = World()
    assert world.fail(EngineFailsAtIndex()) == "index_failed"
    version = world.db.version(VERSION_ID)
    assert version["status"] == "failed"
    assert version["error_detail"].startswith("step=index")
    assert ("index_vectors", "failed", "index_failed") in world.events()

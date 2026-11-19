"""D1 adapter for ``VersionRepository`` over the ``DB`` binding.

The binding is the SDK-wrapped D1 database: ``prepare(sql).bind(*args)`` with
``all()``/``run()``/``first()`` and ``batch([...])`` (atomic). Results arrive
as Python dicts (or attribute objects) shaped ``{"results": [...], "meta":
{"changes": n, ...}}``.

``LOG_EVENT`` is a verbatim copy of the canonical ``log_event`` block of
``infra/d1/queries.sql`` (a test keeps them identical). The other statements
are specific to the ingestion state machine and are not yet in that file.
Status transitions are also enforced by the schema triggers; the ``WHERE``
clauses make every write idempotent and never target ``published``.
"""

from collections.abc import Mapping, Sequence

from unitropico_ingestion.domain.chunking import Chunk
from unitropico_ingestion.domain.ingestion import VersionRecord
from unitropico_ingestion.services.ingestion import token_estimate

LOG_EVENT = """
INSERT INTO processing_events (version_id, step, status, error_code, detail)
VALUES (?1, ?2, ?3, ?4, ?5);"""

GET_VERSION = """SELECT id, document_id, status, source_r2_key, source_sha256,
       requires_review
FROM document_versions
WHERE id = ?1;"""

START_PROCESSING = """UPDATE document_versions
SET status = 'processing',
    processing_attempts = processing_attempts + 1,
    requires_review = 1,
    error_code = NULL,
    error_detail = NULL
WHERE id = ?1
  AND status IN ('uploaded', 'failed', 'extracted', 'review_required', 'ready',
                 'unpublished');"""

MARK_EXTRACTED = """UPDATE document_versions
SET status = 'extracted', page_count = ?2, extraction_r2_key = ?3
WHERE id = ?1 AND status IN ('processing', 'extracted');"""

DELETE_CHUNKS = "DELETE FROM chunks WHERE version_id = ?1;"

INSERT_CHUNK = """INSERT INTO chunks (id, version_id, ordinal, text, text_sha256,
                    section, page_start, page_end, token_estimate)
VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9);"""

SET_CHUNKER_VERSION = """UPDATE document_versions SET chunker_version = ?2
WHERE id = ?1 AND status = 'extracted';"""

SET_EMBEDDING_MODEL = "UPDATE chunks SET embedding_model = ?2 WHERE version_id = ?1;"

FINALIZE = """UPDATE document_versions
SET status = CASE WHEN requires_review = 1 THEN 'review_required' ELSE 'ready' END
WHERE id = ?1 AND status = 'extracted';"""

STATUS = "SELECT status FROM document_versions WHERE id = ?1;"

MARK_FAILED = """UPDATE document_versions
SET status = 'failed', error_code = ?2, error_detail = ?3
WHERE id = ?1
  AND status IN ('uploaded', 'processing', 'extracted', 'review_required', 'ready',
                 'failed');"""

STATEMENTS_PER_BATCH = 100


def _field(obj: object, name: str, default: object = None) -> object:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _changes(result: object) -> int:
    """Read ``meta.changes``; JS numbers may arrive as float through the SDK.

    D1 counts rows changed by triggers too (observed in local D1: a one-row
    UPDATE reports 2 because of ``document_versions_touch``), so callers only
    test ``> 0``.
    """
    changes = _field(_field(result, "meta", {}), "changes", 0)
    if isinstance(changes, bool) or not isinstance(changes, int | float):
        return 0
    return int(changes)


class D1VersionRepository:
    """Implement ``VersionRepository`` with bound parameters only."""

    def __init__(self, db: object):
        """Receive the D1 binding at composition time."""
        self._db = db

    def _stmt(self, sql: str, *args: object) -> object:
        return self._db.prepare(sql).bind(*args)  # type: ignore[attr-defined]

    async def get_version(self, version_id: str) -> VersionRecord | None:
        """Return the version or None."""
        row = await self._stmt(GET_VERSION, version_id).first()  # type: ignore[attr-defined]
        if row is None:
            return None
        return VersionRecord(
            id=str(_field(row, "id")),
            document_id=str(_field(row, "document_id")),
            status=str(_field(row, "status")),
            source_r2_key=str(_field(row, "source_r2_key")),
            source_sha256=str(_field(row, "source_sha256")),
            requires_review=bool(_field(row, "requires_review")),
        )

    async def _run(self, sql: str, *args: object) -> int:
        return _changes(await self._stmt(sql, *args).run())  # type: ignore[attr-defined]

    async def start_processing(self, version_id: str) -> bool:
        """Move to processing and count an attempt."""
        return await self._run(START_PROCESSING, version_id) > 0

    async def mark_extracted(
        self, version_id: str, *, page_count: int, extraction_key: str
    ) -> bool:
        """Record OCR provenance and move to extracted."""
        return (
            await self._run(MARK_EXTRACTED, version_id, page_count, extraction_key) > 0
        )

    async def replace_chunks(
        self, version_id: str, *, chunker_version: str, chunks: Sequence[Chunk]
    ) -> None:
        """Delete and insert chunks in atomic batches (first batch deletes)."""
        statements = [self._stmt(DELETE_CHUNKS, version_id)]
        statements += [
            self._stmt(
                INSERT_CHUNK,
                chunk.id,
                version_id,
                chunk.ordinal,
                chunk.text,
                chunk.text_sha256,
                chunk.section,
                chunk.page_start,
                chunk.page_end,
                token_estimate(chunk.text),
            )
            for chunk in chunks
        ]
        statements.append(self._stmt(SET_CHUNKER_VERSION, version_id, chunker_version))
        for start in range(0, len(statements), STATEMENTS_PER_BATCH):
            await self._db.batch(  # type: ignore[attr-defined]
                statements[start : start + STATEMENTS_PER_BATCH]
            )

    async def set_embedding_model(self, version_id: str, model: str) -> None:
        """Record the embedding model on the version chunks."""
        await self._run(SET_EMBEDDING_MODEL, version_id, model)

    async def finalize(self, version_id: str) -> str | None:
        """Move extracted to review_required/ready and return the status."""
        await self._run(FINALIZE, version_id)
        row = await self._stmt(STATUS, version_id).first()  # type: ignore[attr-defined]
        return None if row is None else str(_field(row, "status"))

    async def mark_failed(self, version_id: str, *, code: str, detail: str) -> bool:
        """Move to failed unless the version is published/unpublished."""
        return await self._run(MARK_FAILED, version_id, code, detail or None) > 0

    async def log_event(
        self,
        version_id: str,
        *,
        step: str,
        status: str,
        error_code: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Append a processing event."""
        await self._run(LOG_EVENT, version_id, step, status, error_code, detail)

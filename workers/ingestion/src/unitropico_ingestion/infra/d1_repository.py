"""D1 adapter for ``VersionRepository`` over the ``DB`` binding.

The binding is the SDK-wrapped D1 database: ``prepare(sql).bind(*args)`` with
``all()``/``run()``/``first()`` and ``batch([...])`` (atomic). Results arrive
as Python dicts (or attribute objects) shaped ``{"results": [...], "meta":
{"changes": n, ...}}``.

``LOG_EVENT``, ``ACTIVE_CORRECTIONS``, ``ACTIVE_OVERRIDES``, ``CORRECTIONS_REV``
and ``CLEAR_CORRECTIONS_DIRTY`` are verbatim copies of the canonical
``log_event``, ``active_corrections_by_version``, ``active_overrides_by_version``,
``corrections_rev_by_version`` and ``clear_corrections_dirty`` blocks of
``infra/d1/queries.sql`` (a test keeps them identical). The other statements
are specific to the ingestion state machine and are not yet in that file.
Status transitions are also enforced by the schema triggers; the ``WHERE``
clauses make every write idempotent and never target ``published``.
"""

from collections.abc import Mapping, Sequence

from unitropico_ingestion.domain.chunking import Chunk
from unitropico_ingestion.domain.corrections import (
    CorrectionSet,
    PageOverride,
    TextCorrection,
)
from unitropico_ingestion.domain.ingestion import VersionRecord
from unitropico_ingestion.services.ingestion import token_estimate

LOG_EVENT = """
INSERT INTO processing_events (version_id, step, status, error_code, detail)
VALUES (?1, ?2, ?3, ?4, ?5);"""

ACTIVE_CORRECTIONS = """SELECT id, page_number, find_text, replace_text
FROM text_corrections
WHERE version_id = ?1 AND deleted_at IS NULL
ORDER BY created_at, rowid;"""

ACTIVE_OVERRIDES = """SELECT id, page_number, text
FROM page_overrides
WHERE version_id = ?1 AND deleted_at IS NULL
ORDER BY page_number;"""

CORRECTIONS_REV = "SELECT corrections_rev FROM document_versions WHERE id = ?1;"

CLEAR_CORRECTIONS_DIRTY = """UPDATE document_versions SET corrections_dirty = 0
WHERE id = ?1 AND corrections_rev = ?2 AND status = 'extracted';"""

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

    async def correction_set(self, version_id: str) -> CorrectionSet:
        """Return revision, active corrections and overrides from one atomic batch."""
        rev_result, rows_result, override_result = list(
            await self._db.batch(  # type: ignore[attr-defined]
                [
                    self._stmt(CORRECTIONS_REV, version_id),
                    self._stmt(ACTIVE_CORRECTIONS, version_id),
                    self._stmt(ACTIVE_OVERRIDES, version_id),
                ]
            )
        )
        revs = _field(rev_result, "results", None) or []
        revision = int(_field(revs[0], "corrections_rev", 0)) if revs else 0  # type: ignore[arg-type]
        rows = _field(rows_result, "results", None) or []
        corrections = [
            TextCorrection(
                id=str(_field(row, "id")),
                page_number=int(_field(row, "page_number")),  # type: ignore[arg-type]
                find_text=str(_field(row, "find_text")),
                replace_text=str(_field(row, "replace_text")),
            )
            for row in rows
        ]
        overrides = [
            PageOverride(
                id=str(_field(row, "id")),
                page_number=int(_field(row, "page_number")),  # type: ignore[arg-type]
                text=str(_field(row, "text")),
            )
            for row in _field(override_result, "results", None) or []
        ]
        return CorrectionSet(revision, tuple(corrections), tuple(overrides))

    async def replace_chunks(
        self,
        version_id: str,
        *,
        chunker_version: str,
        chunks: Sequence[Chunk],
        corrections_rev: int | None = None,
    ) -> bool:
        """Delete and insert chunks in atomic batches (first batch deletes).

        The last batch sets the chunker version and, with ``corrections_rev``,
        clears ``corrections_dirty`` by compare-and-set on the revision.
        """
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
        if corrections_rev is not None:
            statements.append(
                self._stmt(CLEAR_CORRECTIONS_DIRTY, version_id, corrections_rev)
            )
        results: list = []
        for start in range(0, len(statements), STATEMENTS_PER_BATCH):
            results = list(
                await self._db.batch(  # type: ignore[attr-defined]
                    statements[start : start + STATEMENTS_PER_BATCH]
                )
            )
        return corrections_rev is not None and _changes(results[-1]) > 0

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

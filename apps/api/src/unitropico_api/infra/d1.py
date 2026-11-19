from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from unitropico_api.core.logger import get_logger
from unitropico_api.domain.admin import (
    CORRECTIONS_PENDING,
    DUPLICATE_CORRECTION,
    OVERRIDE_EXISTS,
    REPROCESSABLE,
    TOO_MANY_CORRECTIONS,
    AdminError,
    ChunkReview,
    Conflict,
    DocumentList,
    DocumentSummary,
    DocumentWithVersions,
    InvalidInput,
    NewCorrection,
    NewOverride,
    NewVersion,
    NotFound,
    PageOverrideView,
    ProcessingEvent,
    StorageUnavailable,
    TextCorrectionView,
    VersionSummary,
    VersionView,
)

_log = get_logger()


class D1Statement(Protocol):
    """Subset of a D1 prepared statement."""

    def bind(self, *values: object) -> D1Statement:
        """Bind positional parameters ?1..?N."""
        ...

    async def all(self) -> object:
        """Return {"results": [...], "meta": {...}}."""
        ...

    async def first(self) -> object:
        """Return the first row or None."""
        ...

    async def run(self) -> object:
        """Execute without reading rows."""
        ...


class D1Database(Protocol):
    """Subset of the D1 binding used by the catalog adapter."""

    def prepare(self, sql: str) -> D1Statement:
        """Prepare one SQL statement."""
        ...

    async def batch(self, statements: list[D1Statement]) -> object:
        """Run statements in one implicit transaction."""
        ...


VERSION_COLUMNS = (
    "v.id, v.document_id, v.version_label, v.status, v.requires_review, "
    "v.error_code, v.processing_attempts, v.source_sha256, v.source_bytes, "
    "v.page_count, v.issued_at, v.effective_from, v.effective_until, "
    "v.supersedes_version_id, v.created_at, v.updated_at, v.published_at, "
    "v.corrections_dirty"
)
VIEW_SELECT = (
    f"SELECT {VERSION_COLUMNS}, d.title AS d_title, "
    "d.document_type AS d_document_type, d.created_at AS d_created_at "
    "FROM document_versions v JOIN documents d ON d.id = v.document_id "
)
INSERT_DOCUMENT = "INSERT INTO documents (id, title, document_type) VALUES (?1, ?2, ?3)"
# Same statement as insert_version_idempotent in infra/d1/queries.sql.
INSERT_VERSION = (
    "INSERT INTO document_versions (id, document_id, version_label, source_r2_key, "
    "source_sha256, source_bytes) VALUES (?1, ?2, ?3, ?4, ?5, ?6) "
    "ON CONFLICT (document_id, source_sha256) DO NOTHING"
)
LOG_EVENT = (
    "INSERT INTO processing_events (version_id, step, status, error_code, detail) "
    "VALUES (?1, ?2, ?3, ?4, ?5)"
)
# True when no other version of the same document is published.
NO_PUBLISHED_SIBLING = (
    "NOT EXISTS (SELECT 1 FROM document_versions AS sibling "
    "WHERE sibling.document_id = document_versions.document_id "
    "AND sibling.status = 'published')"
)
# Logs only if the preceding statement of the batch stamped updated_at = ?3.
LOG_EVENT_IF_STAMPED = (
    "INSERT INTO processing_events (version_id, step, status, detail) "
    "SELECT ?1, ?2, 'succeeded', ?4 WHERE EXISTS "
    "(SELECT 1 FROM document_versions WHERE id = ?1 AND updated_at = ?3)"
)
TRIGGER_CONFLICTS = {
    "invalid_status_transition": "La transición de estado no está permitida.",
    "publish_without_chunks": "La versión no tiene fragmentos indexados.",
    "chunk_page_out_of_range": "Hay fragmentos fuera del rango de páginas.",
    "published_version_immutable": "La versión publicada no puede modificarse.",
    "correction_page_out_of_range": "La página supera el número de páginas.",
    "text_correction_immutable": "Las correcciones no pueden editarse.",
    "override_page_out_of_range": "La página supera el número de páginas.",
    "page_override_immutable": "Las sustituciones de página no pueden editarse.",
    "corrections_pending": (
        "Hay correcciones sin aplicar: reprocese la versión antes de publicarla."
    ),
}
# Same statements as infra/d1/queries.sql (chunks_page_by_version,
# count_chunks_by_version, corrections_by_version, insert_correction,
# soft_delete_correction); a test keeps them identical.
CHUNKS_PAGE = (
    "SELECT c.ordinal, c.id, c.section, c.page_start, c.page_end, c.text, "
    "v.chunker_version FROM chunks c "
    "JOIN document_versions v ON v.id = c.version_id "
    "WHERE c.version_id = ?1 ORDER BY c.ordinal LIMIT ?2 OFFSET ?3"
)
COUNT_CHUNKS = "SELECT count(*) AS total FROM chunks WHERE version_id = ?1"
CORRECTIONS = (
    "SELECT id, version_id, page_number, find_text, replace_text, reason, "
    "created_at, deleted_at FROM text_corrections WHERE version_id = ?1 "
    "ORDER BY created_at, rowid"
)
CORRECTION_BY_ID = (
    "SELECT id, version_id, page_number, find_text, replace_text, reason, "
    "created_at, deleted_at FROM text_corrections WHERE version_id = ?1 AND id = ?2"
)
INSERT_CORRECTION = (
    "INSERT INTO text_corrections (id, version_id, page_number, find_text, "
    "replace_text, reason) SELECT ?1, ?2, ?3, ?4, ?5, ?6 "
    "WHERE (SELECT count(*) FROM text_corrections "
    "WHERE version_id = ?2 AND deleted_at IS NULL) < 100 RETURNING id"
)
# Logs the creation only if INSERT_CORRECTION inserted the row (same batch).
LOG_CREATION_IF_INSERTED = (
    "INSERT INTO processing_events (version_id, step, status, detail) "
    "SELECT ?2, 'correct', 'succeeded', "
    "'correction_created id=' || ?1 || ' page=' || ?3 "
    "WHERE EXISTS (SELECT 1 FROM text_corrections "
    "WHERE id = ?1 AND version_id = ?2 AND deleted_at IS NULL)"
)
SOFT_DELETE_CORRECTION = (
    "UPDATE text_corrections SET deleted_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
    "WHERE id = ?1 AND version_id = ?2 AND deleted_at IS NULL RETURNING id"
)
# Logs the deletion only while the correction is still active; it runs before
# SOFT_DELETE_CORRECTION in the same batch.
LOG_DELETION_IF_ACTIVE = (
    "INSERT INTO processing_events (version_id, step, status, detail) "
    "SELECT ?2, 'correct', 'succeeded', 'correction_deleted id=' || ?1 "
    "WHERE EXISTS (SELECT 1 FROM text_corrections "
    "WHERE id = ?1 AND version_id = ?2 AND deleted_at IS NULL)"
)
# A pending correction invalidates the review: ready/unpublished go back to
# review_required so the version cannot be published before a new review
# (normally after reprocessing). ?2 = correction id that must still be active
# (deletion, runs before the soft delete) or was just inserted (creation).
REQUIRE_REVIEW_FOR_CORRECTION = (
    "UPDATE document_versions SET requires_review = 1, "
    "status = CASE WHEN status IN ('ready', 'unpublished') "
    "THEN 'review_required' ELSE status END "
    "WHERE id = ?1 AND status <> 'published' AND (?2 IS NULL OR EXISTS "
    "(SELECT 1 FROM text_corrections WHERE id = ?2 AND version_id = ?1 "
    "AND deleted_at IS NULL))"
)
# Same statements as infra/d1/queries.sql (overrides_by_version,
# insert_override, soft_delete_override); a test keeps them identical.
OVERRIDES = (
    "SELECT id, version_id, page_number, text, reason, created_at, deleted_at "
    "FROM page_overrides WHERE version_id = ?1 ORDER BY created_at, rowid"
)
OVERRIDE_BY_ID = (
    "SELECT id, version_id, page_number, text, reason, created_at, deleted_at "
    "FROM page_overrides WHERE version_id = ?1 AND id = ?2"
)
INSERT_OVERRIDE = (
    "INSERT INTO page_overrides (id, version_id, page_number, text, reason) "
    "VALUES (?1, ?2, ?3, ?4, ?5) RETURNING id"
)
SOFT_DELETE_OVERRIDE = (
    "UPDATE page_overrides SET deleted_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
    "WHERE id = ?1 AND version_id = ?2 AND deleted_at IS NULL RETURNING id"
)
# The INSERT runs first in the batch: if it fails nothing else is written.
LOG_OVERRIDE_CREATED = (
    "INSERT INTO processing_events (version_id, step, status, detail) "
    "VALUES (?2, 'correct', 'succeeded', "
    "'override_created id=' || ?1 || ' page=' || ?3)"
)
# Logs the deletion only while the override is still active; it runs before
# SOFT_DELETE_OVERRIDE in the same batch.
LOG_OVERRIDE_DELETED_IF_ACTIVE = (
    "INSERT INTO processing_events (version_id, step, status, detail) "
    "SELECT ?2, 'correct', 'succeeded', 'override_deleted id=' || ?1 "
    "WHERE EXISTS (SELECT 1 FROM page_overrides "
    "WHERE id = ?1 AND version_id = ?2 AND deleted_at IS NULL)"
)
# Same review invalidation as REQUIRE_REVIEW_FOR_CORRECTION, keyed on an
# override that must still be active (deletion) or was just inserted (creation).
REQUIRE_REVIEW_FOR_OVERRIDE = (
    "UPDATE document_versions SET requires_review = 1, "
    "status = CASE WHEN status IN ('ready', 'unpublished') "
    "THEN 'review_required' ELSE status END "
    "WHERE id = ?1 AND status <> 'published' AND EXISTS "
    "(SELECT 1 FROM page_overrides WHERE id = ?2 AND version_id = ?1 "
    "AND deleted_at IS NULL)"
)
VERSION_PUBLISHED = (
    "version_published",
    "La versión está publicada: despublíquela antes de corregir su texto.",
)
UNIQUE_CONFLICTS = {
    "version_label": (
        "version_label_taken",
        "Ya existe una versión con esa etiqueta en el documento.",
    ),
    "source_r2_key": (
        "source_registered_elsewhere",
        "Este archivo ya está registrado en otro documento.",
    ),
}


def _stamp() -> str:
    """Unique-enough timestamp that marks the rows written by one request."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _rows(result: object) -> list[dict[str, Any]]:
    """Extract rows from a D1 result object."""
    rows = result.get("results") if isinstance(result, Mapping) else None
    if rows is None:
        rows = getattr(result, "results", None) or []
    return [dict(row) for row in rows]


def translate_error(
    error: Exception, check_code: str = "constraint_violation"
) -> AdminError:
    """Map a D1/SQLite failure to a domain error without leaking SQL."""
    message = str(error)
    for code, text in TRIGGER_CONFLICTS.items():
        if code in message:
            return Conflict(code, text)
    if "UNIQUE constraint failed" in message:
        if "text_corrections." in message:
            return Conflict(*DUPLICATE_CORRECTION)
        if "page_overrides." in message:
            return Conflict(*OVERRIDE_EXISTS)
        for column, (code, text) in UNIQUE_CONFLICTS.items():
            if column in message:
                return Conflict(code, text)
        return Conflict("duplicate", "El registro ya existe.")
    if "CHECK constraint failed" in message:
        return Conflict(check_code, "Los datos no cumplen las reglas del catálogo.")
    if "FOREIGN KEY constraint failed" in message:
        return Conflict("reference_not_found", "Una referencia no existe.")
    _log.warning("d1 failure type=%s", type(error).__name__)
    return StorageUnavailable(
        "storage_unavailable", "El catálogo no está disponible en este momento."
    )


def _correction(row: Mapping[str, Any]) -> TextCorrectionView:
    """Build a TextCorrectionView from a CORRECTIONS row."""
    return TextCorrectionView(**row, active=row["deleted_at"] is None)


def _correction_error(error: AdminError) -> AdminError:
    """Translate schema guards of text_corrections to the admin contract."""
    if error.code == "published_version_immutable":
        return Conflict(*VERSION_PUBLISHED)
    if error.code in ("correction_page_out_of_range", "override_page_out_of_range"):
        return InvalidInput(
            "page_out_of_range", "La página supera el número de páginas del documento."
        )
    return error


def _override(row: Mapping[str, Any]) -> PageOverrideView:
    """Build a PageOverrideView from an OVERRIDES row."""
    return PageOverrideView(**row, active=row["deleted_at"] is None)


def _view(row: Mapping[str, Any]) -> VersionView:
    """Build a VersionView from a VIEW_SELECT row."""
    data = dict(row)
    document = DocumentSummary(
        id=data["document_id"],
        title=data.pop("d_title"),
        document_type=data.pop("d_document_type"),
        created_at=data.pop("d_created_at"),
    )
    return VersionView(document=document, version=VersionSummary.model_validate(data))


class D1DocumentRepository:
    """Catalog adapter over a D1 binding resolved at call time."""

    def __init__(self, database: Callable[[], D1Database | None]) -> None:
        """Receive a provider of the current request's D1 binding."""
        self._database = database

    def _db(self) -> D1Database:
        """Return the binding or fail closed."""
        db = self._database()
        if db is None:
            raise StorageUnavailable(
                "storage_unavailable", "El catálogo no está configurado."
            )
        return db

    def _stmt(self, sql: str, *values: object) -> D1Statement:
        """Prepare and bind one statement."""
        statement = self._db().prepare(sql)
        return statement.bind(*values) if values else statement

    async def _all(self, sql: str, *values: object) -> list[dict[str, Any]]:
        """Run a query and return its rows."""
        try:
            return _rows(await self._stmt(sql, *values).all())
        except AdminError:
            raise
        except Exception as error:  # JS exceptions have no common base.
            raise translate_error(error) from None

    async def _batch(
        self,
        statements: Sequence[D1Statement],
        check_code: str = "constraint_violation",
    ) -> list[list[dict[str, Any]]]:
        """Run statements atomically and return the rows of each."""
        try:
            results = await self._db().batch(list(statements))
        except AdminError:
            raise
        except Exception as error:  # JS exceptions have no common base.
            raise translate_error(error, check_code) from None
        return [_rows(result) for result in results]

    async def get_document(self, document_id: str) -> DocumentSummary | None:
        """Return a document or None."""
        rows = await self._all(
            "SELECT id, title, document_type, created_at FROM documents WHERE id = ?1",
            document_id,
        )
        return DocumentSummary.model_validate(rows[0]) if rows else None

    async def find_by_source_key(self, source_r2_key: str) -> VersionView | None:
        """Return the version registered for an R2 key, if any."""
        rows = await self._all(
            VIEW_SELECT + "WHERE v.source_r2_key = ?1", source_r2_key
        )
        return _view(rows[0]) if rows else None

    async def get_version(self, version_id: str) -> VersionView | None:
        """Return a version with its document, or None."""
        rows = await self._all(VIEW_SELECT + "WHERE v.id = ?1", version_id)
        return _view(rows[0]) if rows else None

    async def create_version(self, new: NewVersion) -> tuple[VersionView, bool]:
        """Insert document/version idempotently and log the stored source."""
        statements = []
        if new.create_document:
            statements.append(
                self._stmt(
                    INSERT_DOCUMENT, new.document_id, new.title, new.document_type
                )
            )
        statements.append(
            self._stmt(
                INSERT_VERSION,
                new.version_id,
                new.document_id,
                new.version_label,
                new.source_r2_key,
                new.source_sha256,
                new.source_bytes,
            )
        )
        statements.append(
            self._stmt(
                "INSERT INTO processing_events (version_id, step, status, detail) "
                "SELECT ?1, 'upload', 'succeeded', 'source_stored' WHERE EXISTS "
                "(SELECT 1 FROM document_versions WHERE id = ?1)",
                new.version_id,
            )
        )
        try:
            await self._batch(statements)
        except Conflict as error:
            if error.code != "source_registered_elsewhere":
                raise
            # A concurrent upload registered the same bytes first.
            existing = await self.find_by_source_key(new.source_r2_key)
            if existing is None or (
                not new.create_document and existing.document.id != new.document_id
            ):
                raise
            return existing, False
        rows = await self._all(
            VIEW_SELECT + "WHERE v.document_id = ?1 AND v.source_sha256 = ?2",
            new.document_id,
            new.source_sha256,
        )
        if not rows:
            raise StorageUnavailable(
                "storage_unavailable", "No se pudo registrar la versión."
            )
        view = _view(rows[0])
        return view, view.version.id == new.version_id

    async def list_documents(self) -> DocumentList:
        """Return every document with its versions, newest version first."""
        documents, versions = await self._batch(
            [
                self._stmt(
                    "SELECT id, title, document_type, created_at FROM documents "
                    "ORDER BY title, id"
                ),
                self._stmt(
                    f"SELECT {VERSION_COLUMNS} FROM document_versions v "
                    "ORDER BY v.created_at DESC, v.id"
                ),
            ]
        )
        grouped: dict[str, list[VersionSummary]] = {row["id"]: [] for row in documents}
        for row in versions:
            grouped.setdefault(row["document_id"], []).append(
                VersionSummary.model_validate(row)
            )
        return DocumentList(
            documents=[
                DocumentWithVersions(**row, versions=grouped[row["id"]])
                for row in documents
            ]
        )

    async def get_error_detail(self, version_id: str) -> str | None:
        """Return the stored diagnostic detail of a version."""
        rows = await self._all(
            "SELECT error_detail FROM document_versions WHERE id = ?1", version_id
        )
        return rows[0]["error_detail"] if rows else None

    async def list_events(self, version_id: str) -> list[ProcessingEvent]:
        """Return the processing log of a version in chronological order."""
        rows = await self._all(
            "SELECT id, step, status, error_code, detail, created_at "
            "FROM processing_events WHERE version_id = ?1 "
            "ORDER BY created_at, id LIMIT 500",
            version_id,
        )
        return [ProcessingEvent.model_validate(row) for row in rows]

    async def update_metadata(
        self, version_id: str, document: dict[str, object], version: dict[str, object]
    ) -> VersionView:
        """Apply document and version changes atomically unless published."""
        stamp = _stamp()
        statements = []
        if document:
            names = list(document)
            assignments = ", ".join(f"{n} = ?{i + 2}" for i, n in enumerate(names))
            statements.append(
                self._stmt(
                    f"UPDATE documents SET {assignments} WHERE id = "
                    "(SELECT document_id FROM document_versions "
                    "WHERE id = ?1 AND status <> 'published' "
                    f"AND {NO_PUBLISHED_SIBLING})",
                    version_id,
                    *(document[n] for n in names),
                )
            )
        values: list[object] = [version_id, stamp]
        assignments = ["updated_at = ?2"]
        slots: dict[str, str] = {}
        for name, value in version.items():
            values.append(int(value) if isinstance(value, bool) else value)
            slots[name] = f"?{len(values)}"
            assignments.append(f"{name} = {slots[name]}")
        dirty_guard = ""
        if "requires_review" in version:
            slot = slots["requires_review"]
            # Pending corrections forbid marking the version as reviewed.
            dirty_guard = f" AND NOT ({slot} = 0 AND corrections_dirty = 1)"
            assignments.append(
                f"status = CASE WHEN {slot} = 0 AND status = 'review_required' "
                f"THEN 'ready' WHEN {slot} = 1 AND status IN ('ready', 'unpublished') "
                "THEN 'review_required' ELSE status END"
            )
        # Document-level edits would alter a published sibling: apply nothing then.
        guard = f" AND {NO_PUBLISHED_SIBLING}" if document else ""
        statements.append(
            self._stmt(
                f"UPDATE document_versions SET {', '.join(assignments)} "
                f"WHERE id = ?1 AND status <> 'published'{guard}{dirty_guard} "
                "RETURNING id",
                *values,
            )
        )
        fields = ",".join(sorted([*document, *version]))
        statements.append(
            self._stmt(
                LOG_EVENT_IF_STAMPED,
                version_id,
                "review",
                stamp,
                f"metadata_updated:{fields}",
            )
        )
        results = await self._batch(statements)
        current = await self.get_version(version_id)
        if (
            dirty_guard
            and not results[-2]
            and current is not None
            and current.version.corrections_dirty
            and version["requires_review"] is False
        ):
            conflict = Conflict(*CORRECTIONS_PENDING)
        elif current is not None and current.version.status != "published" and document:
            conflict = Conflict(
                "document_has_published_version",
                "El documento tiene otra versión publicada: despublíquela antes "
                "de cambiar título o tipo.",
            )
        else:
            conflict = Conflict(
                "version_published",
                "La versión está publicada: despublíquela antes de editarla.",
            )
        return await self._after_guarded_update(version_id, results[-2], conflict)

    async def publish(self, version_id: str) -> VersionView:
        """Publish a reviewed ready/unpublished version; triggers enforce the rest."""
        stamp = _stamp()
        results = await self._batch(
            [
                self._stmt(
                    "UPDATE document_versions SET status = 'published', "
                    "published_at = ?2, updated_at = ?2 WHERE id = ?1 "
                    "AND status IN ('ready', 'unpublished') AND requires_review = 0 "
                    "AND corrections_dirty = 0 RETURNING id",
                    version_id,
                    stamp,
                ),
                self._stmt(LOG_EVENT_IF_STAMPED, version_id, "publish", stamp, None),
            ],
            check_code="publish_requirements_unmet",
        )
        stale = _stale("publicar")
        if not results[0]:
            current = await self.get_version(version_id)
            if current is not None and current.version.corrections_dirty:
                stale = Conflict(*CORRECTIONS_PENDING)
        return await self._after_guarded_update(version_id, results[0], stale)

    async def unpublish(self, version_id: str) -> VersionView:
        """Withdraw a published version."""
        stamp = _stamp()
        results = await self._batch(
            [
                self._stmt(
                    "UPDATE document_versions SET status = 'unpublished', "
                    "updated_at = ?2 WHERE id = ?1 AND status = 'published' "
                    "RETURNING id",
                    version_id,
                    stamp,
                ),
                self._stmt(LOG_EVENT_IF_STAMPED, version_id, "unpublish", stamp, None),
            ]
        )
        return await self._after_guarded_update(
            version_id, results[0], _stale("despublicar")
        )

    async def mark_for_reprocess(self, version_id: str) -> VersionView:
        """Require a new review of the next extraction and log the request."""
        stamp = _stamp()
        allowed = ", ".join(f"'{status}'" for status in sorted(REPROCESSABLE))
        results = await self._batch(
            [
                self._stmt(
                    "UPDATE document_versions SET requires_review = 1, "
                    f"updated_at = ?2 WHERE id = ?1 AND status IN ({allowed}) "
                    "RETURNING id",
                    version_id,
                    stamp,
                ),
                self._stmt(
                    LOG_EVENT_IF_STAMPED,
                    version_id,
                    "upload",
                    stamp,
                    "reprocess_requested",
                ),
            ]
        )
        return await self._after_guarded_update(
            version_id, results[0], _stale("reprocesar")
        )

    async def log_event(
        self,
        version_id: str,
        step: str,
        status: str,
        error_code: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Append one processing event."""
        await self._all(LOG_EVENT, version_id, step, status, error_code, detail)

    async def list_chunks(
        self, version_id: str, offset: int, limit: int
    ) -> tuple[int, list[ChunkReview]]:
        """Return the chunk total and one page of chunks ordered by ordinal."""
        counted, rows = await self._batch(
            [
                self._stmt(COUNT_CHUNKS, version_id),
                self._stmt(CHUNKS_PAGE, version_id, limit, offset),
            ]
        )
        total = int(counted[0]["total"]) if counted else 0
        return total, [ChunkReview.model_validate(row) for row in rows]

    async def list_corrections(self, version_id: str) -> list[TextCorrectionView]:
        """Return every correction of a version, oldest first."""
        rows = await self._all(CORRECTIONS, version_id)
        return [_correction(row) for row in rows]

    async def get_correction(
        self, version_id: str, correction_id: str
    ) -> TextCorrectionView | None:
        """Return one correction of the version, or None."""
        rows = await self._all(CORRECTION_BY_ID, version_id, correction_id)
        return _correction(rows[0]) if rows else None

    async def create_correction(self, new: NewCorrection) -> TextCorrectionView:
        """Store a correction and its creation event atomically.

        The cap of active corrections is checked by the INSERT itself (no row
        returned = cap reached) and duplicates by a partial unique index, so
        concurrent requests cannot bypass either. The schema triggers mark the
        version ``corrections_dirty`` in the same transaction.
        """
        try:
            results = await self._batch(
                [
                    self._stmt(
                        INSERT_CORRECTION,
                        new.id,
                        new.version_id,
                        new.page_number,
                        new.find_text,
                        new.replace_text,
                        new.reason,
                    ),
                    self._stmt(REQUIRE_REVIEW_FOR_CORRECTION, new.version_id, new.id),
                    self._stmt(
                        LOG_CREATION_IF_INSERTED,
                        new.id,
                        new.version_id,
                        new.page_number,
                    ),
                ],
                check_code="invalid_correction",
            )
        except AdminError as error:
            raise _correction_error(error) from None
        if not results[0]:
            raise Conflict(*TOO_MANY_CORRECTIONS)
        created = await self.get_correction(new.version_id, new.id)
        if created is None:
            raise StorageUnavailable(
                "storage_unavailable", "No se pudo registrar la corrección."
            )
        return created

    async def delete_correction(
        self, version_id: str, correction_id: str
    ) -> TextCorrectionView:
        """Logically delete a correction (idempotent) and log it once."""
        try:
            await self._batch(
                [
                    self._stmt(LOG_DELETION_IF_ACTIVE, correction_id, version_id),
                    self._stmt(
                        REQUIRE_REVIEW_FOR_CORRECTION, version_id, correction_id
                    ),
                    self._stmt(SOFT_DELETE_CORRECTION, correction_id, version_id),
                ]
            )
        except AdminError as error:
            raise _correction_error(error) from None
        current = await self.get_correction(version_id, correction_id)
        if current is None:
            raise NotFound("correction_not_found", "La corrección no existe.")
        return current

    async def get_chunker_version(self, version_id: str) -> str | None:
        """Return the chunker version recorded by the last chunk step."""
        rows = await self._all(
            "SELECT chunker_version FROM document_versions WHERE id = ?1", version_id
        )
        return rows[0]["chunker_version"] if rows else None

    async def list_overrides(self, version_id: str) -> list[PageOverrideView]:
        """Return every page override of a version, oldest first."""
        rows = await self._all(OVERRIDES, version_id)
        return [_override(row) for row in rows]

    async def get_override(
        self, version_id: str, override_id: str
    ) -> PageOverrideView | None:
        """Return one override of the version, or None."""
        rows = await self._all(OVERRIDE_BY_ID, version_id, override_id)
        return _override(rows[0]) if rows else None

    async def create_override(self, new: NewOverride) -> PageOverrideView:
        """Store an override, invalidate the review and log it atomically.

        A second active override for the page violates the partial unique index
        (409 ``override_exists``) and rolls the whole batch back; the schema
        triggers mark the version ``corrections_dirty`` in the same transaction.
        """
        try:
            await self._batch(
                [
                    self._stmt(
                        INSERT_OVERRIDE,
                        new.id,
                        new.version_id,
                        new.page_number,
                        new.text,
                        new.reason,
                    ),
                    self._stmt(REQUIRE_REVIEW_FOR_OVERRIDE, new.version_id, new.id),
                    self._stmt(
                        LOG_OVERRIDE_CREATED, new.id, new.version_id, new.page_number
                    ),
                ],
                check_code="invalid_override",
            )
        except AdminError as error:
            raise _correction_error(error) from None
        created = await self.get_override(new.version_id, new.id)
        if created is None:
            raise StorageUnavailable(
                "storage_unavailable", "No se pudo registrar la sustitución."
            )
        return created

    async def delete_override(
        self, version_id: str, override_id: str
    ) -> PageOverrideView:
        """Logically delete an override (idempotent) and log it once."""
        try:
            await self._batch(
                [
                    self._stmt(LOG_OVERRIDE_DELETED_IF_ACTIVE, override_id, version_id),
                    self._stmt(REQUIRE_REVIEW_FOR_OVERRIDE, version_id, override_id),
                    self._stmt(SOFT_DELETE_OVERRIDE, override_id, version_id),
                ]
            )
        except AdminError as error:
            raise _correction_error(error) from None
        current = await self.get_override(version_id, override_id)
        if current is None:
            raise NotFound("override_not_found", "La sustitución no existe.")
        return current

    async def _after_guarded_update(
        self, version_id: str, returned: list[dict[str, Any]], conflict: AdminError
    ) -> VersionView:
        """Return the updated view or explain why the guarded UPDATE missed."""
        view = await self.get_version(version_id)
        if view is None:
            raise NotFound("version_not_found", "La versión no existe.")
        if not returned:
            raise conflict
        return view


def _stale(action: str) -> Conflict:
    """Conflict raised when the state changed between the check and the update."""
    return Conflict(
        "invalid_status_transition",
        f"El estado de la versión cambió; no se puede {action}.",
    )

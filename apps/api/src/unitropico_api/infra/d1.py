from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from unitropico_api.core.logger import get_logger
from unitropico_api.domain.admin import (
    REPROCESSABLE,
    AdminError,
    Conflict,
    DocumentList,
    DocumentSummary,
    DocumentWithVersions,
    NewVersion,
    NotFound,
    ProcessingEvent,
    StorageUnavailable,
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
    "v.supersedes_version_id, v.created_at, v.updated_at, v.published_at"
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
}
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
        if "requires_review" in version:
            slot = slots["requires_review"]
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
                f"WHERE id = ?1 AND status <> 'published'{guard} RETURNING id",
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
        if current is not None and current.version.status != "published" and document:
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
                    "RETURNING id",
                    version_id,
                    stamp,
                ),
                self._stmt(LOG_EVENT_IF_STAMPED, version_id, "publish", stamp, None),
            ],
            check_code="publish_requirements_unmet",
        )
        return await self._after_guarded_update(
            version_id, results[0], _stale("publicar")
        )

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

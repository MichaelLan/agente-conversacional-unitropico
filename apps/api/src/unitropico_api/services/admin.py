import hashlib
import uuid
from typing import Protocol

from unitropico_api.core.logger import get_logger
from unitropico_api.domain.admin import (
    ACTIVE_WORKFLOW_STATUSES,
    DEFAULT_MAX_UPLOAD_BYTES,
    PDF_MAGIC,
    PUBLISHABLE,
    REPROCESSABLE,
    REVIEWABLE,
    Conflict,
    DocumentList,
    DocumentSummary,
    IngestionLaunchError,
    IngestionOutcome,
    IngestionRequest,
    InvalidInput,
    NewVersion,
    NotFound,
    PayloadTooLarge,
    ProcessingEvent,
    ReprocessResult,
    StorageUnavailable,
    UploadMetadata,
    UploadResult,
    VersionDetail,
    VersionPatch,
    VersionView,
    WorkflowState,
    instance_id,
    source_key,
)

_log = get_logger()

DOCUMENT_FIELDS = ("title", "document_type")
VERSION_FIELDS = (
    "version_label",
    "issued_at",
    "effective_from",
    "effective_until",
    "supersedes_version_id",
    "requires_review",
)


class DocumentRepository(Protocol):
    """Catalog port (D1): documents, versions and processing events."""

    async def get_document(self, document_id: str) -> DocumentSummary | None:
        """Return a document or None."""
        ...

    async def find_by_source_key(self, source_r2_key: str) -> VersionView | None:
        """Return the version registered for an R2 key, if any."""
        ...

    async def create_version(self, new: NewVersion) -> tuple[VersionView, bool]:
        """Insert idempotently by sha; return the version and whether it is new."""
        ...

    async def list_documents(self) -> DocumentList:
        """Return every document with its versions."""
        ...

    async def get_version(self, version_id: str) -> VersionView | None:
        """Return a version with its document, or None."""
        ...

    async def get_error_detail(self, version_id: str) -> str | None:
        """Return the stored diagnostic detail of a version."""
        ...

    async def list_events(self, version_id: str) -> list[ProcessingEvent]:
        """Return the processing log of a version in chronological order."""
        ...

    async def update_metadata(
        self, version_id: str, document: dict[str, object], version: dict[str, object]
    ) -> VersionView:
        """Apply metadata changes unless the version is published."""
        ...

    async def publish(self, version_id: str) -> VersionView:
        """Move a reviewed ready/unpublished version to published."""
        ...

    async def unpublish(self, version_id: str) -> VersionView:
        """Move a published version to unpublished."""
        ...

    async def mark_for_reprocess(self, version_id: str) -> VersionView:
        """Require a new review and log the request, unless processing."""
        ...

    async def log_event(
        self,
        version_id: str,
        step: str,
        status: str,
        error_code: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Append one processing event."""
        ...


class BlobStore(Protocol):
    """Object storage port (R2) for original PDFs."""

    async def put_pdf(self, key: str, data: bytes, sha256: str) -> bool:
        """Store the PDF unless an object of the same size exists; True if written."""
        ...


class IngestionLauncher(Protocol):
    """Workflow port that drives the ingestion of one version."""

    async def launch(self, request: IngestionRequest) -> IngestionOutcome:
        """Create the instance, reuse it if it exists, restart it if it errored."""
        ...

    async def relaunch(self, request: IngestionRequest) -> IngestionOutcome:
        """Create or restart the instance; Conflict if it is still active."""
        ...

    async def status(self, version_id: str) -> WorkflowState | None:
        """Return the instance status, or None if it does not exist."""
        ...


class AdminService:
    """Admin use cases over injected catalog, storage and Workflow ports."""

    def __init__(
        self,
        documents: DocumentRepository | None = None,
        blobs: BlobStore | None = None,
        ingestion: IngestionLauncher | None = None,
        *,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    ) -> None:
        """Receive only ports and the upload limit."""
        self._documents = documents
        self._blobs = blobs
        self._ingestion = ingestion
        self.max_upload_bytes = max_upload_bytes

    def _ports(self) -> tuple[DocumentRepository, BlobStore, IngestionLauncher]:
        """Return the ports or fail closed when composition omitted them."""
        if self._documents is None or self._blobs is None or self._ingestion is None:
            raise StorageUnavailable(
                "storage_unavailable", "El almacenamiento no está configurado."
            )
        return self._documents, self._blobs, self._ingestion

    async def upload(self, meta: UploadMetadata, data: bytes) -> UploadResult:
        """Validate, store in R2, register in D1 and launch the Workflow."""
        documents, blobs, ingestion = self._ports()
        if len(data) > self.max_upload_bytes:
            raise PayloadTooLarge(
                "payload_too_large",
                f"El archivo supera el límite de {self.max_upload_bytes} bytes.",
            )
        if not data.startswith(PDF_MAGIC):
            raise InvalidInput("invalid_pdf", "El archivo no es un PDF válido.")
        sha256 = hashlib.sha256(data).hexdigest()
        key = source_key(sha256)

        existing = await documents.find_by_source_key(key)
        if existing is not None:
            if not self._same_document(meta, existing):
                raise Conflict(
                    "source_registered_elsewhere",
                    "Este archivo ya está registrado en otro documento.",
                )
            _log.info("admin upload existing version=%s", existing.version.id)
            return UploadResult(**existing.model_dump(), created=False, ingestion=None)

        document = None
        if meta.document_id is not None:  # title/document_type are ignored here
            document = await documents.get_document(meta.document_id)
            if document is None:
                raise NotFound("document_not_found", "El documento no existe.")

        await blobs.put_pdf(key, data, sha256)
        new = NewVersion(
            document_id=meta.document_id or uuid.uuid4().hex,
            version_id=uuid.uuid4().hex,
            create_document=document is None,
            title=meta.title,
            document_type=meta.document_type.value if meta.document_type else None,
            version_label=meta.version_label,
            source_r2_key=key,
            source_sha256=sha256,
            source_bytes=len(data),
        )
        view, created = await documents.create_version(new)
        if not created:
            return UploadResult(**view.model_dump(), created=False, ingestion=None)
        _log.info("admin upload created version=%s", view.version.id)
        outcome = await self._launch(documents, ingestion, view)
        return UploadResult(**view.model_dump(), created=True, ingestion=outcome)

    @staticmethod
    def _same_document(meta: UploadMetadata, existing: VersionView) -> bool:
        """Whether an already registered PDF belongs to the requested document.

        With `document_id` the ids must match. Without it the upload targets a new
        document, so only an idempotent retry (same title and type) is accepted.
        """
        if meta.document_id is not None:
            return meta.document_id == existing.document.id
        document_type = meta.document_type.value if meta.document_type else None
        return (
            meta.title == existing.document.title
            and document_type == existing.document.document_type
        )

    async def list_documents(self) -> DocumentList:
        """List documents with their versions."""
        documents, _, _ = self._ports()
        return await documents.list_documents()

    async def detail(self, version_id: str) -> VersionDetail:
        """Return a version with its events and Workflow status."""
        documents, _, ingestion = self._ports()
        view = await self._require(documents, version_id)
        events = await documents.list_events(version_id)
        error_detail = await documents.get_error_detail(version_id)
        try:
            workflow = await ingestion.status(version_id)
        except IngestionLaunchError as error:
            _log.warning(
                "workflow status unavailable version=%s reason=%s",
                version_id,
                error.reason,
            )
            workflow = None
        return VersionDetail(
            **view.model_dump(),
            error_detail=error_detail,
            processing_events=events,
            workflow=workflow,
        )

    async def update_metadata(
        self, version_id: str, patch: VersionPatch
    ) -> VersionView:
        """Validate the merged metadata and apply it."""
        documents, _, _ = self._ports()
        view = await self._require(documents, version_id)
        current = view.version
        if current.status == "published":
            raise Conflict(
                "version_published",
                "La versión está publicada: despublíquela antes de editarla.",
            )
        changes = patch.model_dump(include=patch.model_fields_set, mode="json")
        start = changes.get("effective_from", current.effective_from)
        end = changes.get("effective_until", current.effective_until)
        if start is not None and end is not None and end < start:
            raise InvalidInput(
                "invalid_effective_range",
                "La fecha de fin de vigencia es anterior a la de inicio.",
            )
        supersedes = changes.get("supersedes_version_id")
        if supersedes is not None:
            if supersedes == version_id:
                raise InvalidInput(
                    "invalid_supersedes", "Una versión no puede sustituirse a sí misma."
                )
            await self._check_supersedes(
                documents, current.document_id, version_id, supersedes
            )
        if changes.get("requires_review") is False and current.status not in REVIEWABLE:
            raise Conflict(
                "review_not_available",
                "Solo se puede marcar como revisada una versión procesada.",
            )
        document = {k: v for k, v in changes.items() if k in DOCUMENT_FIELDS}
        version = {k: v for k, v in changes.items() if k in VERSION_FIELDS}
        result = await documents.update_metadata(version_id, document, version)
        _log.info(
            "admin metadata updated version=%s fields=%s", version_id, sorted(changes)
        )
        return result

    @staticmethod
    async def _check_supersedes(
        documents: DocumentRepository,
        document_id: str,
        version_id: str,
        target_id: str,
    ) -> None:
        """The target must exist, share the document and not close a cycle."""
        target = await documents.get_version(target_id)
        if target is None:
            raise InvalidInput("invalid_supersedes", "La versión sustituida no existe.")
        if target.version.document_id != document_id:
            raise InvalidInput(
                "supersedes_other_document",
                "La versión sustituida pertenece a otro documento.",
            )
        seen = {version_id}
        node: VersionView | None = target
        while node is not None:
            if node.version.id in seen:
                raise Conflict(
                    "supersedes_cycle",
                    "La sustitución formaría un ciclo de versiones.",
                )
            seen.add(node.version.id)
            next_id = node.version.supersedes_version_id
            node = await documents.get_version(next_id) if next_id else None

    async def publish(self, version_id: str) -> VersionView:
        """Publish a reviewed ready version."""
        documents, _, _ = self._ports()
        current = (await self._require(documents, version_id)).version
        if current.status not in PUBLISHABLE:
            raise Conflict(
                "invalid_status_transition",
                f"No se puede publicar una versión en estado {current.status}.",
            )
        if current.requires_review:
            raise Conflict(
                "review_required", "La versión debe revisarse antes de publicarse."
            )
        result = await documents.publish(version_id)
        _log.info("admin published version=%s", version_id)
        return result

    async def unpublish(self, version_id: str) -> VersionView:
        """Withdraw a published version."""
        documents, _, _ = self._ports()
        current = (await self._require(documents, version_id)).version
        if current.status != "published":
            raise Conflict(
                "invalid_status_transition",
                f"No se puede despublicar una versión en estado {current.status}.",
            )
        result = await documents.unpublish(version_id)
        _log.info("admin unpublished version=%s", version_id)
        return result

    async def reprocess(self, version_id: str) -> ReprocessResult:
        """Create or restart the Workflow instance of a settled version."""
        documents, _, ingestion = self._ports()
        current = (await self._require(documents, version_id)).version
        if current.status not in REPROCESSABLE:
            raise Conflict(
                "invalid_status_transition",
                f"No se puede reprocesar una versión en estado {current.status}.",
            )
        # Order: (1) read the instance state, so an active run answers 409 with the
        # catalog untouched; (2) mark requires_review + log the request; (3) relaunch.
        # If (3) loses a race (Conflict) or fails, an error event is recorded.
        try:
            state = await ingestion.status(version_id)
        except IngestionLaunchError:
            state = None  # unknown: let relaunch decide
        if state is not None and state.status in ACTIVE_WORKFLOW_STATUSES:
            raise Conflict("ingestion_in_progress", "La versión ya se está procesando.")
        view = await documents.mark_for_reprocess(version_id)
        try:
            outcome = await self._launch(documents, ingestion, view, restart=True)
        except Conflict:
            await documents.log_event(
                version_id,
                "upload",
                "failed",
                "ingestion_in_progress",
                "workflow_active_after_mark",
            )
            raise
        if outcome.outcome == "failed":
            raise StorageUnavailable(
                "workflow_launch_failed",
                "No se pudo iniciar el procesamiento; inténtelo de nuevo.",
            )
        return ReprocessResult(**view.model_dump(), ingestion=outcome)

    async def _launch(
        self,
        documents: DocumentRepository,
        ingestion: IngestionLauncher,
        view: VersionView,
        *,
        restart: bool = False,
    ) -> IngestionOutcome:
        """Launch the Workflow and log the outcome; a failure never raises."""
        version = view.version
        request = IngestionRequest(
            version_id=version.id,
            document_id=version.document_id,
            source_r2_key=source_key(version.source_sha256),
            source_sha256=version.source_sha256,
        )
        try:
            if restart:
                outcome = await ingestion.relaunch(request)
            else:
                outcome = await ingestion.launch(request)
        except IngestionLaunchError as error:
            _log.warning(
                "workflow launch failed version=%s reason=%s", version.id, error.reason
            )
            await documents.log_event(
                version.id, "upload", "failed", "workflow_launch_failed", error.reason
            )
            return IngestionOutcome(
                instance_id=instance_id(version.id), outcome="failed"
            )
        await documents.log_event(
            version.id, "upload", "succeeded", None, f"workflow_{outcome.outcome}"
        )
        return outcome

    @staticmethod
    async def _require(documents: DocumentRepository, version_id: str) -> VersionView:
        """Return the version or raise NotFound."""
        view = await documents.get_version(version_id)
        if view is None:
            raise NotFound("version_not_found", "La versión no existe.")
        return view

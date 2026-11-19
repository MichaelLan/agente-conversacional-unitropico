"""Admin catalog contract v1 (docs/IMPLEMENTATION_STATUS.md, section 4b)."""

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    StringConstraints,
    model_validator,
)

PDF_MAGIC = b"%PDF-"
DEFAULT_MAX_UPLOAD_BYTES = 25 * 1024 * 1024

VersionStatus = Literal[
    "uploaded",
    "processing",
    "extracted",
    "review_required",
    "ready",
    "published",
    "unpublished",
    "failed",
]
REPROCESSABLE: frozenset[str] = frozenset(
    {
        "uploaded",
        "processing",
        "extracted",
        "failed",
        "review_required",
        "ready",
        "unpublished",
    }
)
# Workflow instance states in which a new run must not be started.
ACTIVE_WORKFLOW_STATUSES: frozenset[str] = frozenset(
    {"queued", "running", "waiting", "paused", "waitingForPause"}
)
PUBLISHABLE: frozenset[str] = frozenset({"ready", "unpublished"})
REVIEWABLE: frozenset[str] = frozenset({"review_required", "ready", "unpublished"})


def source_key(sha256: str) -> str:
    """Deterministic R2 key of an original PDF."""
    return f"raw/{sha256}.pdf"


def instance_id(version_id: str) -> str:
    """Workflow instance id `ver_{version_id}`, limited to [A-Za-z0-9_-]{1,100}."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", version_id)
    candidate = f"ver_{safe}"
    if safe == version_id and len(candidate) <= 100:
        return candidate
    # Disambiguate ids that were rewritten or truncated.
    digest = hashlib.sha256(version_id.encode()).hexdigest()[:16]
    return f"ver_{safe[:79]}_{digest}"


class DocumentType(StrEnum):
    """Controlled document categories of the institutional corpus."""

    RESOLUCION = "resolucion"
    REGLAMENTO = "reglamento"
    MANUAL = "manual"
    CONVOCATORIA = "convocatoria"
    POLITICA = "politica"
    DOCUMENTO_TECNICO = "documento_tecnico"
    BOLETIN = "boletin"
    OTRO = "otro"


def _calendar_date(value: str) -> str:
    """Accept only real calendar dates written as YYYY-MM-DD."""
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError("fecha inexistente") from None
    return value


Title = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
VersionLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
]
EntityId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
CalendarDate = Annotated[
    str,
    StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$"),
    AfterValidator(_calendar_date),
]


class UploadMetadata(BaseModel):
    """Query parameters of POST /admin/uploads."""

    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    document_type: DocumentType | None = None
    version_label: VersionLabel
    document_id: EntityId | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        """Require title and document_type unless an existing document is given."""
        if self.document_id is None and (
            self.title is None or self.document_type is None
        ):
            raise ValueError("title y document_type son obligatorios sin document_id")
        return self


class VersionPatch(BaseModel):
    """Editable metadata; omitted fields stay unchanged, null clears a value."""

    model_config = ConfigDict(extra="forbid", strict=True)

    title: Title | None = None
    document_type: DocumentType | None = None
    version_label: VersionLabel | None = None
    issued_at: CalendarDate | None = None
    effective_from: CalendarDate | None = None
    effective_until: CalendarDate | None = None
    supersedes_version_id: EntityId | None = None
    requires_review: bool | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        """Require at least one field and reject nulls on mandatory columns."""
        if not self.model_fields_set:
            raise ValueError("debe indicar al menos un campo")
        for name in ("title", "document_type", "version_label", "requires_review"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} no admite null")
        start, end = self.effective_from, self.effective_until
        if start is not None and end is not None and end < start:
            raise ValueError("effective_until debe ser >= effective_from")
        return self


class DocumentSummary(BaseModel):
    """Logical document fields shown in the admin panel."""

    id: str
    title: str
    document_type: str
    created_at: str


class VersionSummary(BaseModel):
    """One physical version with its processing state and validity metadata."""

    id: str
    document_id: str
    version_label: str | None
    status: VersionStatus
    requires_review: bool
    error_code: str | None
    processing_attempts: int
    source_sha256: str
    source_bytes: int
    page_count: int | None
    issued_at: str | None
    effective_from: str | None
    effective_until: str | None
    supersedes_version_id: str | None
    created_at: str
    updated_at: str
    published_at: str | None


class VersionView(BaseModel):
    """A version together with its document."""

    document: DocumentSummary
    version: VersionSummary


class DocumentWithVersions(DocumentSummary):
    """A document and all of its versions, newest first."""

    versions: list[VersionSummary]


class DocumentList(BaseModel):
    """Response of GET /admin/documents."""

    documents: list[DocumentWithVersions]


class ProcessingEvent(BaseModel):
    """Append-only processing log entry."""

    id: int
    step: str
    status: str
    error_code: str | None
    detail: str | None
    created_at: str


class WorkflowState(BaseModel):
    """Status of the ingestion Workflow instance of a version."""

    instance_id: str
    status: str
    error_name: str | None = None
    error_message: str | None = None


class IngestionOutcome(BaseModel):
    """What happened when the API asked the Workflow to process a version."""

    instance_id: str
    outcome: Literal["created", "existing", "restarted", "failed"]
    status: str | None = None


class VersionDetail(VersionView):
    """Response of GET /admin/versions/{id}."""

    error_detail: str | None
    processing_events: list[ProcessingEvent]
    workflow: WorkflowState | None


class UploadResult(VersionView):
    """Response of POST /admin/uploads."""

    created: bool
    ingestion: IngestionOutcome | None


class ReprocessResult(VersionView):
    """Response of POST /admin/versions/{id}/reprocess."""

    ingestion: IngestionOutcome


@dataclass(frozen=True, slots=True)
class NewVersion:
    """Validated upload ready to be registered in the catalog."""

    document_id: str
    version_id: str
    create_document: bool
    title: str | None
    document_type: str | None
    version_label: str
    source_r2_key: str
    source_sha256: str
    source_bytes: int


@dataclass(frozen=True, slots=True)
class IngestionRequest:
    """Parameters handed to the ingestion Workflow (`event.payload`)."""

    version_id: str
    document_id: str
    source_r2_key: str
    source_sha256: str

    def as_params(self) -> dict[str, str]:
        """Serialize as the Workflow params object."""
        return {
            "version_id": self.version_id,
            "document_id": self.document_id,
            "source_r2_key": self.source_r2_key,
            "source_sha256": self.source_sha256,
        }


class AdminError(Exception):
    """Domain error with a stable code and a Spanish, content-free message."""

    def __init__(self, code: str, message: str) -> None:
        """Keep the machine code and the human message."""
        super().__init__(code)
        self.code = code
        self.message = message


class Unauthorized(AdminError):
    """Missing or invalid admin credentials."""


class AdminDisabled(AdminError):
    """No admin token is configured; admin routes stay closed."""


class NotFound(AdminError):
    """Unknown document or version."""


class Conflict(AdminError):
    """The current state or the schema forbids the operation."""


class InvalidInput(AdminError):
    """Semantically invalid request."""


class PayloadTooLarge(AdminError):
    """Upload exceeds the configured size limit."""


class UnsupportedMedia(AdminError):
    """Upload is not declared as application/pdf."""


class StorageUnavailable(AdminError):
    """A storage or Workflow binding is missing or failing."""


class IngestionLaunchError(Exception):
    """The Workflow binding failed; carries only the error type name."""

    def __init__(self, reason: str) -> None:
        """Keep a content-free reason."""
        super().__init__(reason)
        self.reason = reason

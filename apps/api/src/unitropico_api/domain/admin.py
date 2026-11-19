"""Admin catalog contract v1 (docs/IMPLEMENTATION_STATUS.md, section 4b)."""

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

PDF_MAGIC = b"%PDF-"
DEFAULT_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_CHUNKS_PAGE = 100
DEFAULT_CHUNKS_PAGE = 50
MAX_ACTIVE_CORRECTIONS = 100
"""Active corrections per version (each one is one event in the chunk step)."""
MAX_FIND_TEXT = 500
MAX_REPLACE_TEXT = 500
MAX_REASON = 300

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


class ChunksQuery(BaseModel):
    """Query parameters of GET /admin/versions/{id}/chunks."""

    model_config = ConfigDict(extra="forbid")

    offset: Annotated[int, Field(ge=0, le=1_000_000)] = 0
    limit: Annotated[int, Field(ge=1, le=MAX_CHUNKS_PAGE)] = DEFAULT_CHUNKS_PAGE


def _no_control_chars(value: str) -> str:
    """Reject control characters other than tab and line breaks."""
    if any(unicodedata.category(ch) == "Cc" and ch not in "\t\n\r" for ch in value):
        raise ValueError("no se admiten caracteres de control")
    return value


FindText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_FIND_TEXT),
    AfterValidator(_no_control_chars),
]
ReplaceText = Annotated[
    str,
    StringConstraints(max_length=MAX_REPLACE_TEXT),
    AfterValidator(_no_control_chars),
]
Reason = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_REASON),
    AfterValidator(_no_control_chars),
]


class CorrectionCreate(BaseModel):
    """Body of POST /admin/versions/{id}/corrections (literal OCR correction)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    page_number: Annotated[int, Field(ge=1)]
    find_text: FindText
    replace_text: ReplaceText
    reason: Reason

    @model_validator(mode="after")
    def _check(self) -> Self:
        """A correction must change the text."""
        if self.find_text == self.replace_text:
            raise ValueError("replace_text debe ser distinto de find_text")
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


class ChunkReview(BaseModel):
    """One stored chunk, as shown to the administrator for review."""

    ordinal: int
    id: str
    section: str | None
    page_start: int
    page_end: int
    text: str
    chunker_version: str | None


class ChunkPage(BaseModel):
    """Response of GET /admin/versions/{id}/chunks."""

    version_id: str
    status: VersionStatus
    total: int
    offset: int
    limit: int
    chunks: list[ChunkReview]


class TextCorrectionView(BaseModel):
    """A recorded correction; deleted ones are kept for traceability."""

    id: str
    version_id: str
    page_number: int
    find_text: str
    replace_text: str
    reason: str
    created_at: str
    deleted_at: str | None
    active: bool


class CorrectionList(BaseModel):
    """Response of GET /admin/versions/{id}/corrections."""

    version_id: str
    corrections: list[TextCorrectionView]


REPROCESS_NOTICE = (
    "El cambio se aplicará cuando se reprocese la versión "
    "(POST /admin/versions/{id}/reprocess); no se reprocesa automáticamente. "
    "La versión vuelve a requerir revisión."
)


class CorrectionResult(BaseModel):
    """Response of POST/DELETE on corrections: never triggers a reprocess."""

    correction: TextCorrectionView
    version_status: VersionStatus
    requires_review: bool
    reprocess_required: bool = True
    message: str = REPROCESS_NOTICE


@dataclass(frozen=True, slots=True)
class NewCorrection:
    """Validated correction ready to be stored."""

    id: str
    version_id: str
    page_number: int
    find_text: str
    replace_text: str
    reason: str


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

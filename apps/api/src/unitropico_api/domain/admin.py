"""Admin catalog contract v1 (docs/IMPLEMENTATION_STATUS.md, sections 4 and 4c)."""

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
# (code, message) of the correction conflicts raised by the service and the adapter.
CORRECTIONS_PENDING = (
    "corrections_pending",
    "Hay correcciones creadas o borradas que aún no se han aplicado: reprocese "
    "la versión antes de marcarla como revisada o publicarla.",
)
TOO_MANY_CORRECTIONS = (
    "too_many_corrections",
    f"La versión ya tiene {MAX_ACTIVE_CORRECTIONS} correcciones activas.",
)
DUPLICATE_CORRECTION = (
    "duplicate_correction",
    "Ya existe una corrección activa para ese texto en esa página.",
)
MAX_FIND_TEXT = 500
MAX_REPLACE_TEXT = 500
MAX_REASON = 300
MAX_OVERRIDE_TEXT = 20000
"""Characters (code points) of a page override, after trimming both ends."""
OVERRIDE_EXISTS = (
    "override_exists",
    "Ya existe una sustitución activa para esa página: bórrela primero.",
)
PAGE_TEXT_UNAVAILABLE = (
    "page_text_unavailable",
    "El texto de las páginas no está disponible: reprocese la versión para generarlo.",
)

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
    """Reject control (Cc) and format (Cf) characters except tab and line breaks.

    Cf covers bidirectional overrides and isolates (U+202A-U+202E,
    U+2066-U+2069), zero-width characters, the soft hyphen and the BOM: they
    are invisible in the admin panel, so they could make a correction look
    different from what it does. ZWJ/ZWNJ (U+200D/U+200C) are rejected too:
    Spanish text never needs them and the ingestion normalization removes them
    from the page text, so a find_text containing them could never be what the
    administrator saw.
    """
    for ch in value:
        category = unicodedata.category(ch)
        if (category == "Cc" and ch not in "\t\n\r") or category == "Cf":
            raise ValueError("no se admiten caracteres de control ni de formato")
    return value


def _no_control_chars_strict(value: str) -> str:
    """Like ``_no_control_chars`` but only tab and line feed are allowed (4c)."""
    for ch in value:
        category = unicodedata.category(ch)
        if (category == "Cc" and ch not in "\t\n") or category == "Cf":
            raise ValueError("no se admiten caracteres de control ni de formato")
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


MATCH_RULE_HELP = (
    "find_text se busca en el texto preparado de la página física indicada, el "
    "mismo que se muestra en los fragmentos: texto normalizado (espacios "
    "colapsados, caracteres invisibles eliminados), sin los encabezados y pies "
    "de página repetidos (solo queda su primera aparición en el documento) y con "
    "las tablas convertidas a filas Markdown (| celda | celda |), con las "
    "entidades HTML decodificadas y sin etiquetas. Copie el texto desde un "
    "fragmento de esa página. La comparación es literal (mayúsculas, tildes y "
    "puntuación cuentan); cualquier secuencia de espacios o saltos de línea "
    "equivale a otra. Debe aparecer exactamente una vez en la página (las "
    "apariciones solapadas cuentan) o la versión falla con "
    "correction_not_applicable. Las correcciones de una página se aplican en "
    "orden de creación, cada una sobre el resultado de las anteriores. Un "
    "fragmento que une dos páginas solo se corrige página a página."
)


class CorrectionCreate(BaseModel):
    """Body of POST /admin/versions/{id}/corrections (literal OCR correction)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    page_number: Annotated[int, Field(ge=1)]
    find_text: Annotated[FindText, Field(description=MATCH_RULE_HELP)]
    replace_text: ReplaceText
    reason: Reason

    @model_validator(mode="after")
    def _check(self) -> Self:
        """A correction must change the text."""
        if self.find_text == self.replace_text:
            raise ValueError("replace_text debe ser distinto de find_text")
        return self


OverrideText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=MAX_OVERRIDE_TEXT
    ),
    AfterValidator(_no_control_chars_strict),
]
OverrideReason = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_REASON),
    AfterValidator(_no_control_chars_strict),
]

OVERRIDE_RULE_HELP = (
    "El texto sustituye por completo el texto de la página física indicada en "
    "el próximo reprocesamiento, antes de aplicar las correcciones literales de "
    "esa página (que entonces se buscan en este texto). Se prepara igual que el "
    "texto del OCR de una página: espacios colapsados, caracteres invisibles "
    "eliminados y tablas HTML convertidas a filas Markdown; los encabezados y "
    "pies repetidos NO se eliminan. Máximo una sustitución activa por página."
)


class OverrideCreate(BaseModel):
    """Body of POST /admin/versions/{id}/page-overrides (full page text)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    page_number: Annotated[int, Field(ge=1)]
    text: Annotated[OverrideText, Field(description=OVERRIDE_RULE_HELP)]
    reason: OverrideReason


class PagesQuery(BaseModel):
    """Query parameters of GET /admin/versions/{id}/pages."""

    model_config = ConfigDict(extra="forbid")

    page: Annotated[int, Field(ge=1, le=100_000)]


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
    corrections_dirty: bool
    """Corrections or page overrides changed after the last applied chunking.

    While true the version cannot be marked reviewed nor published (409
    ``corrections_pending``): reprocess it so the ingestion applies them.
    """


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
    match_rule: str = MATCH_RULE_HELP


class PageOverrideView(BaseModel):
    """A recorded page override; deleted ones are kept for traceability."""

    id: str
    version_id: str
    page_number: int
    text: str
    reason: str
    created_at: str
    deleted_at: str | None
    active: bool


class OverrideList(BaseModel):
    """Response of GET /admin/versions/{id}/page-overrides."""

    version_id: str
    overrides: list[PageOverrideView]


class PageTextView(BaseModel):
    """Response of GET /admin/versions/{id}/pages?page=N.

    Final prepared text of one page as the last chunk step produced it (read
    from ``pages/{version_id}/{chunker_version}.json`` in R2).
    """

    version_id: str
    page_number: int
    page_count: int
    text: str
    source: Literal["ocr", "override"]
    override_id: str | None
    corrections_applied: list[str]
    chunker_version: str


REPROCESS_NOTICE = (
    "El cambio se aplicará cuando se reprocese la versión "
    "(POST /admin/versions/{id}/reprocess); no se reprocesa automáticamente. "
    "La versión vuelve a requerir revisión y no podrá marcarse como revisada ni "
    "publicarse hasta que el reprocesamiento aplique las correcciones."
)


OVERRIDE_NOTICE = (
    "El cambio se aplicará cuando se reprocese la versión "
    "(POST /admin/versions/{id}/reprocess); no se reprocesa automáticamente. "
    "La versión vuelve a requerir revisión y no podrá marcarse como revisada ni "
    "publicarse hasta que el reprocesamiento aplique la sustitución de página."
)


class CorrectionResult(BaseModel):
    """Response of POST/DELETE on corrections: never triggers a reprocess."""

    correction: TextCorrectionView
    version_status: VersionStatus
    requires_review: bool
    corrections_dirty: bool
    reprocess_required: bool = True
    message: str = REPROCESS_NOTICE


class OverrideResult(BaseModel):
    """Response of POST/DELETE on page overrides: never triggers a reprocess."""

    override: PageOverrideView
    version_status: VersionStatus
    requires_review: bool
    corrections_dirty: bool
    reprocess_required: bool = True
    message: str = OVERRIDE_NOTICE


@dataclass(frozen=True, slots=True)
class NewOverride:
    """Validated page override ready to be stored."""

    id: str
    version_id: str
    page_number: int
    text: str
    reason: str


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

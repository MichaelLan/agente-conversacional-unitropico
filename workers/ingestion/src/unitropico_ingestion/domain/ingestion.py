import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

MAX_ID_BYTES = 64
"""D1 ids are TEXT of 1..64 UTF-8 bytes (see infra/d1/migrations/0001_initial.sql)."""

MAX_ERROR_DETAIL = 500
"""error_detail is stored in D1 and shown to the administrator: keep it short."""


class ErrorCode(StrEnum):
    """Stable error codes stored in ``document_versions.error_code``."""

    INVALID_SOURCE = "invalid_source"
    INVALID_STATE = "invalid_state"
    CONFIG_MISSING = "config_missing"
    OCR_FAILED = "ocr_failed"
    OCR_EMPTY = "ocr_empty"
    CHUNK_FAILED = "chunk_failed"
    CORRECTION_NOT_APPLICABLE = "correction_not_applicable"
    EMBEDDING_FAILED = "embedding_failed"
    INDEX_FAILED = "index_failed"
    INTERNAL_ERROR = "internal_error"


class IngestionError(Exception):
    """Typed ingestion failure with a stable code and a sanitized detail.

    ``permanent`` errors must not be retried (the Workflow raises
    ``NonRetryableError``); the others are retried by the step configuration.
    ``detail`` is built by this package from safe fragments (status codes,
    counts, exception type names), never from provider payloads or document
    text.
    """

    def __init__(self, code: ErrorCode, detail: str = "", *, permanent: bool = False):
        """Store the classification; the message is only the code."""
        super().__init__(str(code))
        self.code = ErrorCode(code)
        self.detail = detail
        self.permanent = permanent


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class IngestionParams:
    """Workflow instance params as created by the API (instance ``ver_{id}``).

    Payload: ``{"version_id", "document_id", "source_r2_key", "source_sha256"}``.
    Only ``version_id`` is required; the other ids, when present, are checked
    against D1 (the source of truth) before any paid call.
    """

    version_id: str
    document_id: str | None = None
    source_r2_key: str | None = None
    source_sha256: str | None = None

    @classmethod
    def from_payload(cls, payload: object) -> IngestionParams:
        """Validate the event payload."""
        if not isinstance(payload, Mapping):
            payload = {}
        version_id = payload.get("version_id")
        document_id = payload.get("document_id")
        key = payload.get("source_r2_key")
        digest = payload.get("source_sha256")
        if (
            not is_valid_id(version_id)
            or (document_id is not None and not is_valid_id(document_id))
            or (key is not None and (not isinstance(key, str) or not key.strip()))
            or (
                digest is not None
                and (not isinstance(digest, str) or not _SHA256.match(digest))
            )
        ):
            raise IngestionError(
                ErrorCode.INVALID_SOURCE, "invalid_params", permanent=True
            )
        return cls(version_id, document_id, key, digest)  # type: ignore[arg-type]

    def mismatches(self, version: VersionRecord) -> list[str]:
        """Return the names of payload ids that disagree with the D1 row."""
        expected = {
            "document_id": (self.document_id, version.document_id),
            "source_r2_key": (self.source_r2_key, version.source_r2_key),
            "source_sha256": (self.source_sha256, version.source_sha256),
        }
        return [k for k, (given, real) in expected.items() if given not in (None, real)]


@dataclass(frozen=True)
class VersionRecord:
    """The subset of ``document_versions`` the Workflow needs."""

    id: str
    document_id: str
    status: str
    source_r2_key: str
    source_sha256: str
    requires_review: bool


@dataclass(frozen=True)
class OcrPage:
    """Markdown of one physical PDF page (1-based) as returned by the OCR."""

    page_number: int
    markdown: str

    def __post_init__(self) -> None:
        """Reject invalid page numbers and non-text content."""
        if type(self.page_number) is not int or self.page_number < 1:
            raise ValueError("page_number must be a positive int")
        if not isinstance(self.markdown, str):
            raise TypeError("markdown must be a str")


@dataclass(frozen=True)
class OcrResult:
    """Parsed OCR output: ordered pages, physical page count and failed pages."""

    pages: tuple[OcrPage, ...]
    page_count: int
    failed_pages: tuple[int, ...] = ()

    @property
    def text_pages(self) -> int:
        """Return how many pages carry visible text."""
        return sum(bool(page.markdown.strip()) for page in self.pages)


def is_valid_id(value: object) -> bool:
    """Return True for a D1-compatible id (1..64 bytes, no surrounding spaces)."""
    return (
        isinstance(value, str)
        and value == value.strip()
        and 1 <= len(value.encode("utf-8")) <= MAX_ID_BYTES
        and "/" not in value
        and all(ch.isprintable() for ch in value)
    )


_UNSAFE_DETAIL = re.compile(r"[^A-Za-z0-9 _=:.,/+-]")


def sanitize_detail(detail: str, secrets: tuple[str, ...] = ()) -> str:
    """Redact secrets, drop unexpected characters and truncate.

    Defense in depth: details are already built from safe fragments, but a
    secret value that slipped in is replaced before anything is persisted.
    """
    text = detail if isinstance(detail, str) else type(detail).__name__
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    text = _UNSAFE_DETAIL.sub("", text.replace("[redacted]", "redacted"))
    return " ".join(text.split())[:MAX_ERROR_DETAIL]

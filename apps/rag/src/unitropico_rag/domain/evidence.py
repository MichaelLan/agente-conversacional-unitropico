from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_IDENTITY_FIELDS = (
    "document_id",
    "version_id",
    "chunk_id",
    "source_sha256",
    "page_start",
    "page_end",
    "section",
)


class RetrievalQuery(BaseModel):
    """Accept only a trimmed question (1–500 characters) and a limit (1–20)."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    question: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)

    @field_validator("question", mode="before")
    @classmethod
    def trim_question(cls, value: object) -> object:
        """Normalize whitespace before checking the question length."""
        return value.strip() if isinstance(value, str) else value


@dataclass(frozen=True)
class EvidenceIdentity:
    """Identify a source chunk and its catalog-attested physical PDF page range."""

    document_id: str
    version_id: str
    chunk_id: str
    source_sha256: str
    page_start: int
    page_end: int
    section: str | None


@dataclass(frozen=True)
class ChunkCandidate(EvidenceIdentity):
    """Carry exact retrieved text, not proof of semantic grounding or consistency."""

    text: str


@dataclass(frozen=True)
class RegistryEntry(EvidenceIdentity):
    """Attest current chunk provenance, exact UTF-8 digest and publication state.

    Adapters must read authoritative current metadata, not stale index metadata.
    Formal replacement is explicit; upload order never establishes precedence.
    Validity fields are descriptive metadata for the prompt; no policy is inferred.
    """

    text_sha256: str
    status: str
    requires_review: bool
    formally_superseded: bool
    document_title: str
    version_label: str | None = None
    validity_label: str | None = None
    effective_from: date | None = None
    effective_until: date | None = None


@dataclass(frozen=True)
class Evidence(ChunkCandidate):
    """Admissible chunk enriched with catalog metadata, never with model output."""

    document_title: str
    version_label: str | None
    validity_label: str | None
    effective_from: date | None
    effective_until: date | None


@dataclass(frozen=True)
class RankedChunk:
    """One channel hit: a chunk id and the channel's own score (rank is position).

    Scores are channel-specific (bm25: lower is better; cosine: higher is better)
    and must never be compared across channels; fusion uses only ranks.
    """

    chunk_id: str
    score: float


@dataclass(frozen=True)
class RetrievalResult:
    """Return admissible evidence only, never an LLM answer or sufficiency claim.

    ``relevance`` is the best semantic-channel (cosine) score among the
    returned evidence, or None when no returned item carries one (lexical-only
    mode, failed semantic channel, or a retriever without fusion trace). It is
    internal: the HTTP contracts do not expose it.
    """

    status: Literal["unavailable", "abstained", "evidence_found"]
    evidence: tuple[Evidence, ...] = ()
    relevance: float | None = None


def _nonblank(value: object) -> bool:
    """Return whether value is a string with visible content."""
    return isinstance(value, str) and bool(value.strip())


def _optional_text(value: object) -> bool:
    """Accept None or a nonblank string."""
    return value is None or _nonblank(value)


def _optional_date(value: object) -> bool:
    """Accept None or a plain date."""
    return value is None or type(value) is date


def is_admissible(candidate: ChunkCandidate, entry: RegistryEntry | None) -> bool:
    """Require exact provenance, valid page range and current eligibility."""
    if not isinstance(candidate, ChunkCandidate) or not isinstance(
        entry, RegistryEntry
    ):
        return False
    if any(getattr(candidate, n) != getattr(entry, n) for n in _IDENTITY_FIELDS):
        return False
    return (
        entry.status == "published"
        and entry.requires_review is False
        and entry.formally_superseded is False
        and all(
            _nonblank(value)
            for value in (
                candidate.document_id,
                candidate.version_id,
                candidate.chunk_id,
                entry.document_title,
            )
        )
        and _optional_text(entry.version_label)
        and _optional_text(entry.validity_label)
        and _optional_date(entry.effective_from)
        and _optional_date(entry.effective_until)
        and isinstance(candidate.source_sha256, str)
        and len(candidate.source_sha256) == 64
        and all(char in "0123456789abcdef" for char in candidate.source_sha256)
        and type(candidate.page_start) is int
        and type(candidate.page_end) is int
        and type(entry.page_start) is int
        and type(entry.page_end) is int
        and 0 < candidate.page_start <= candidate.page_end
        and _optional_text(candidate.section)
        and _nonblank(candidate.text)
        and sha256(candidate.text.encode("utf-8")).hexdigest() == entry.text_sha256
    )


def to_evidence(candidate: ChunkCandidate, entry: RegistryEntry) -> Evidence:
    """Merge an admissible chunk with authoritative catalog presentation metadata."""
    return Evidence(
        document_id=candidate.document_id,
        version_id=candidate.version_id,
        chunk_id=candidate.chunk_id,
        source_sha256=candidate.source_sha256,
        page_start=candidate.page_start,
        page_end=candidate.page_end,
        section=candidate.section,
        text=candidate.text,
        document_title=entry.document_title,
        version_label=entry.version_label,
        validity_label=entry.validity_label,
        effective_from=entry.effective_from,
        effective_until=entry.effective_until,
    )

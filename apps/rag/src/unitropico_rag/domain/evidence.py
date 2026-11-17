from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    """Identify a source chunk and its catalog-attested physical PDF page."""

    document_id: str
    version_id: str
    chunk_id: str
    source_sha256: str
    page: int
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
    No temporal validity policy is inferred from this minimal contract.
    """

    text_sha256: str
    status: str
    requires_review: bool
    formally_superseded: bool


@dataclass(frozen=True)
class RetrievalResult:
    """Return admissible evidence only, never an LLM answer or sufficiency claim."""

    status: Literal["unavailable", "abstained", "evidence_found"]
    evidence: tuple[ChunkCandidate, ...] = ()

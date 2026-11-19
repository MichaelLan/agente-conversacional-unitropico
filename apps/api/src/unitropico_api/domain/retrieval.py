from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RetrievalQuery(BaseModel):
    """Accept only a trimmed question and a bounded retrieval limit."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    question: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)

    @field_validator("question", mode="before")
    @classmethod
    def trim_question(cls, value: object) -> object:
        """Trim before enforcing the question length, without coercing types."""
        return value.strip() if isinstance(value, str) else value


class EvidenceResponse(BaseModel):
    """Preserve exact source identity/text and physical PDF pages, never URLs."""

    model_config = ConfigDict(strict=True, extra="forbid")
    document_id: str
    document_title: str
    version_id: str
    version_label: str | None
    chunk_id: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    section: str | None
    text: str

    @field_validator("document_id", "document_title", "version_id", "chunk_id", "text")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        """Reject blank values without altering valid evidence."""
        if not value.strip():
            raise ValueError("Evidence identity and text must not be blank")
        return value

    @model_validator(mode="after")
    def require_page_range(self) -> Self:
        """Require a non-inverted physical page range."""
        if self.page_end < self.page_start:
            raise ValueError("page_end must not precede page_start")
        return self


class RetrievalResponse(BaseModel):
    """Describe retrieval evidence, not an answer or evidence sufficiency."""

    model_config = ConfigDict(
        strict=True, extra="forbid", revalidate_instances="always"
    )
    status: Literal["unavailable", "abstained", "evidence_found"]
    evidence: list[EvidenceResponse]

    @model_validator(mode="after")
    def require_coherence(self) -> Self:
        """Require nonempty evidence exactly when evidence was found."""
        if (self.status == "evidence_found") != bool(self.evidence):
            raise ValueError("Status and evidence must agree")
        return self

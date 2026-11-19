from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AbstainReason = Literal[
    "no_evidence",
    "model_abstained",
    "invalid_output",
    "invalid_citation",
    "stale_source",
]


class ChatMessage(BaseModel):
    """One prior conversation turn supplied by the client; never persisted."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    """Accept a trimmed question and at most three turns of history."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    question: str = Field(min_length=1, max_length=500)
    history: list[ChatMessage] = Field(default_factory=list, max_length=6)

    @field_validator("question", mode="before")
    @classmethod
    def trim_question(cls, value: object) -> object:
        """Trim before enforcing the length, without coercing types."""
        return value.strip() if isinstance(value, str) else value


class Citation(BaseModel):
    """Catalog-derived source reference; never produced by the model."""

    model_config = ConfigDict(strict=True, extra="forbid")
    chunk_id: str
    document_id: str
    document_title: str
    version_id: str
    version_label: str | None
    section: str | None
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    quote: str

    @field_validator("chunk_id", "document_id", "document_title", "version_id", "quote")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        """Reject blank identity or quote values."""
        if not value.strip():
            raise ValueError("Citation identity and quote must not be blank")
        return value

    @model_validator(mode="after")
    def require_page_range(self) -> Self:
        """Require a non-inverted physical page range."""
        if self.page_end < self.page_start:
            raise ValueError("page_end must not precede page_start")
        return self


class ChatResponse(BaseModel):
    """Answered or abstained result with the v1 status invariants enforced."""

    model_config = ConfigDict(strict=True, extra="forbid")
    status: Literal["answered", "abstained"]
    answer: str | None
    reason: AbstainReason | None
    citations: list[Citation]
    referral: str | None

    @model_validator(mode="after")
    def require_coherence(self) -> Self:
        """Enforce which fields each status requires or forbids."""
        if self.status == "answered":
            valid = (
                self.answer is not None
                and bool(self.answer.strip())
                and bool(self.citations)
                and self.reason is None
            )
        else:
            valid = (
                self.answer is None
                and not self.citations
                and self.reason is not None
                and self.referral is not None
                and bool(self.referral.strip())
            )
        if not valid:
            raise ValueError("Status and fields must agree")
        return self

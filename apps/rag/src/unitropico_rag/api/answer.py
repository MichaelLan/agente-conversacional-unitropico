from typing import Literal, Protocol

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from unitropico_rag.domain.answer import (
    AbstentionReason,
    AnswerQuery,
    AnswerResult,
    ConversationTurn,
)


class AnswerPort(Protocol):
    """Answer one validated query."""

    async def query(self, query: AnswerQuery) -> AnswerResult:
        """Return answered, abstained or unavailable."""
        ...


class TurnRequest(BaseModel):
    """One prior conversation turn; privileged roles are not accepted."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)

    @field_validator("content")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        """Reject whitespace-only turns."""
        if not value.strip():
            raise ValueError("content must not be whitespace-only")
        return value


class AnswerRequest(BaseModel):
    """Contract v1 request: trimmed question (1–500) and up to 6 history turns."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    question: str = Field(min_length=1, max_length=500)
    history: list[TurnRequest] = Field(default_factory=list, max_length=6)

    @field_validator("question", mode="before")
    @classmethod
    def trim_question(cls, value: object) -> object:
        """Strip surrounding whitespace before checking length."""
        return value.strip() if isinstance(value, str) else value


class CitationResponse(BaseModel):
    """Catalog-attested citation; only the verified quote originates in the model."""

    model_config = ConfigDict(strict=True, extra="forbid")
    chunk_id: str
    document_id: str
    document_title: str
    version_id: str
    version_label: str | None
    section: str | None
    page_start: int
    page_end: int
    quote: str


class AnswerResponse(BaseModel):
    """Contract v1 200 body: answered or abstained."""

    model_config = ConfigDict(strict=True, extra="forbid")
    status: Literal["answered", "abstained"]
    answer: str | None
    reason: AbstentionReason | None
    citations: list[CitationResponse]
    referral: str | None


class UnavailableResponse(BaseModel):
    """Contract v1 503 body."""

    model_config = ConfigDict(strict=True, extra="forbid")
    status: Literal["unavailable"] = "unavailable"
    referral: str


def create_answer_router(service: AnswerPort) -> APIRouter:
    """Bind POST /answer to an explicitly composed answer service."""
    router = APIRouter()

    @router.post(
        "/answer",
        response_model=AnswerResponse,
        responses={503: {"model": UnavailableResponse, "description": "Unavailable"}},
    )
    async def answer(payload: AnswerRequest) -> AnswerResponse | JSONResponse:
        """Answer with citations (200), abstain with a reason (200) or 503."""
        result = await service.query(
            AnswerQuery(
                question=payload.question,
                history=tuple(
                    ConversationTurn(role=turn.role, content=turn.content)
                    for turn in payload.history
                ),
            )
        )
        if result.status == "unavailable":
            body = UnavailableResponse(referral=result.referral or "")
            return JSONResponse(status_code=503, content=body.model_dump())
        return AnswerResponse(
            status=result.status,
            answer=result.answer,
            reason=result.reason,
            citations=[
                CitationResponse(
                    chunk_id=item.chunk_id,
                    document_id=item.document_id,
                    document_title=item.document_title,
                    version_id=item.version_id,
                    version_label=item.version_label,
                    section=item.section,
                    page_start=item.page_start,
                    page_end=item.page_end,
                    quote=item.quote,
                )
                for item in result.citations
            ],
            referral=result.referral,
        )

    return router

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator

from unitropico_rag.services.query import QueryService


class QueryRequest(BaseModel):
    """Accept only a bounded question for the local deterministic PoC."""

    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=500, strict=True)

    @field_validator("question")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        """Reject whitespace-only input without altering length validation."""
        if not value.strip():
            raise ValueError("question must not be whitespace-only")
        return value


class QueryResponse(BaseModel):
    """Describe the transformation explicitly, never an institutional answer."""

    mode: Literal["deterministic-poc"] = "deterministic-poc"
    normalized_question: str


class HealthResponse(BaseModel):
    """Report only local application liveness, not external readiness."""

    status: Literal["ok"] = "ok"


def create_router(service: QueryService) -> APIRouter:
    """Bind transport handlers to the composed query service."""
    router = APIRouter()

    @router.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Return local liveness without testing any remote resource."""
        return HealthResponse()

    @router.post("/poc/query", response_model=QueryResponse)
    async def query(payload: QueryRequest) -> QueryResponse:
        """Validate transport input and return only the deterministic result."""
        return QueryResponse(normalized_question=await service.query(payload.question))

    return router

from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict

from unitropico_rag.domain.evidence import RetrievalQuery
from unitropico_rag.services.retrieval import RetrievalService


class EvidenceResponse(BaseModel):
    """Expose eligible exact text and attested physical PDF location only."""

    model_config = ConfigDict(strict=True, extra="forbid")
    document_id: str
    version_id: str
    chunk_id: str
    source_sha256: str
    page: int
    section: str | None
    text: str


class RetrievalResponse(BaseModel):
    """Describe retrieval, never an answer or a claim of sufficient evidence."""

    model_config = ConfigDict(strict=True, extra="forbid")
    status: Literal["unavailable", "abstained", "evidence_found"]
    evidence: list[EvidenceResponse]


def create_retrieval_router(service: RetrievalService) -> APIRouter:
    """Bind the internal retrieval transport to an explicitly composed service."""
    router = APIRouter()

    @router.post(
        "/retrieval/query",
        response_model=RetrievalResponse,
        responses={503: {"model": RetrievalResponse, "description": "Unavailable"}},
    )
    async def query(payload: RetrievalQuery, response: Response) -> RetrievalResponse:
        """Retrieve eligible text; 503 means unavailable, 200 may mean abstained.

        This internal route does not generate answers or establish sufficiency.
        Questions are trimmed to 1–500 characters; limit is 1–20 (default 5).
        Unknown fields are rejected. Page denotes the physical PDF page, not an
        inferred printed page; null sections remain null and no URL is invented.
        """
        result = await service.query(payload)
        if result.status == "unavailable":
            response.status_code = 503
        return RetrievalResponse(
            status=result.status,
            evidence=[
                EvidenceResponse(
                    document_id=item.document_id,
                    version_id=item.version_id,
                    chunk_id=item.chunk_id,
                    source_sha256=item.source_sha256,
                    page=item.page,
                    section=item.section,
                    text=item.text,
                )
                for item in result.evidence
            ],
        )

    return router

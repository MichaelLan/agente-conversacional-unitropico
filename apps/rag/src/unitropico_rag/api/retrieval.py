from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict

from unitropico_rag.domain.evidence import RetrievalQuery
from unitropico_rag.services.answer import EvidenceRetrieval


class EvidenceResponse(BaseModel):
    """Expose eligible exact text and attested physical PDF location only."""

    model_config = ConfigDict(strict=True, extra="forbid")
    document_id: str
    version_id: str
    chunk_id: str
    source_sha256: str
    page_start: int
    page_end: int
    section: str | None
    document_title: str
    version_label: str | None
    text: str


class RetrievalResponse(BaseModel):
    """Describe retrieval, never an answer or a claim of sufficient evidence."""

    model_config = ConfigDict(strict=True, extra="forbid")
    status: Literal["unavailable", "abstained", "evidence_found"]
    evidence: list[EvidenceResponse]


def create_retrieval_router(service: EvidenceRetrieval) -> APIRouter:
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
        Unknown fields are rejected. Pages are physical PDF pages from the
        catalog, not inferred printed pages; null sections remain null.
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
                    page_start=item.page_start,
                    page_end=item.page_end,
                    section=item.section,
                    document_title=item.document_title,
                    version_label=item.version_label,
                    text=item.text,
                )
                for item in result.evidence
            ],
        )

    return router

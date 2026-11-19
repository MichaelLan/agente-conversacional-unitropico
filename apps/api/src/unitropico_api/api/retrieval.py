from fastapi import APIRouter, Response

from unitropico_api.domain.errors import MalformedUpstream
from unitropico_api.domain.retrieval import RetrievalQuery, RetrievalResponse
from unitropico_api.services.retrieval import QueryCoordinationService


def create_retrieval_router(service: QueryCoordinationService) -> APIRouter:
    """Bind coordination to HTTP without providing a runtime RAG transport."""
    router = APIRouter()

    @router.post(
        "/retrieval/query",
        response_model=RetrievalResponse,
        responses={
            502: {"model": RetrievalResponse, "description": "Malformed upstream"},
            503: {"model": RetrievalResponse, "description": "Unavailable"},
        },
    )
    async def query(payload: RetrievalQuery, response: Response) -> RetrievalResponse:
        """Return evidence or abstention, never generated answers.

        Question is trimmed to 1–500 characters; limit is 1–20 (default 5).
        Extra fields, including privileged roles, are rejected (422).
        Missing configuration/dependency failure returns 503; invalid upstream
        returns 502. Both use unavailable with empty evidence and no diagnostics.
        Pages are physical (start/end); section is nullable; text is unchanged.
        No live API→RAG transport is configured by default.
        """
        try:
            result = await service.query(payload)
        except MalformedUpstream:
            response.status_code = 502
            return RetrievalResponse(status="unavailable", evidence=[])
        if result.status == "unavailable":
            response.status_code = 503
        return result

    return router

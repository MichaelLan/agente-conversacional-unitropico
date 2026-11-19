from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Report only local application liveness, not external readiness."""

    status: Literal["ok"] = "ok"


def create_health_router() -> APIRouter:
    """Expose liveness without testing any remote resource."""
    router = APIRouter()

    @router.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Return local liveness without testing any remote resource."""
        return HealthResponse()

    return router

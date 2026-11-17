from fastapi import FastAPI

from unitropico_api.api.health import router
from unitropico_api.api.retrieval import create_retrieval_router
from unitropico_api.core.logger import configure_logger
from unitropico_api.core.settings import Settings
from unitropico_api.services.retrieval import AsyncRagClient, QueryCoordinationService


def create_app(*, rag_client: AsyncRagClient | None = None) -> FastAPI:
    """Valida configuración y compone logging, aplicación HTTP y rutas."""
    settings = Settings()
    configure_logger(settings.log_level)
    app = FastAPI(title="API Unitrópico")
    app.include_router(router)
    app.include_router(create_retrieval_router(QueryCoordinationService(rag_client)))
    return app

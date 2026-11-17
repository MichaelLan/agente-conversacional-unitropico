from fastapi import FastAPI

from unitropico_rag.api.retrieval import create_retrieval_router
from unitropico_rag.api.routes import create_router
from unitropico_rag.core.logger import configure_logger
from unitropico_rag.core.settings import Settings
from unitropico_rag.infra.langchain import normalize_question
from unitropico_rag.services.query import QueryService
from unitropico_rag.services.retrieval import RetrievalService


def create_app(*, retrieval_service: RetrievalService | None = None) -> FastAPI:
    """Compose local settings and HTTP; retrieval defaults to unconfigured."""
    settings = Settings.from_environment()
    settings.disable_remote_tracing()
    configure_logger(settings.log_level)
    app = FastAPI(title="Unitrópico RAG — deterministic local PoC")
    app.include_router(create_router(QueryService(normalize_question)))
    app.include_router(
        create_retrieval_router(
            RetrievalService() if retrieval_service is None else retrieval_service
        )
    )
    return app

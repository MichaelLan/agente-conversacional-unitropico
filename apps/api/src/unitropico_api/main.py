import os

from fastapi import FastAPI

from unitropico_api.api.admin import create_admin_router, install_admin_error_handlers
from unitropico_api.api.chat import create_chat_router
from unitropico_api.api.health import router
from unitropico_api.api.retrieval import create_retrieval_router
from unitropico_api.core.logger import configure_logger
from unitropico_api.core.settings import Settings
from unitropico_api.services.admin import (
    AdminService,
    BlobStore,
    DocumentRepository,
    IngestionLauncher,
)
from unitropico_api.services.chat import AnswerClient, ChatService
from unitropico_api.services.retrieval import AsyncRagClient, QueryCoordinationService


def create_app(
    *,
    settings: Settings | None = None,
    rag_client: AsyncRagClient | None = None,
    chat_client: AnswerClient | None = None,
    documents: DocumentRepository | None = None,
    blobs: BlobStore | None = None,
    ingestion: IngestionLauncher | None = None,
) -> FastAPI:
    """Compose logging, HTTP app and routes; settings default to os.environ."""
    settings = settings or Settings.from_mapping(os.environ)
    configure_logger(settings.log_level)
    docs = settings.expose_docs
    app = FastAPI(
        title="API Unitrópico",
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.include_router(router)
    app.include_router(create_retrieval_router(QueryCoordinationService(rag_client)))
    app.include_router(create_chat_router(ChatService(chat_client)))
    admin = AdminService(
        documents,
        blobs,
        ingestion,
        max_upload_bytes=settings.admin_max_upload_bytes,
    )
    token = settings.admin_token.get_secret_value() if settings.admin_token else None
    app.include_router(create_admin_router(admin, token or None))
    install_admin_error_handlers(app)
    return app

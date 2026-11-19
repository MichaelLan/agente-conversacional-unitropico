import os

from fastapi import FastAPI

from unitropico_api.api.admin import create_admin_router, install_admin_error_handlers
from unitropico_api.api.chat import create_chat_router, unavailable_body
from unitropico_api.api.health import router
from unitropico_api.api.rate_limit import install_public_rejection_handler, rate_limit
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
from unitropico_api.services.rate_limit import RateLimiter, RateLimitGuard
from unitropico_api.services.retrieval import AsyncRagClient, QueryCoordinationService


def create_app(
    *,
    settings: Settings | None = None,
    rag_client: AsyncRagClient | None = None,
    chat_client: AnswerClient | None = None,
    documents: DocumentRepository | None = None,
    blobs: BlobStore | None = None,
    ingestion: IngestionLauncher | None = None,
    chat_limiter: RateLimiter | None = None,
    retrieval_limiter: RateLimiter | None = None,
) -> FastAPI:
    """Compose logging, HTTP app and routes; settings default to os.environ.

    Without limiters (and RATE_LIMIT_REQUIRED unset) public routes are not
    rate limited; with RATE_LIMIT_REQUIRED a missing limiter answers 503.
    """
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
    salt = (
        settings.rate_limit_salt.get_secret_value() if settings.rate_limit_salt else ""
    )
    required = settings.rate_limit_required

    def limits(limiter: RateLimiter | None, route: str, unavailable):
        """Route dependencies for one limiter; none when limiting is off."""
        if limiter is None and not required:
            return []
        guard = RateLimitGuard(limiter, route=route, salt=salt, required=required)
        return [rate_limit(guard, unavailable)]

    retrieval_limits = limits(
        retrieval_limiter,
        "retrieval",
        lambda: {"status": "unavailable", "evidence": []},
    )
    app.include_router(
        create_retrieval_router(QueryCoordinationService(rag_client), retrieval_limits)
    )
    chat_limits = limits(chat_limiter, "chat", unavailable_body)
    app.include_router(create_chat_router(ChatService(chat_client), chat_limits))
    install_public_rejection_handler(app)
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

from urllib.parse import unquote, urlsplit

from workers import Response, WorkerEntrypoint, asgi

from unitropico_api.api.admin import early_upload_rejection
from unitropico_api.api.rate_limit import early_public_body_rejection
from unitropico_api.core.settings import Settings
from unitropico_api.infra.d1 import D1DocumentRepository
from unitropico_api.infra.r2 import R2BlobStore
from unitropico_api.infra.rate_limit import WorkersRateLimiter
from unitropico_api.infra.service_binding import (
    RequestEnvMiddleware,
    RequestScopedRagClient,
    request_binding,
)
from unitropico_api.infra.workflow import WorkflowIngestionLauncher
from unitropico_api.main import create_app

_app = None
_settings = None


def _compose(env):
    """Compose once per isolate; bindings are resolved per request."""
    global _settings
    values = {
        name: getattr(env, name)
        for name in Settings.ENV_NAMES
        if getattr(env, name, None) is not None
    }
    settings = Settings.from_mapping(values)
    _settings = settings
    client = RequestScopedRagClient(
        base_url=settings.rag_base_url,
        retrieval_timeout=settings.rag_retrieval_timeout_seconds,
        answer_timeout=settings.rag_answer_timeout_seconds,
    )
    app = create_app(
        settings=settings,
        rag_client=client,
        chat_client=client,
        documents=D1DocumentRepository(lambda: request_binding("DB")),
        blobs=R2BlobStore(lambda: request_binding("DOCUMENTS")),
        ingestion=WorkflowIngestionLauncher(lambda: request_binding("INGESTION")),
        chat_limiter=WorkersRateLimiter(lambda: request_binding("CHAT_LIMITER")),
        retrieval_limiter=WorkersRateLimiter(
            lambda: request_binding("RETRIEVAL_LIMITER")
        ),
    )
    return RequestEnvMiddleware(app)


class Default(WorkerEntrypoint):
    """Bridge Workers requests to the lazily composed ASGI application."""

    async def fetch(self, request):
        """Pass the real environment and execution context to the SDK ASGI bridge."""
        global _app
        if _app is None:
            _app = _compose(self.env)
        # asgi.fetch buffers the whole body before FastAPI runs, so decide on
        # uploads and oversized public bodies from the headers alone.
        token = _settings.admin_token
        path = unquote(urlsplit(request.url).path)
        rejection = early_upload_rejection(
            request.method,
            path,
            request.headers.get,
            token.get_secret_value() if token else None,
            _settings.admin_max_upload_bytes,
        ) or early_public_body_rejection(request.method, path, request.headers.get)
        if rejection is not None:
            status, headers, body = rejection
            return Response.from_json(body, status=status, headers=headers)
        return await asgi.fetch(_app, request, self.env, self.ctx)

from urllib.parse import unquote, urlsplit

from workers import Response, WorkerEntrypoint, asgi

from unitropico_api.api.admin import early_upload_rejection
from unitropico_api.core.settings import Settings
from unitropico_api.infra.d1 import D1DocumentRepository
from unitropico_api.infra.r2 import R2BlobStore
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
    """Compose once per isolate; RAG, DB, DOCUMENTS and INGESTION per request."""
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
        # uploads from the headers alone, without touching the body.
        token = _settings.admin_token
        rejection = early_upload_rejection(
            request.method,
            unquote(urlsplit(request.url).path),
            request.headers.get,
            token.get_secret_value() if token else None,
            _settings.admin_max_upload_bytes,
        )
        if rejection is not None:
            status, headers, body = rejection
            return Response.from_json(body, status=status, headers=headers)
        return await asgi.fetch(_app, request, self.env, self.ctx)

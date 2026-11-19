from workers import Response, WorkerEntrypoint, WorkflowEntrypoint
from workers import fetch as workers_fetch
from workers.workflows import NonRetryableError

from unitropico_ingestion.core.logger import configure_logger
from unitropico_ingestion.core.settings import ENV_NAMES, Settings
from unitropico_ingestion.infra.d1_repository import D1VersionRepository
from unitropico_ingestion.infra.firecrawl import FirecrawlOcrClient, pyodide_runtime
from unitropico_ingestion.infra.gateway_embeddings import GatewayEmbedder
from unitropico_ingestion.infra.r2_store import R2BlobStore
from unitropico_ingestion.infra.vectorize_index import VectorizeIndex
from unitropico_ingestion.services.chunker import StructuralChunker
from unitropico_ingestion.workflow import IngestionConfig, IngestionDeps, run_ingestion


def _env_mapping(env: object) -> dict[str, object]:
    """Copy known settings names from the Workers ``env`` into a plain dict."""
    return {name: getattr(env, name, None) for name in ENV_NAMES.values()}


def _deps(env: object, settings: Settings) -> IngestionDeps:
    """Compose the adapters from the bindings and settings of this isolate."""
    firecrawl_key = settings.firecrawl_api_key
    gateway_key = settings.ai_gateway_api_key
    return IngestionDeps(
        blobs=R2BlobStore(env.DOCUMENTS),
        ocr=FirecrawlOcrClient(
            api_key=firecrawl_key.value if firecrawl_key else None,
            runtime=pyodide_runtime,
            url=settings.firecrawl_url,
            max_pages=settings.ocr_max_pages,
        ),
        embedder=GatewayEmbedder(
            api_key=gateway_key.value if gateway_key else None,
            fetch=workers_fetch,
            model=settings.embedding_model,
            url=settings.embedding_url,
        ),
        repository=D1VersionRepository(env.DB),
        index=VectorizeIndex(env.VECTORIZE),
        chunker=StructuralChunker(),
        config=IngestionConfig(
            ocr_version=settings.ocr_version,
            ocr_max_pages=settings.ocr_max_pages,
            embedding_model=settings.embedding_model,
            embedding_dimensions=settings.embedding_dimensions,
            secrets=settings.secrets(),
        ),
    )


class IngestionWorkflow(WorkflowEntrypoint):
    """OCR → chunks (D1/FTS5) → embeddings → Vectorize for one document version.

    Params (created by the API as instance ``ver_{version_id}``):
    ``{"version_id": "..."}``. Ends in ``review_required`` or ``ready``;
    publishing is always a manual administrator action.
    """

    async def run(self, event, step):
        """Run the durable steps; failures end as NonRetryableError(code)."""
        try:
            settings = Settings.from_mapping(_env_mapping(self.env))
        except ValueError as error:
            raise NonRetryableError(str(error)) from None
        configure_logger(settings.log_level)
        try:
            payload = event["payload"]
        except KeyError, TypeError:
            payload = None
        return await run_ingestion(
            payload,
            step,
            _deps(self.env, settings),
            non_retryable=NonRetryableError,
        )


class Default(WorkerEntrypoint):
    """No public HTTP surface: the Workflow is triggered through its binding."""

    async def fetch(self, request):
        """Always respond 404."""
        return Response("not found", status=404)

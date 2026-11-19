"""Workflow orchestration, independent of the Workers runtime.

``run_ingestion`` receives the Workflow ``step`` object (``step.do(name=...,
config=...)`` decorator; the decorated coroutine is awaited) and the ports.
Steps take no parameters: results of previous steps are captured from their
return values, which the engine replays from its cache when an instance
resumes. ``non_retryable`` builds the runtime ``NonRetryableError``.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from unitropico_ingestion.core.logger import logger
from unitropico_ingestion.domain.chunking import Chunker
from unitropico_ingestion.domain.ingestion import (
    ErrorCode,
    IngestionError,
    IngestionParams,
    VersionRecord,
    sanitize_detail,
)
from unitropico_ingestion.ports import (
    BlobStore,
    Embedder,
    OcrClient,
    VectorIndex,
    VersionRepository,
)
from unitropico_ingestion.services import ingestion

STATE_STEP = {
    "retries": {"limit": 3, "delay": "5 seconds", "backoff": "exponential"},
    "timeout": "2 minutes",
}
# Every OCR attempt may be billed: one retry only, and 4xx are non-retryable.
OCR_STEP = {
    "retries": {"limit": 1, "delay": "30 seconds", "backoff": "constant"},
    "timeout": "6 minutes",
}
WORK_STEP = {
    "retries": {"limit": 3, "delay": "10 seconds", "backoff": "exponential"},
    "timeout": "5 minutes",
}

# Workflow step -> (processing_events.step, default error code).
_STEP_EVENTS = {
    "mark_processing": ("extract", ErrorCode.INTERNAL_ERROR),
    "ocr": ("extract", ErrorCode.OCR_FAILED),
    "chunk": ("chunk", ErrorCode.CHUNK_FAILED),
    "embed": ("embed", ErrorCode.EMBEDDING_FAILED),
    "index": ("index_vectors", ErrorCode.INDEX_FAILED),
    "finalize": ("review", ErrorCode.INTERNAL_ERROR),
}


class WorkflowStep(Protocol):
    """The subset of the Workflows ``step`` API used here."""

    def do(
        self, name: str | None = None, *, concurrent: bool = False, config: Any = None
    ) -> Callable[[Callable[[], Awaitable[Any]]], Callable[[], Awaitable[Any]]]:
        """Return a decorator that turns a coroutine into a durable step."""
        ...


@dataclass(frozen=True)
class IngestionConfig:
    """Per-deployment values the use cases need (from Settings)."""

    ocr_version: str
    ocr_max_pages: int
    embedding_model: str
    embedding_dimensions: int
    secrets: tuple[str, ...] = ()


@dataclass(frozen=True)
class IngestionDeps:
    """Composed adapters for one Workflow run."""

    blobs: BlobStore
    ocr: OcrClient
    embedder: Embedder
    repository: VersionRepository
    index: VectorIndex
    chunker: Chunker
    config: IngestionConfig


def _stable_code(code: str) -> ErrorCode | None:
    """Return the ``ErrorCode`` named ``code`` or None when it is not stable."""
    try:
        return ErrorCode(code)
    except ValueError:
        return None


def _family(step_name: str) -> str:
    return "embed" if step_name.startswith("embed_") else step_name


async def run_ingestion(
    payload: object,
    step: WorkflowStep,
    deps: IngestionDeps,
    *,
    non_retryable: Callable[[str], BaseException],
) -> dict:
    """Run mark_processing → ocr → chunk → embed_n → index → finalize.

    Any failure marks the version ``failed`` (code + sanitized detail + event)
    in a durable ``mark_failed`` step and ends the instance with
    ``non_retryable(code)``. Publishing is always a manual admin action.
    """
    try:
        params = IngestionParams.from_payload(payload)
    except IngestionError as error:
        raise non_retryable(str(error.code)) from None
    version_id = params.version_id
    repo, config = deps.repository, deps.config
    failure: dict[str, Any] = {"step": "mark_processing", "error": None}

    def durable(name: str, conf: dict, body: Callable[[], Awaitable[dict]]):
        # Attribute any failure (even one raised by the engine around the step,
        # e.g. a timeout) to this step before it is awaited.
        failure["step"] = name
        failure["error"] = None

        @step.do(name=name, config=conf)
        async def _run() -> dict:
            failure["step"] = name
            try:
                return await body()
            except IngestionError as error:
                failure["error"] = error
                logger.warning(
                    "step_failed step=%s code=%s permanent=%s",
                    name,
                    error.code,
                    error.permanent,
                )
                if error.permanent:
                    raise non_retryable(str(error.code)) from None
                raise
            except Exception as error:
                # A runtime ``NonRetryableError(code)`` carries a stable code.
                code = _stable_code(str(error).strip())
                failure["error"] = IngestionError(
                    code or _STEP_EVENTS[_family(name)][1],
                    f"error={type(error).__name__}",
                )
                logger.warning(
                    "step_failed step=%s error=%s", name, type(error).__name__
                )
                raise

        return _run

    try:

        async def _mark() -> dict:
            version = await ingestion.mark_processing(repo, params)
            return {
                "document_id": version.document_id,
                "source_r2_key": version.source_r2_key,
                "source_sha256": version.source_sha256,
            }

        source = await durable("mark_processing", STATE_STEP, _mark)()
        version = VersionRecord(
            id=version_id,
            document_id=source["document_id"],
            status="processing",
            source_r2_key=source["source_r2_key"],
            source_sha256=source["source_sha256"],
            requires_review=True,
        )

        async def _ocr() -> dict:
            return await ingestion.run_ocr(
                blobs=deps.blobs,
                ocr=deps.ocr,
                repo=repo,
                version=version,
                ocr_version=config.ocr_version,
                max_pages=config.ocr_max_pages,
            )

        ocr = await durable("ocr", OCR_STEP, _ocr)()

        async def _chunk() -> dict:
            return await ingestion.chunk_version(
                blobs=deps.blobs,
                repo=repo,
                chunker=deps.chunker,
                version_id=version_id,
                ocr_key=ocr["ocr_key"],
            )

        chunked = await durable("chunk", WORK_STEP, _chunk)()
        batches = ingestion.batch_count(chunked["chunk_count"])
        reused_batches = 0
        for batch in range(batches):

            async def _embed(batch: int = batch) -> dict:
                return await ingestion.embed_chunks(
                    blobs=deps.blobs,
                    embedder=deps.embedder,
                    version_id=version_id,
                    chunks_key=chunked["chunks_key"],
                    chunker_version=chunked["chunker_version"],
                    batch=batch,
                    model=config.embedding_model,
                    dimensions=config.embedding_dimensions,
                )

            embedded = await durable(f"embed_{batch}", WORK_STEP, _embed)()
            reused_batches += bool(embedded["reused"])

        async def _index() -> dict:
            return await ingestion.index_chunks(
                blobs=deps.blobs,
                repo=repo,
                index=deps.index,
                version=version,
                chunks_key=chunked["chunks_key"],
                chunker_version=chunked["chunker_version"],
                batches=batches,
                model=config.embedding_model,
            )

        indexed = await durable("index", WORK_STEP, _index)()

        async def _finalize() -> dict:
            return await ingestion.finalize(repo, version_id)

        final = await durable("finalize", STATE_STEP, _finalize)()
    except Exception as exc:
        error = failure["error"]
        name = failure["step"]
        event_step, default_code = _STEP_EVENTS[_family(name)]
        if isinstance(error, IngestionError):
            code = error.code
        else:
            # A permanent error reaches here as ``non_retryable(code)`` with no
            # recorded error when the failure was raised outside ``durable``.
            code = default_code
            for candidate in map(str, getattr(exc, "args", ())):
                code = _stable_code(candidate.strip()) or code
        raw_detail = error.detail if isinstance(error, IngestionError) else ""
        detail = sanitize_detail(f"step={name} {raw_detail}", config.secrets)

        async def _fail() -> dict:
            marked = await repo.mark_failed(version_id, code=str(code), detail=detail)
            if marked:
                await repo.log_event(
                    version_id,
                    step=event_step,
                    status="failed",
                    error_code=str(code),
                    detail=detail,
                )
            return {"marked": marked}

        try:
            await step.do(name="mark_failed", config=STATE_STEP)(_fail)()
        except Exception as fail_error:
            logger.error(
                "mark_failed_error step=%s code=%s error=%s",
                name,
                code,
                type(fail_error).__name__,
            )
        raise non_retryable(str(code)) from None

    logger.info(
        "ingestion_done chunks=%s status=%s", chunked["chunk_count"], final["status"]
    )
    return {
        "version_id": version_id,
        "status": final["status"],
        "chunk_count": chunked["chunk_count"],
        "corrections_applied": len(chunked.get("corrections_applied", [])),
        "corrections_pending": bool(chunked.get("corrections_pending", False)),
        "page_count": ocr["page_count"],
        "ocr_reused": ocr["reused"],
        "embedding_batches_reused": reused_batches,
        "vectors_upserted": indexed["upserted"],
        "vectors_deleted": indexed["deleted"],
    }

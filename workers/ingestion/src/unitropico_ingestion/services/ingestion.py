"""Ingestion use cases, one per Workflow step.

Every function is idempotent: R2 keys are deterministic, D1 writes replace by
version and Vectorize upserts use stable ids. Return values are small dicts
(keys and counts) because a Workflow step result is limited to 1 MiB.
"""

import json
import math
from collections.abc import Sequence
from dataclasses import asdict
from hashlib import sha256

from unitropico_ingestion.domain.chunking import Chunk, Chunker, PageText
from unitropico_ingestion.domain.ingestion import (
    ErrorCode,
    IngestionError,
    IngestionParams,
    VersionRecord,
)
from unitropico_ingestion.ports import (
    BlobStore,
    Embedder,
    OcrClient,
    VectorIndex,
    VectorRecord,
    VersionRepository,
)
from unitropico_ingestion.services.ocr_parser import parse_ocr_response

EMBED_BATCH_SIZE = 96
"""Chunks per embedding request (one ``embed_{n}`` step each)."""

VECTORIZE_BATCH_SIZE = 1000
"""Maximum vectors per Vectorize upsert/delete from a Worker."""

MAX_PDF_BYTES = 50_000_000
"""Firecrawl Parse accepts files up to 50 MB per request."""

REPROCESSABLE_STATES = frozenset(
    {"uploaded", "failed", "extracted", "review_required", "ready", "unpublished"}
)
JSON = "application/json"


# -- deterministic R2 keys -----------------------------------------------------
def ocr_key(source_sha256: str, ocr_version: str, max_pages: int) -> str:
    """Raw OCR response, shared by every reprocess of the same bytes.

    ``max_pages`` is part of the key: a response parsed with another page cap
    is a different (possibly truncated) result.
    """
    return f"ocr/{source_sha256}/{ocr_version}-p{max_pages}.json"


def chunks_key(version_id: str, chunker_version: str) -> str:
    """JSONL with the chunks of one version and chunker."""
    return f"chunks/{version_id}/{chunker_version}.jsonl"


def embeddings_key(
    version_id: str, chunker_version: str, model: str, batch: int
) -> str:
    """Embeddings of one batch of ``EMBED_BATCH_SIZE`` chunks."""
    return f"embeddings/{version_id}/{chunker_version}/{model}/{batch}.json"


def vectors_manifest_key(version_id: str) -> str:
    """Ids currently (or possibly) present in Vectorize for the version."""
    return f"vectors/{version_id}/manifest.json"


def batch_count(chunk_count: int) -> int:
    """Return the number of ``embed_{n}`` steps for a chunk count."""
    return math.ceil(chunk_count / EMBED_BATCH_SIZE)


def token_estimate(text: str) -> int:
    """Cheap token estimate (~4 characters per token), at least 1."""
    return max(1, math.ceil(len(text) / 4))


# -- step 1 ----------------------------------------------------------------------
async def mark_processing(
    repo: VersionRepository, params: IngestionParams
) -> VersionRecord:
    """Enter ``processing`` (one attempt) or accept a resumed ``processing``.

    The API sets ``requires_review=1`` on reprocess but leaves ``status``; this
    step performs the transition (allowed by the schema trigger from uploaded,
    failed, extracted, review_required, ready and unpublished).
    """
    version_id = params.version_id
    version = await repo.get_version(version_id)
    if version is None:
        raise IngestionError(
            ErrorCode.INVALID_SOURCE, "version_not_found", permanent=True
        )
    mismatches = params.mismatches(version)
    if mismatches:
        raise IngestionError(
            ErrorCode.INVALID_SOURCE,
            "payload_mismatch=" + ",".join(mismatches),
            permanent=True,
        )
    if version.status in REPROCESSABLE_STATES:
        if not await repo.start_processing(version_id):
            version = await repo.get_version(version_id)
            if version is None or version.status != "processing":
                raise IngestionError(
                    ErrorCode.INVALID_STATE, "concurrent_change", permanent=False
                )
        await repo.log_event(version_id, step="extract", status="started")
    elif version.status != "processing":
        raise IngestionError(
            ErrorCode.INVALID_STATE, f"status={version.status}", permanent=True
        )
    return version


# -- step 2 ----------------------------------------------------------------------
async def run_ocr(
    *,
    blobs: BlobStore,
    ocr: OcrClient,
    repo: VersionRepository,
    version: VersionRecord,
    ocr_version: str,
    max_pages: int,
) -> dict:
    """Reuse ``ocr/{sha}/{ocr_version}-p{max_pages}.json`` or pay one OCR call."""
    key = ocr_key(version.source_sha256, ocr_version, max_pages)
    raw = await blobs.get(key)
    reused = raw is not None
    if raw is None:
        pdf = await blobs.get(version.source_r2_key)
        if pdf is None:
            raise IngestionError(
                ErrorCode.INVALID_SOURCE, "source_missing", permanent=True
            )
        if not pdf.startswith(b"%PDF"):
            raise IngestionError(ErrorCode.INVALID_SOURCE, "not_pdf", permanent=True)
        if len(pdf) > MAX_PDF_BYTES:
            raise IngestionError(
                ErrorCode.INVALID_SOURCE, f"bytes={len(pdf)}", permanent=True
            )
        if sha256(pdf).hexdigest() != version.source_sha256:
            raise IngestionError(
                ErrorCode.INVALID_SOURCE, "sha256_mismatch", permanent=True
            )
        raw = await ocr.parse(pdf, filename=f"{version.source_sha256[:16]}.pdf")
        # Never cache an unsuccessful or truncated body (raises when invalid).
        result = parse_ocr_response(raw)
        await blobs.put(key, raw, content_type=JSON)
    else:
        result = parse_ocr_response(raw)
    if not await repo.mark_extracted(
        version.id, page_count=result.page_count, extraction_key=key
    ):
        raise IngestionError(ErrorCode.INVALID_STATE, "mark_extracted", permanent=True)
    await repo.log_event(
        version.id,
        step="extract",
        status="skipped" if reused else "succeeded",
        detail=(
            f"reused={int(reused)} pages={result.page_count} "
            f"text_pages={result.text_pages} failed_pages={len(result.failed_pages)}"
        ),
    )
    return {
        "ocr_key": key,
        "page_count": result.page_count,
        "text_pages": result.text_pages,
        "reused": reused,
    }


# -- step 3 ----------------------------------------------------------------------
def _chunk_line(chunk: Chunk) -> str:
    row = asdict(chunk)
    row["token_estimate"] = token_estimate(chunk.text)
    return json.dumps(row, ensure_ascii=False, sort_keys=True)


def _parse_chunks(data: bytes) -> list[dict]:
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line]


async def chunk_version(
    *,
    blobs: BlobStore,
    repo: VersionRepository,
    chunker: Chunker,
    version_id: str,
    ocr_key: str,
) -> dict:
    """Chunk the stored OCR by physical page and replace the version chunks."""
    raw = await blobs.get(ocr_key)
    if raw is None:
        raise IngestionError(ErrorCode.CHUNK_FAILED, "ocr_missing", permanent=True)
    result = parse_ocr_response(raw)
    pages = [PageText(page.page_number, page.markdown) for page in result.pages]
    chunks = chunker.chunk(pages, version_id=version_id)
    if not chunks:
        raise IngestionError(ErrorCode.OCR_EMPTY, "chunks=0", permanent=True)
    key = chunks_key(version_id, chunker.version)
    body = "".join(_chunk_line(chunk) + "\n" for chunk in chunks).encode("utf-8")
    await blobs.put(key, body, content_type="application/x-ndjson")
    await repo.replace_chunks(
        version_id, chunker_version=chunker.version, chunks=chunks
    )
    await repo.log_event(
        version_id, step="chunk", status="succeeded", detail=f"chunks={len(chunks)}"
    )
    await repo.log_event(version_id, step="index_fts", status="succeeded")
    return {
        "chunks_key": key,
        "chunk_count": len(chunks),
        "chunker_version": chunker.version,
    }


async def _load_chunks(blobs: BlobStore, key: str) -> list[dict]:
    data = await blobs.get(key)
    if data is None:
        raise IngestionError(ErrorCode.CHUNK_FAILED, "chunks_missing", permanent=True)
    return _parse_chunks(data)


# -- step 4 ----------------------------------------------------------------------
def _valid_vectors(vectors: object, count: int, dimensions: int) -> bool:
    return (
        isinstance(vectors, list)
        and len(vectors) == count
        and all(
            isinstance(vector, list)
            and len(vector) == dimensions
            and all(type(x) in (int, float) and math.isfinite(x) for x in vector)
            for vector in vectors
        )
    )


async def embed_chunks(
    *,
    blobs: BlobStore,
    embedder: Embedder,
    version_id: str,
    chunks_key: str,
    chunker_version: str,
    batch: int,
    model: str,
    dimensions: int,
) -> dict:
    """Embed batch ``n`` (≤ 96 chunks) or reuse it from R2; validate dimensions."""
    chunks = (await _load_chunks(blobs, chunks_key))[
        batch * EMBED_BATCH_SIZE : (batch + 1) * EMBED_BATCH_SIZE
    ]
    if not chunks:
        raise IngestionError(
            ErrorCode.EMBEDDING_FAILED, f"empty_batch={batch}", permanent=True
        )
    ids = [chunk["id"] for chunk in chunks]
    key = embeddings_key(version_id, chunker_version, model, batch)
    cached = await blobs.get(key)
    if cached is not None:
        try:
            stored = json.loads(cached)
        except ValueError:
            stored = None
        if (
            isinstance(stored, dict)
            and stored.get("model") == model
            and stored.get("dimensions") == dimensions
            and stored.get("ids") == ids
            and _valid_vectors(stored.get("vectors"), len(ids), dimensions)
        ):
            return {"batch": batch, "count": len(ids), "reused": True}
    vectors = await embedder.embed([chunk["text"] for chunk in chunks])
    if not isinstance(vectors, list) or len(vectors) != len(ids):
        raise IngestionError(
            ErrorCode.EMBEDDING_FAILED, "count_mismatch", permanent=True
        )
    if not _valid_vectors(vectors, len(ids), dimensions):
        got = len(vectors[0]) if vectors and isinstance(vectors[0], list) else 0
        raise IngestionError(
            ErrorCode.EMBEDDING_FAILED,
            f"dimension_mismatch expected={dimensions} got={got}",
            permanent=True,
        )
    payload = {"model": model, "dimensions": dimensions, "ids": ids, "vectors": vectors}
    await blobs.put(key, json.dumps(payload).encode("utf-8"), content_type=JSON)
    return {"batch": batch, "count": len(ids), "reused": False}


# -- step 5 ----------------------------------------------------------------------
async def _read_manifest(blobs: BlobStore, version_id: str) -> list[str]:
    data = await blobs.get(vectors_manifest_key(version_id))
    if data is None:
        return []
    ids = json.loads(data).get("ids", [])
    return [i for i in ids if isinstance(i, str)]


async def _write_manifest(blobs: BlobStore, version_id: str, ids: Sequence[str]):
    body = json.dumps({"ids": sorted(set(ids))}).encode("utf-8")
    await blobs.put(vectors_manifest_key(version_id), body, content_type=JSON)


async def index_chunks(
    *,
    blobs: BlobStore,
    repo: VersionRepository,
    index: VectorIndex,
    version: VersionRecord,
    chunks_key: str,
    chunker_version: str,
    batches: int,
    model: str,
) -> dict:
    """Upsert the version vectors and delete the ids left by a previous run.

    The R2 manifest records every id that may be in Vectorize: it is widened
    to old ∪ new before the upsert and narrowed to new afterwards, so a crash
    at any point never forgets a stale id.
    """
    chunks = await _load_chunks(blobs, chunks_key)
    new_ids = [chunk["id"] for chunk in chunks]
    old_ids = await _read_manifest(blobs, version.id)
    await _write_manifest(blobs, version.id, [*old_ids, *new_ids])
    metadata = {"version_id": version.id, "document_id": version.document_id}
    upserted = 0
    for batch in range(batches):
        data = await blobs.get(
            embeddings_key(version.id, chunker_version, model, batch)
        )
        if data is None:
            raise IngestionError(
                ErrorCode.INDEX_FAILED, f"embeddings_missing={batch}", permanent=True
            )
        stored = json.loads(data)
        records = [
            VectorRecord(id=chunk_id, values=values, metadata=dict(metadata))
            for chunk_id, values in zip(stored["ids"], stored["vectors"], strict=True)
        ]
        for start in range(0, len(records), VECTORIZE_BATCH_SIZE):
            await index.upsert(records[start : start + VECTORIZE_BATCH_SIZE])
        upserted += len(records)
    if upserted != len(new_ids):
        raise IngestionError(
            ErrorCode.INDEX_FAILED,
            f"vectors={upserted} chunks={len(new_ids)}",
            permanent=True,
        )
    stale = sorted(set(old_ids) - set(new_ids))
    for start in range(0, len(stale), VECTORIZE_BATCH_SIZE):
        await index.delete_by_ids(stale[start : start + VECTORIZE_BATCH_SIZE])
    await _write_manifest(blobs, version.id, new_ids)
    await repo.set_embedding_model(version.id, model)
    await repo.log_event(
        version.id,
        step="embed",
        status="succeeded",
        detail=f"model={model} batches={batches}",
    )
    await repo.log_event(
        version.id,
        step="index_vectors",
        status="succeeded",
        detail=f"upserted={upserted} deleted={len(stale)}",
    )
    return {"upserted": upserted, "deleted": len(stale)}


# -- step 6 ----------------------------------------------------------------------
async def finalize(repo: VersionRepository, version_id: str) -> dict:
    """Leave the version in review_required or ready; never publish."""
    status = await repo.finalize(version_id)
    if status not in ("review_required", "ready"):
        raise IngestionError(
            ErrorCode.INVALID_STATE, f"status={status}", permanent=True
        )
    await repo.log_event(
        version_id,
        step="review",
        status="started" if status == "review_required" else "skipped",
        detail=f"status={status}",
    )
    return {"status": status}

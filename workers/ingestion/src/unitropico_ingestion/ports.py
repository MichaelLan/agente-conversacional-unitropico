"""Ports used by the ingestion use cases; adapters live in ``infra/``."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from unitropico_ingestion.domain.chunking import Chunk
from unitropico_ingestion.domain.ingestion import VersionRecord


class BlobStore(Protocol):
    """Object storage (R2) with deterministic keys."""

    async def get(self, key: str) -> bytes | None:
        """Return the object bytes or None when the key does not exist."""
        ...

    async def exists(self, key: str) -> bool:
        """Return True when the key exists, without downloading it."""
        ...

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        """Store (overwrite) an object."""
        ...


class OcrClient(Protocol):
    """Document OCR provider (Firecrawl Parse v2)."""

    async def parse(self, pdf: bytes, *, filename: str) -> bytes:
        """Return the raw JSON response body; raise IngestionError on failure."""
        ...


class Embedder(Protocol):
    """Text embedding provider."""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one vector per input text, in order."""
        ...


@dataclass(frozen=True)
class VectorRecord:
    """One vector to upsert; ``id`` is the D1 chunk id."""

    id: str
    values: list[float]
    metadata: dict[str, str]


class VectorIndex(Protocol):
    """Vector index (Vectorize)."""

    async def upsert(self, vectors: Sequence[VectorRecord]) -> None:
        """Insert or replace vectors by id."""
        ...

    async def delete_by_ids(self, ids: Sequence[str]) -> None:
        """Delete vectors by id; unknown ids are ignored."""
        ...


class VersionRepository(Protocol):
    """Catalog state of document versions, chunks and processing events (D1)."""

    async def get_version(self, version_id: str) -> VersionRecord | None:
        """Return the version or None."""
        ...

    async def start_processing(self, version_id: str) -> bool:
        """Move to processing and count an attempt; False when not allowed."""
        ...

    async def mark_extracted(
        self, version_id: str, *, page_count: int, extraction_key: str
    ) -> bool:
        """Move processing/extracted to extracted with OCR provenance."""
        ...

    async def replace_chunks(
        self, version_id: str, *, chunker_version: str, chunks: Sequence[Chunk]
    ) -> None:
        """Delete the version chunks and insert the new ones (FTS via triggers)."""
        ...

    async def set_embedding_model(self, version_id: str, model: str) -> None:
        """Record the embedding model on every chunk of the version."""
        ...

    async def finalize(self, version_id: str) -> str | None:
        """Move extracted to review_required/ready; return the resulting status."""
        ...

    async def mark_failed(self, version_id: str, *, code: str, detail: str) -> bool:
        """Move to failed with a code and a sanitized detail when allowed."""
        ...

    async def log_event(
        self,
        version_id: str,
        *,
        step: str,
        status: str,
        error_code: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Append a processing event (no personal data, no document text)."""
        ...

from collections.abc import Callable, Mapping
from typing import Protocol

from unitropico_api.core.logger import get_logger
from unitropico_api.domain.admin import StorageUnavailable

_log = get_logger()


class R2Bucket(Protocol):
    """Subset of the R2 binding used to store original PDFs."""

    async def head(self, key: str) -> object:
        """Return object metadata or None."""
        ...

    async def put(self, key: str, value: bytes, **options: object) -> object:
        """Store an object."""
        ...


def _size(meta: object) -> object:
    """Read `size` from an R2Object proxy or a converted mapping."""
    if isinstance(meta, Mapping):
        return meta.get("size")
    return getattr(meta, "size", None)


class R2BlobStore:
    """Blob adapter over an R2 binding resolved at call time."""

    def __init__(self, bucket: Callable[[], R2Bucket | None]) -> None:
        """Receive a provider of the current request's R2 binding."""
        self._bucket = bucket

    async def put_pdf(self, key: str, data: bytes, sha256: str) -> bool:
        """Store the PDF unless an object of the same size exists; True if written."""
        bucket = self._bucket()
        if bucket is None:
            raise StorageUnavailable(
                "storage_unavailable", "El almacenamiento no está configurado."
            )
        try:
            existing = await bucket.head(key)
            if existing is not None and _size(existing) == len(data):
                return False
            # R2 verifies the sha256 checksum before accepting the object.
            await bucket.put(
                key,
                data,
                httpMetadata={"contentType": "application/pdf"},
                sha256=sha256,
            )
        except Exception as error:  # JS exceptions have no common base.
            _log.warning("r2 failure type=%s", type(error).__name__)
            raise StorageUnavailable(
                "storage_unavailable", "No se pudo guardar el archivo."
            ) from None
        return True

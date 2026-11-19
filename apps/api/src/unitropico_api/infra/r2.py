import json
from collections.abc import Callable, Mapping
from typing import Protocol

from unitropico_api.core.logger import get_logger
from unitropico_api.domain.admin import StorageUnavailable

_log = get_logger()


class R2Bucket(Protocol):
    """Subset of the R2 binding: store original PDFs, read ingestion JSON."""

    async def get(self, key: str) -> object:
        """Return an object body (with ``text()``) or None."""
        ...

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

    def _require(self) -> R2Bucket:
        """Return the binding or fail closed."""
        bucket = self._bucket()
        if bucket is None:
            raise StorageUnavailable(
                "storage_unavailable", "El almacenamiento no está configurado."
            )
        return bucket

    async def get_json(self, key: str) -> object | None:
        """Return the parsed JSON object at ``key``, or None when absent.

        A body that is not valid JSON is treated as absent (logged): the
        caller can only report the data as unavailable.
        """
        bucket = self._require()
        try:
            obj = await bucket.get(key)
            if obj is None:
                return None
            text = await obj.text()  # type: ignore[attr-defined]
        except Exception as error:  # JS exceptions have no common base.
            _log.warning("r2 failure type=%s", type(error).__name__)
            raise StorageUnavailable(
                "storage_unavailable", "No se pudo leer el almacenamiento."
            ) from None
        try:
            return json.loads(str(text))
        except ValueError:
            _log.warning("r2 invalid json key_prefix=%s", key.split("/", 1)[0])
            return None

    async def put_pdf(self, key: str, data: bytes, sha256: str) -> bool:
        """Store the PDF unless an object of the same size exists; True if written."""
        bucket = self._require()
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

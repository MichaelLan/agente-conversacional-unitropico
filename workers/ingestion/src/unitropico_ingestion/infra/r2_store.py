"""R2 adapter over the ``DOCUMENTS`` binding (SDK-wrapped ``self.env``)."""


def _to_bytes(buffer: object) -> bytes:
    """Convert an ``arrayBuffer()`` result (memoryview or JsProxy) to bytes."""
    to_bytes = getattr(buffer, "to_bytes", None)
    if callable(to_bytes):
        return to_bytes()
    return bytes(buffer)  # type: ignore[arg-type]


class R2BlobStore:
    """Implement ``BlobStore`` with ``get``/``head``/``put`` on an R2 bucket."""

    def __init__(self, bucket: object):
        """Receive the bucket binding at composition time."""
        self._bucket = bucket

    async def get(self, key: str) -> bytes | None:
        """Return the object bytes, or None when absent."""
        obj = await self._bucket.get(key)  # type: ignore[attr-defined]
        if obj is None:
            return None
        return _to_bytes(await obj.arrayBuffer())

    async def exists(self, key: str) -> bool:
        """Check existence with ``head`` (no body transfer)."""
        return await self._bucket.head(key) is not None  # type: ignore[attr-defined]

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        """Overwrite the object with the given content type."""
        await self._bucket.put(  # type: ignore[attr-defined]
            key, data, httpMetadata={"contentType": content_type}
        )

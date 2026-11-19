"""Vectorize adapter over the ``VECTORIZE`` binding (SDK-wrapped ``self.env``)."""

from collections.abc import Sequence

from unitropico_ingestion.ports import VectorRecord

MAX_BATCH = 1000


class VectorizeIndex:
    """Implement ``VectorIndex`` with ``upsert`` and ``deleteByIds``."""

    def __init__(self, index: object):
        """Receive the index binding at composition time."""
        self._index = index

    async def upsert(self, vectors: Sequence[VectorRecord]) -> None:
        """Upsert up to 1000 vectors per call (Workers limit)."""
        for start in range(0, len(vectors), MAX_BATCH):
            await self._index.upsert(  # type: ignore[attr-defined]
                [
                    {"id": v.id, "values": list(v.values), "metadata": dict(v.metadata)}
                    for v in vectors[start : start + MAX_BATCH]
                ]
            )

    async def delete_by_ids(self, ids: Sequence[str]) -> None:
        """Delete vectors by id in batches of 1000."""
        for start in range(0, len(ids), MAX_BATCH):
            await self._index.deleteByIds(  # type: ignore[attr-defined]
                list(ids[start : start + MAX_BATCH])
            )

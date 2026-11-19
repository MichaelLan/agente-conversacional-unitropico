import asyncio

from unitropico_ingestion.infra.r2_store import R2BlobStore
from unitropico_ingestion.infra.vectorize_index import VectorizeIndex
from unitropico_ingestion.ports import VectorRecord


class FakeR2Object:
    """Test double: fake r2 object."""

    def __init__(self, data):
        """Store the configuration."""
        self._data = data

    async def arrayBuffer(self):  # noqa: N802 - JS API name
        """ArrayBuffer."""
        return memoryview(self._data)


class FakeBucket:
    """Test double: fake bucket."""

    def __init__(self):
        """Store the configuration."""
        self.objects, self.puts = {}, []

    async def get(self, key):
        """Mimic the runtime method of the same name."""
        return FakeR2Object(self.objects[key]) if key in self.objects else None

    async def head(self, key):
        """Mimic the runtime method of the same name."""
        return object() if key in self.objects else None

    async def put(self, key, data, **kwargs):
        """Mimic the runtime method of the same name."""
        self.puts.append((key, kwargs))
        self.objects[key] = bytes(data)


class FakeVectorizeBinding:
    """Test double: fake vectorize binding."""

    def __init__(self):
        """Store the configuration."""
        self.upserts, self.deletes = [], []

    async def upsert(self, vectors):
        """Mimic the runtime method of the same name."""
        self.upserts.append(vectors)

    async def deleteByIds(self, ids):  # noqa: N802 - JS API name
        """DeleteByIds."""
        self.deletes.append(ids)


def test_r2_store_round_trip():
    """R2 store round trip."""
    bucket = FakeBucket()
    store = R2BlobStore(bucket)

    async def flow():
        assert await store.get("k") is None and not await store.exists("k")
        await store.put("k", b"data", content_type="application/json")
        assert await store.get("k") == b"data" and await store.exists("k")

    asyncio.run(flow())
    assert bucket.puts == [("k", {"httpMetadata": {"contentType": "application/json"}})]


def test_vectorize_batches_of_1000():
    """Vectorize batches of 1000."""
    binding = FakeVectorizeBinding()
    index = VectorizeIndex(binding)
    vectors = [VectorRecord(f"id{i}", [0.1], {"version_id": "v"}) for i in range(2500)]

    async def flow():
        await index.upsert(vectors)
        await index.delete_by_ids([v.id for v in vectors])

    asyncio.run(flow())
    assert [len(b) for b in binding.upserts] == [1000, 1000, 500]
    assert binding.upserts[0][0] == {
        "id": "id0",
        "values": [0.1],
        "metadata": {"version_id": "v"},
    }
    assert [len(b) for b in binding.deletes] == [1000, 1000, 500]

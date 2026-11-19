import asyncio
import json

import pytest

from unitropico_ingestion.domain.ingestion import IngestionError
from unitropico_ingestion.infra.gateway_embeddings import GatewayEmbedder

URL = "https://ai-gateway.vercel.sh/v1/embeddings"


class FakeResponse:
    """Test double: fake response."""

    def __init__(self, status, payload):
        """Store the configuration."""
        self.status, self._payload = status, payload

    async def json(self):
        """Mimic the runtime method of the same name."""
        return self._payload


class FakeFetch:
    """Test double: fake fetch."""

    def __init__(self, response):
        """Store the configuration."""
        self.response, self.calls = response, []

    async def __call__(self, url, **kwargs):
        """Record the call and return the canned response."""
        self.calls.append((url, kwargs))
        return self.response


def _embedder(fetch, key="gw-key"):
    return GatewayEmbedder(
        api_key=key, fetch=fetch, model="openai/text-embedding-3-small", url=URL
    )


def test_sends_json_request_and_orders_by_index():
    """Sends json request and orders by index."""
    payload = {
        "data": [
            {"index": 1, "embedding": [0.3, 0.4]},
            {"index": 0, "embedding": [1, 2]},
        ]
    }
    fetch = FakeFetch(FakeResponse(200, payload))
    vectors = asyncio.run(_embedder(fetch).embed(["a", "b"]))
    assert vectors == [[1.0, 2.0], [0.3, 0.4]]
    url, kwargs = fetch.calls[0]
    assert url == URL and kwargs["method"] == "POST"
    assert kwargs["headers"]["Authorization"] == "Bearer gw-key"
    assert json.loads(kwargs["body"]) == {
        "model": "openai/text-embedding-3-small",
        "input": ["a", "b"],
    }


@pytest.mark.parametrize(
    ("status", "permanent"), [(400, True), (401, True), (429, False), (502, False)]
)
def test_maps_http_errors(status, permanent):
    """Maps http errors."""
    with pytest.raises(IngestionError) as info:
        asyncio.run(_embedder(FakeFetch(FakeResponse(status, {}))).embed(["a"]))
    assert info.value.code == "embedding_failed"
    assert info.value.permanent is permanent


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": []},
        {"data": [{"embedding": ["x"]}]},
        {"data": [{"embedding": [float("nan")]}]},
    ],
)
def test_rejects_malformed_payloads(payload):
    """Rejects malformed payloads."""
    with pytest.raises(IngestionError) as info:
        asyncio.run(_embedder(FakeFetch(FakeResponse(200, payload))).embed(["a"]))
    assert info.value.detail == "invalid_response" and info.value.permanent


def test_missing_key_and_too_many_inputs_fail_without_request():
    """Missing key and too many inputs fail without request."""
    fetch = FakeFetch(FakeResponse(200, {}))
    with pytest.raises(IngestionError, match="config_missing"):
        asyncio.run(_embedder(fetch, key=None).embed(["a"]))
    with pytest.raises(IngestionError, match="embedding_failed"):
        asyncio.run(_embedder(fetch).embed(["a"] * 97))
    assert fetch.calls == []

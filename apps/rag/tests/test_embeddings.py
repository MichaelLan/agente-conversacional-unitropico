"""GatewayEmbedder with a fake fetch transport (no network)."""

import asyncio
import json
import math

import pytest
from pydantic import SecretStr

from unitropico_rag.infra.embeddings import EmbeddingError, GatewayEmbedder

URL = "https://ai-gateway.vercel.sh/v1/embeddings"


def vector(seed: float, dims: int = 4) -> list[float]:
    """Deterministic test vector."""
    return [seed + n / 10 for n in range(dims)]


class Fetch:
    """Fake transport returning a canned payload."""

    def __init__(self, payload=None, status=200, error=None, delay=0.0):
        """Configure the response and record calls."""
        self.payload, self.status, self.error, self.delay = (
            payload,
            status,
            error,
            delay,
        )
        self.calls = []

    async def __call__(self, url, **kwargs):
        """Record the request and return self as response."""
        self.calls.append((url, kwargs))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self

    async def json(self):
        """Return the canned payload."""
        return self.payload


def embedder(fetch, dims=4, timeout=1.0):
    """Build an embedder with test configuration."""
    return GatewayEmbedder(
        model="openai/text-embedding-3-small",
        api_key=SecretStr("fake-key"),
        fetch=fetch,
        url=URL,
        timeout=timeout,
        dimensions=dims,
    )


def embed(fetch, texts, **kwargs):
    """Run embed synchronously."""
    return asyncio.run(embedder(fetch, **kwargs).embed(texts))


def test_request_and_ordered_vectors():
    """One POST with model and inputs; vectors reordered by index."""
    fetch = Fetch(
        {
            "data": [
                {"index": 1, "embedding": vector(2)},
                {"index": 0, "embedding": [0, 1, 2, 3]},
            ]
        }
    )
    assert embed(fetch, ["uno", "dos"]) == [[0.0, 1.0, 2.0, 3.0], vector(2)]
    url, kwargs = fetch.calls[0]
    assert url == URL and kwargs["method"] == "POST"
    assert kwargs["redirect"] == "manual"
    assert kwargs["headers"]["Authorization"] == "Bearer fake-key"
    assert json.loads(kwargs["body"]) == {
        "model": "openai/text-embedding-3-small",
        "input": ["uno", "dos"],
    }


def test_missing_index_uses_position():
    """Without index fields the response order is used."""
    fetch = Fetch({"data": [{"embedding": vector(1)}, {"embedding": vector(2)}]})
    assert embed(fetch, ["a", "b"]) == [vector(1), vector(2)]


def test_default_dimensions_1536():
    """The production length is accepted exactly."""
    fetch = Fetch({"data": [{"embedding": [0.1] * 1536}]})
    assert len(embed(fetch, ["a"], dims=1536)[0]) == 1536


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"data": None},
        {"data": []},
        {"data": [{"embedding": vector(1)}, {"embedding": vector(2)}]},
        {"data": ["x"]},
        {"data": [{"embedding": vector(1, 3)}]},
        {"data": [{"embedding": vector(1, 5)}]},
        {"data": [{"embedding": [0.1, 0.2, math.nan, 0.3]}]},
        {"data": [{"embedding": [0.1, 0.2, math.inf, 0.3]}]},
        {"data": [{"embedding": [0.1, 0.2, "0.3", 0.4]}]},
        {"data": [{"embedding": [0.1, 0.2, True, 0.4]}]},
        {"data": [{"index": 1, "embedding": vector(1)}]},
        {"data": [{"index": "0", "embedding": vector(1)}]},
    ],
)
def test_invalid_responses(payload, caplog):
    """Count, length, finiteness and index are validated strictly."""
    with pytest.raises(EmbeddingError) as caught:
        embed(Fetch(payload), ["a"])
    assert caught.value.code == "invalid_response"
    assert "fake-key" not in caplog.text


def test_duplicate_index_rejected():
    """Two vectors claiming the same input are rejected."""
    payload = {"data": [{"index": 0, "embedding": vector(1)}] * 2}
    with pytest.raises(EmbeddingError):
        embed(Fetch(payload), ["a", "b"])


@pytest.mark.parametrize(
    "fetch,code",
    [
        (Fetch(status=500), "http_error"),
        (Fetch(status=302), "http_error"),
        (Fetch(error=OSError("SECRET")), "transport_error"),
        (Fetch({"data": []}, delay=0.2), "timeout"),
    ],
)
def test_transport_failures(fetch, code, caplog):
    """Failures map to controlled codes; no retries, no content in logs."""
    with pytest.raises(EmbeddingError) as caught:
        embed(fetch, ["pregunta SECRET"], timeout=0.05)
    assert caught.value.code == code
    assert caught.value.__cause__ is None
    assert len(fetch.calls) == 1
    assert "SECRET" not in caplog.text


@pytest.mark.parametrize("texts", ["texto", [], [""], ["  "], [1], ["a"] * 101])
def test_invalid_input_without_io(texts):
    """Invalid inputs are rejected before calling the provider."""
    fetch = Fetch()
    with pytest.raises(EmbeddingError) as caught:
        embed(fetch, texts)
    assert caught.value.code == "invalid_input" and fetch.calls == []


@pytest.mark.parametrize(
    "changes",
    [
        {"model": " "},
        {"api_key": SecretStr("")},
        {"url": "http://insecure"},
        {"timeout": 0},
        {"dimensions": 0},
        {"dimensions": 1537},
        {"fetch": None},
    ],
)
def test_invalid_config(changes):
    """Configuration is validated at construction."""
    kwargs = {
        "model": "m",
        "api_key": SecretStr("k"),
        "fetch": Fetch(),
        "url": URL,
        "timeout": 1.0,
        "dimensions": 4,
    } | changes
    with pytest.raises(EmbeddingError):
        GatewayEmbedder(**kwargs)

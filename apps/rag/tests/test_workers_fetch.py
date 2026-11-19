import asyncio
import json

import pytest
from pydantic import SecretStr

from unitropico_rag.domain.generation import GeneratedText, GenerationError
from unitropico_rag.infra.gateway import GatewayClient
from unitropico_rag.infra.workers_fetch import FetchTransportError, WorkersFetch

GOOD = {
    "choices": [
        {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ]
}


class JsException(Exception):
    """Stand-in for Pyodide's JsException, which is not importable on host."""


class FakeSdkResponse:
    """Mimic workers.Response: status attribute and async json()."""

    def __init__(self, status=200, payload=GOOD, json_error=None):
        """Configure the canned response."""
        self.status, self.payload, self.json_error = status, payload, json_error

    async def json(self):
        """Return the payload or raise a runtime-specific error."""
        if self.json_error:
            raise self.json_error
        return self.payload


class FakeSdkFetch:
    """Mimic ``workers.fetch(resource, **options)`` without any network."""

    def __init__(self, response=None, error=None):
        """Configure the response or error and record calls."""
        self.response, self.error, self.calls = response, error, []

    async def __call__(self, resource, **options):
        """Record the SDK-shaped call."""
        self.calls.append((resource, options))
        if self.error:
            raise self.error
        return self.response or FakeSdkResponse()


def call(adapter):
    """Invoke the adapter with the gateway's keyword shape."""
    return asyncio.run(
        adapter(
            "https://gateway.test",
            method="POST",
            headers={"A": "b"},
            body="{}",
            redirect="manual",
        )
    )


def test_forwards_options_and_decodes_json():
    """Forward the exact options and expose int status plus async json()."""
    sdk = FakeSdkFetch()
    response = call(WorkersFetch(sdk))
    assert response.status == 200
    assert asyncio.run(response.json()) == GOOD
    assert sdk.calls == [
        (
            "https://gateway.test",
            {
                "method": "POST",
                "headers": {"A": "b"},
                "body": "{}",
                "redirect": "manual",
            },
        )
    ]


def test_runtime_errors_become_controlled_types():
    """JS-side failures become OSError/ValueError without leaking messages."""
    with pytest.raises(FetchTransportError) as caught:
        call(WorkersFetch(FakeSdkFetch(error=JsException("SECRET"))))
    assert str(caught.value) == "JsException" and caught.value.__cause__ is None
    response = call(
        WorkersFetch(FakeSdkFetch(FakeSdkResponse(json_error=JsException("SECRET"))))
    )
    with pytest.raises(ValueError) as caught:
        asyncio.run(response.json())
    assert "SECRET" not in str(caught.value)


def test_non_integer_status_is_normalized():
    """A non-int status cannot be mistaken for success."""
    assert call(WorkersFetch(FakeSdkFetch(FakeSdkResponse(status="200")))).status == -1


@pytest.mark.parametrize(
    "sdk,code",
    [
        (FakeSdkFetch(), None),
        (FakeSdkFetch(error=JsException("SECRET")), "transport_error"),
        (
            FakeSdkFetch(FakeSdkResponse(json_error=JsException("SECRET"))),
            "invalid_response",
        ),
        (FakeSdkFetch(FakeSdkResponse(status=500)), "http_error"),
    ],
)
def test_gateway_over_adapter(sdk, code):
    """GatewayClient classifies adapter outcomes into controlled codes."""
    gateway = GatewayClient(
        model="m",
        api_key=SecretStr("k"),
        fetch=WorkersFetch(sdk),
        url="https://gateway.test",
        timeout=5,
        max_tokens=100,
        max_input_chars=1000,
        reasoning_effort="low",
    )
    if code is None:
        result = asyncio.run(gateway.complete("s", "u"))
        assert result == GeneratedText("ok")
        assert json.loads(sdk.calls[0][1]["body"])["model"] == "m"
        return
    with pytest.raises(GenerationError) as caught:
        asyncio.run(gateway.complete("s", "u"))
    assert caught.value.code == code

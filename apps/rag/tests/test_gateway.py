import asyncio
import json
from dataclasses import FrozenInstanceError

import pytest
from pydantic import SecretStr

from unitropico_rag.domain.generation import GeneratedText, GenerationError, TokenUsage
from unitropico_rag.infra.gateway import GatewayClient

SECRET = "fake-secret-never-live"
GOOD = {
    "choices": [
        {
            "message": {"role": "assistant", "content": " Candidato á "},
            "finish_reason": "stop",
        }
    ]
}


class Fetch:
    """Emulate only the documented asynchronous Workers fetch contract."""

    def __init__(self, payload=GOOD, status=200, failure=None, delay=0):
        """Configure one isolated response and record calls."""
        self.payload, self.status, self.failure, self.delay = (
            payload,
            status,
            failure,
            delay,
        )
        self.calls = []

    async def __call__(self, url, **kwargs):
        """Record SDK-shaped arguments without performing I/O."""
        self.calls.append((url, kwargs))
        await asyncio.sleep(self.delay)
        if self.failure == "network":
            raise OSError(SECRET)
        return self

    async def json(self):
        """Return Python JSON data or a deliberately sensitive parse failure."""
        if self.failure == "json":
            raise ValueError(SECRET)
        return self.payload


def client(adapter, **kwargs):
    """Construct a client using explicit test-only credentials and model."""
    config = {"model": "caller/model", "api_key": SecretStr(SECRET), "fetch": adapter}
    return GatewayClient(**(config | kwargs))


def complete(instance, system="Sistema", user='Consulta "á"\n'):
    """Exercise the public async port without an async pytest plugin."""
    return asyncio.run(instance.complete(system, user))


def assert_error(action, code, attempted, fetch):
    """Check safe error metadata without leaking an upstream exception chain."""
    with pytest.raises(GenerationError) as caught:
        action()
    error = caught.value
    assert str(error) == code
    assert error.code == code and error.request_attempted is attempted
    assert error.__cause__ is None and error.__context__ is None
    assert SECRET not in repr(error)
    assert len(fetch.calls) == int(attempted)


def test_request_and_immutable_candidate():
    """Preserve literal inputs, fixed roles and the documented fetch signature."""
    fetch = Fetch()
    result = complete(client(fetch))
    assert result == GeneratedText(" Candidato á ", None)
    with pytest.raises(FrozenInstanceError):
        result.content = "changed"
    assert len(fetch.calls) == 1
    url, kwargs = fetch.calls[0]
    assert url == "https://ai-gateway.vercel.sh/v1/chat/completions"
    assert set(kwargs) == {"method", "headers", "body", "redirect"}
    assert kwargs["method"] == "POST" and kwargs["redirect"] == "error"
    assert kwargs["headers"] == {
        "Authorization": f"Bearer {SECRET}",
        "Content-Type": "application/json",
    }
    assert json.loads(kwargs["body"]) == {
        "model": "caller/model",
        "max_tokens": 512,
        "stream": False,
        "messages": [
            {"role": "system", "content": "Sistema"},
            {"role": "user", "content": 'Consulta "á"\n'},
        ],
    }


@pytest.mark.parametrize(
    "config",
    [
        {"model": None},
        {"model": " "},
        {"model": 1},
        {"api_key": None},
        {"api_key": SECRET},
        {"api_key": SecretStr(" ")},
        {"timeout": True},
        {"timeout": 0},
        {"timeout": 61},
        {"timeout": float("nan")},
        {"max_tokens": True},
        {"max_tokens": 0},
        {"max_tokens": 2049},
        {"fetch": None},
    ],
)
def test_invalid_config(config):
    """Reject invalid explicit configuration before any fetch."""
    fetch = Fetch()
    assert_error(lambda: client(fetch, **config), "invalid_config", False, fetch)


@pytest.mark.parametrize(
    "system,user", [(None, "x"), ("x", 1), (" ", "x"), ("x", ""), ("s", "x" * 20000)]
)
def test_invalid_input(system, user):
    """Reject invalid or oversized prompts before attempting a request."""
    fetch = Fetch()
    assert_error(
        lambda: complete(client(fetch), system, user), "invalid_input", False, fetch
    )


@pytest.mark.parametrize("status", [True, "200", 201, 204, 302, 401, 429, 500])
def test_status(status):
    """Reject all but an integer 200 without retrying or reading an error body."""
    fetch = Fetch(status=status, failure="json")
    assert_error(lambda: complete(client(fetch)), "http_error", True, fetch)


@pytest.mark.parametrize(
    "failure,code",
    [
        ("network", "transport_error"),
        ("json", "invalid_response"),
        ("timeout", "timeout"),
    ],
)
def test_transport(failure, code):
    """Sanitize network, JSON and native deadline failures without retries."""
    fetch = Fetch(failure=failure, delay=0.02 if failure == "timeout" else 0)
    assert_error(lambda: complete(client(fetch, timeout=0.001)), code, True, fetch)


@pytest.mark.parametrize(
    "payload", [None, {}, {"choices": []}, {"choices": GOOD["choices"] * 2}]
)
def test_invalid_candidate(payload):
    """Never return partial, refused, malformed or tool-directed generations."""
    fetch = Fetch(payload)
    assert_error(lambda: complete(client(fetch)), "invalid_response", True, fetch)


@pytest.mark.parametrize(
    "field,value",
    [
        ("role", "user"),
        ("content", None),
        ("content", " "),
        ("content", 1),
        ("finish_reason", "length"),
        ("finish_reason", "tool_calls"),
        ("finish_reason", None),
        ("refusal", "no"),
        ("tool_calls", [{}]),
        ("function_call", {}),
    ],
)
def test_invalid_message(field, value):
    """Reject incomplete, refused and tool-directed message candidates."""
    payload = json.loads(json.dumps(GOOD))
    choice = payload["choices"][0]
    target = choice if field == "finish_reason" else choice["message"]
    target[field] = value
    test_invalid_candidate(payload)


@pytest.mark.parametrize(
    "usage,expected",
    [
        (None, None),
        ({}, TokenUsage()),
        (
            {"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2},
            TokenUsage(2, 0, 2),
        ),
        ({"prompt_tokens": 2, "vendor_field": 7}, TokenUsage(2)),
    ],
)
def test_usage(usage, expected):
    """Keep absent counts unknown and accept only reported token counts."""
    assert complete(client(Fetch(GOOD | {"usage": usage}))).usage == expected


@pytest.mark.parametrize(
    "usage",
    [
        True,
        [],
        {"total_tokens": True},
        {"prompt_tokens": -1},
        {"completion_tokens": 1.5},
    ],
)
def test_invalid_usage(usage):
    """Fail safely for malformed accounting rather than inventing token counts."""
    test_invalid_candidate(GOOD | {"usage": usage})


def test_bounds_and_alias():
    """Accept boundary inputs and ignore provider model aliases in responses."""
    fetch = Fetch(GOOD | {"model": "provider-alias"})
    assert complete(
        client(fetch, timeout=60, max_tokens=2048), "s", "x" * 19999
    ).content
    assert json.loads(fetch.calls[0][1]["body"])["max_tokens"] == 2048

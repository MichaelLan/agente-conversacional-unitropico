import asyncio
import json

import pytest
from test_app import request
from test_retrieval import EMPTY, EVIDENCE, Client, post

from unitropico_api.domain.retrieval import MalformedUpstream
from unitropico_api.infra.service_binding import RagServiceBindingClient
from unitropico_api.main import create_app


class Binding:
    """Record native async fetch calls without network or runtime SDKs."""

    def __init__(self, status=200, payload=None, error=None, decode_error=None):
        """Configure the response and retain public call counters."""
        self.status = status
        self.payload = payload
        self.error = error
        self.decode_error = decode_error
        self.calls = []
        self.reads = 0

    async def fetch(self, *args, **kwargs):
        """Record the exact transport request and emulate asynchronous failure."""
        self.calls.append((args, kwargs))
        await asyncio.sleep(0)
        if self.error:
            raise self.error
        return self

    async def json(self):
        """Return decoded Python data or a controlled decoding error."""
        self.reads += 1
        if self.decode_error:
            raise self.decode_error
        return self.payload


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"status": "evidence_found", "evidence": [EVIDENCE]}, 200),
        ({"status": "abstained", "evidence": []}, 200),
        (EMPTY, 503),
        (None, 503),
        ({"status": "evidence_found", "evidence": []}, 502),
        ({"status": True, "evidence": []}, 502),
        ({"status": "abstained", "evidence": [EVIDENCE]}, 502),
        ({"status": "abstained", "evidence": [], "answer": "secret"}, 502),
    ],
)
def test_binding_contract(payload, expected, caplog):
    """Exercise exact serialization and coordinator validation via HTTP ASGI."""
    binding = Binding(payload=payload)
    app = create_app(rag_client=RagServiceBindingClient(binding))
    body = payload if expected == 200 else EMPTY
    assert post(app, {"question": " private question ", "limit": 2}) == (expected, body)
    assert binding.calls == [
        (
            ("https://rag.internal/retrieval/query",),
            {
                "method": "POST",
                "headers": {"Content-Type": "application/json"},
                "body": '{"question":"private question","limit":2}',
            },
        )
    ]
    assert binding.reads == 1
    assert "private question" not in caplog.text
    assert EVIDENCE["text"] not in caplog.text
    assert "secret" not in caplog.text


@pytest.mark.parametrize("status", [199, 300, 404, 503, True, "200", None])
def test_non_success_never_reads_body(status, caplog):
    """Reject non-2xx or noninteger HTTP statuses without decoding diagnostics."""
    binding = Binding(status=status, decode_error=ValueError("secret"))
    assert post(
        create_app(rag_client=RagServiceBindingClient(binding)),
        {"question": "private question"},
    ) == (503, EMPTY)
    assert len(binding.calls) == 1
    assert binding.reads == 0
    assert "secret" not in caplog.text
    assert "private question" not in caplog.text


@pytest.mark.parametrize("error", [OSError("secret"), TimeoutError("secret")])
def test_transport_failure_is_unavailable(error, caplog):
    """Fail closed without retries or leaking transport exception messages."""
    binding = Binding(error=error)
    assert post(
        create_app(rag_client=RagServiceBindingClient(binding)),
        {"question": "private question"},
    ) == (503, EMPTY)
    assert len(binding.calls) == 1
    assert binding.reads == 0
    assert "secret" not in caplog.text
    assert "private question" not in caplog.text


@pytest.mark.parametrize("status", [201, 299])
def test_success_status_boundaries(status):
    """Accept the full 2xx range while retaining upstream schema validation."""
    payload = {"status": "abstained", "evidence": []}
    binding = Binding(status=status, payload=payload)
    assert post(
        create_app(rag_client=RagServiceBindingClient(binding)), {"question": "x"}
    ) == (200, payload)
    assert len(binding.calls) == binding.reads == 1


def test_rejected_requests_do_not_call_binding():
    """Preserve input validation and POST-only routing before transport."""
    binding = Binding()
    app = create_app(rag_client=RagServiceBindingClient(binding))
    assert post(app, {"question": " "})[0] == 422
    assert request(app, "/retrieval/query")[0]["status"] == 405
    assert binding.calls == []
    assert binding.reads == 0


def test_native_timeout(monkeypatch):
    """Expire a real asyncio deadline deterministically without waiting ten seconds."""
    timeout = asyncio.timeout
    deadlines = []

    def expire(delay):
        """Record the production deadline but schedule immediate cancellation."""
        deadlines.append(delay)
        return timeout(0)

    monkeypatch.setattr(asyncio, "timeout", expire)
    binding = Binding()
    assert post(
        create_app(rag_client=RagServiceBindingClient(binding)),
        {"question": "private question"},
    ) == (503, EMPTY)
    assert deadlines == [10]
    assert len(binding.calls) == 1
    assert binding.reads == 0


def test_decode_failure_is_bad_gateway(caplog):
    """Keep malformed JSON separate from dependency unavailability."""
    binding = Binding(decode_error=json.JSONDecodeError("secret", "secret", 0))
    assert post(
        create_app(rag_client=RagServiceBindingClient(binding)),
        {"question": "private question"},
    ) == (502, EMPTY)
    assert len(binding.calls) == binding.reads == 1
    assert "secret" not in caplog.text
    assert "private question" not in caplog.text


def test_existing_port_propagates_malformed_upstream():
    """Preserve explicit malformed classification for any injected async client."""
    client = Client(MalformedUpstream())
    assert post(create_app(rag_client=client), {"question": "x"}) == (502, EMPTY)
    assert client.calls == [{"question": "x", "limit": 5}]

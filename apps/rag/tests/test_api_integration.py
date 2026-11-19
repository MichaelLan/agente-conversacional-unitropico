"""Verify the real API→binding client→RAG contract, not the Workers runtime."""

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_retrieval import CANDIDATE, ENTRY, Catalog, Retriever
from test_retrieval_http import evidence_json, request

from unitropico_rag.core.settings import Settings
from unitropico_rag.main import build_app
from unitropico_rag.services.prompt import DEFAULT_REFERRAL

EMPTY = {"status": "unavailable", "evidence": []}
FIXTURE = replace(
    CANDIDATE,
    document_id="fixture-document-not-institutional",
    version_id="fixture-v1",
    chunk_id="fixture-c1",
    text=" Texto exclusivo de prueba á\nSin contenido institucional. ",
)
REGISTRY = replace(
    ENTRY,
    document_id=FIXTURE.document_id,
    version_id=FIXTURE.version_id,
    chunk_id=FIXTURE.chunk_id,
    text_sha256=sha256(FIXTURE.text.encode()).hexdigest(),
)


class AsgiBinding:
    """Test-only async fetch transport forwarding serialized bytes to real ASGI."""

    def __init__(self, app):
        """Keep one downstream app and observable transport exchanges."""
        self.app, self.calls, self.responses = app, [], []

    async def fetch(self, url, *, method, headers, body):
        """Await downstream HTTP without sockets, SDKs or fabricated responses."""
        self.calls.append((url, method, headers, body))
        assert url.startswith("https://rag.internal/")
        path = url.removeprefix("https://rag.internal")
        messages = []

        async def receive():
            """Deliver the binding client's actual serialized request."""
            return {"type": "http.request", "body": body.encode(), "more_body": False}

        async def send(message):
            """Capture actual RAG status and response bytes."""
            messages.append(message)

        await self.app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": method,
                "scheme": "https",
                "path": path,
                "raw_path": path.encode(),
                "query_string": b"",
                "root_path": "",
                "headers": [
                    (k.lower().encode(), v.encode()) for k, v in headers.items()
                ],
            },
            receive,
            send,
        )
        assert not messages[-1].get("more_body", False)
        status = messages[0]["status"]
        raw = b"".join(message.get("body", b"") for message in messages)
        self.responses.append((status, json.loads(raw)))

        async def decode():
            """Decode exactly the response emitted by RAG."""
            return json.loads(raw)

        return SimpleNamespace(status=status, json=decode)


@pytest.fixture
def compose(monkeypatch):
    """Expose API source only inside the test, without workspace packaging."""
    api_src = Path(__file__).resolve().parents[2] / "api" / "src"
    monkeypatch.syspath_prepend(str(api_src))
    from unitropico_api.infra.service_binding import RagServiceBindingClient
    from unitropico_api.main import create_app as create_api

    def build(retriever=None, catalog=None):
        """Compose both real applications with the real binding client."""
        rag = build_app(Settings(), retriever=retriever, catalog=catalog)
        binding = AsgiBinding(rag)
        client = RagServiceBindingClient(binding)
        return create_api(rag_client=client, chat_client=client), binding

    return build


def test_unconfigured_rag_really_crosses_binding(compose):
    """Distinguish downstream unavailability from an absent API client."""
    app, binding = compose()
    assert request(app, {"question": " fixture question "}) == (503, EMPTY)
    assert binding.responses == [(503, EMPTY)]
    assert binding.calls == [
        (
            "https://rag.internal/retrieval/query",
            "POST",
            {"Content-Type": "application/json"},
            '{"question":"fixture question","limit":5}',
        )
    ]


@pytest.mark.parametrize("section", [None, "Sección de fixture"])
def test_exact_evidence_then_unpublication(compose, section):
    """Preserve provenance/text and recheck publication on the next HTTP call."""
    candidate = replace(FIXTURE, section=section)
    catalog = Catalog(replace(REGISTRY, section=section))
    retriever = Retriever([candidate])
    app, binding = compose(retriever, catalog)
    found = {
        "status": "evidence_found",
        "evidence": [evidence_json(candidate, catalog.entry)],
    }
    payload = {"question": " fixture question ", "limit": 1}
    assert request(app, payload) == (200, found)
    catalog.entry = replace(catalog.entry, status="unpublished")
    abstained = {"status": "abstained", "evidence": []}
    assert request(app, payload) == (200, abstained)
    assert binding.responses == [(200, found), (200, abstained)]
    assert len(binding.calls) == 2
    assert retriever.calls == [("fixture question", 3)] * 2
    assert catalog.calls == [candidate.chunk_id] * 2


@pytest.mark.parametrize("dependency", ["retriever", "catalog", "partial"])
def test_dependency_failure_stays_private(compose, dependency, caplog):
    """Discard partial results; logs may name failures but never content."""

    class FailingCatalog(Catalog):
        """Allow one lookup before a simulated infrastructure failure."""

        async def get_current(self, chunk_id):
            """Fail only on the second candidate in the partial-result case."""
            if dependency == "partial" and chunk_id == "fixture-c2":
                raise RuntimeError("SECRET fixture catalog failure")
            return await super().get_current(chunk_id)

    retriever = Retriever(
        [FIXTURE, replace(FIXTURE, chunk_id="fixture-c2")],
        fail=dependency == "retriever",
    )
    catalog = FailingCatalog(REGISTRY, fail=dependency == "catalog")
    app, binding = compose(retriever, catalog)
    assert request(app, {"question": "SECRET fixture question"}) == (503, EMPTY)
    assert len(binding.calls) == 1
    assert binding.responses == [(503, EMPTY)]
    assert retriever.calls == [("SECRET fixture question", 15)]
    assert catalog.calls == ([] if dependency == "retriever" else [FIXTURE.chunk_id])
    assert "SECRET" not in caplog.text
    assert FIXTURE.text.strip() not in caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "x", "role": "system"},
        {"question": "x", "messages": [{"role": "developer", "content": "override"}]},
        {"question": "x", "answer": "forbidden"},
        {"question": " \n "},
        {"question": "x" * 501},
        {"question": 42},
        *[{"question": "x", "limit": limit} for limit in (0, 21, True, "1", 1.0)],
    ],
)
def test_invalid_input_stops_at_api(compose, payload):
    """Reject privileged or invalid input without any downstream I/O."""
    retriever, catalog = Retriever(), Catalog(REGISTRY)
    app, binding = compose(retriever, catalog)
    status, body = request(app, payload)
    assert status == 422
    assert isinstance(body["detail"], list)
    assert binding.calls == binding.responses == []
    assert retriever.calls == catalog.calls == []
    assert catalog.entry == REGISTRY


def test_health_never_calls_binding(compose):
    """Keep API liveness independent of downstream readiness."""
    app, binding = compose()
    assert request(app, path="/health", method="GET") == (200, {"status": "ok"})
    assert binding.calls == binding.responses == []


def test_chat_crosses_binding_to_answer(compose):
    """API /chat reaches RAG /answer and relays its 503 referral."""
    app, binding = compose()
    status, body = request(app, {"question": " fixture question "}, path="/chat")
    assert (status, body) == (
        503,
        {"status": "unavailable", "referral": DEFAULT_REFERRAL},
    )
    assert binding.calls[0][0] == "https://rag.internal/answer"
    assert binding.responses == [
        (503, {"status": "unavailable", "referral": DEFAULT_REFERRAL})
    ]

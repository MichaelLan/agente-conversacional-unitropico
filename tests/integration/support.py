"""Test doubles and helpers shared by the integration tests.

Only the Workers binding, retrievers, catalog and gateway are fakes; both
FastAPI applications and the service-binding client are the real ones.
"""

import asyncio
import json
from datetime import date
from hashlib import sha256
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from pydantic import SecretStr
from unitropico_api.infra.service_binding import RagServiceBindingClient
from unitropico_api.main import create_app as create_api
from unitropico_rag.core.settings import Settings
from unitropico_rag.domain.evidence import ChunkCandidate, RegistryEntry
from unitropico_rag.main import Bindings, build_app

CANDIDATE = ChunkCandidate(
    document_id="doc",
    version_id="v1",
    chunk_id="c1",
    source_sha256="a" * 64,
    page_start=2,
    page_end=3,
    section=None,
    text="Texto de prueba sobre apoyos á\n",
)
ENTRY = RegistryEntry(
    document_id="doc",
    version_id="v1",
    chunk_id="c1",
    source_sha256="a" * 64,
    page_start=2,
    page_end=3,
    section=None,
    text_sha256=sha256(CANDIDATE.text.encode("utf-8")).hexdigest(),
    status="published",
    requires_review=False,
    formally_superseded=False,
    document_title="Reglamento sintético de prueba",
    version_label="2026-1",
    validity_label="Vigente durante 2026-1",
    effective_from=date(2026, 2, 1),
    effective_until=date(2026, 6, 30),
)


class Retriever:
    """Return test candidates and record the public retrieval inputs."""

    def __init__(self, candidates=(), fail=False):
        """Initialize an isolated test adapter."""
        self.candidates, self.fail, self.calls = candidates, fail, []

    async def retrieve(self, question, limit):
        """Record input or simulate an infrastructure failure."""
        self.calls.append((question, limit))
        if self.fail:
            raise RuntimeError("SECRET retrieval content")
        return self.candidates


class Catalog:
    """Expose mutable current metadata only inside tests."""

    def __init__(self, entry=ENTRY, fail=False, entries=None):
        """Initialize current publication state and lookup recording."""
        self.entry, self.fail, self.calls = entry, fail, []
        self.entries = entries or {}

    async def get_current(self, chunk_id):
        """Look up current state without caching."""
        self.calls.append(chunk_id)
        if self.fail:
            raise RuntimeError("SECRET catalog content")
        return self.entries.get(chunk_id, self.entry)


def evidence_json(candidate=CANDIDATE, entry=ENTRY) -> dict[str, Any]:
    """Expected HTTP evidence item for a candidate and its catalog entry."""
    return {
        "document_id": candidate.document_id,
        "version_id": candidate.version_id,
        "chunk_id": candidate.chunk_id,
        "source_sha256": candidate.source_sha256,
        "page_start": candidate.page_start,
        "page_end": candidate.page_end,
        "section": candidate.section,
        "document_title": entry.document_title,
        "version_label": entry.version_label,
        "text": candidate.text,
    }


def request(
    app: FastAPI,
    payload: object = None,
    path: str = "/retrieval/query",
    method: str = "POST",
) -> tuple[int, dict[str, Any]]:
    """Observe an HTTP exchange through ASGI without external transport."""
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        """Supply the JSON request body."""
        return {
            "type": "http.request",
            "body": json.dumps(payload).encode(),
            "more_body": False,
        }

    async def send(message: dict[str, Any]) -> None:
        """Collect the response status, headers and body."""
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json")],
        "server": ("localhost", 80),
        "client": ("127.0.0.1", 1),
    }
    asyncio.run(app(scope, receive, send))
    assert (b"content-type", b"application/json") in messages[0]["headers"]
    return messages[0]["status"], json.loads(
        b"".join(message.get("body", b"") for message in messages)
    )


class AsgiBinding:
    """Test-only async fetch transport forwarding serialized bytes to real ASGI."""

    def __init__(self, app, tamper=None):
        """Keep one downstream app; ``tamper`` may rewrite the decoded response."""
        self.app, self.tamper = app, tamper
        self.calls, self.responses = [], []

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
        payload = json.loads(b"".join(m.get("body", b"") for m in messages))
        self.responses.append((status, payload))
        if self.tamper:
            payload = self.tamper(payload)

        async def decode():
            """Decode the (possibly tampered) response emitted by RAG."""
            return payload

        return SimpleNamespace(status=status, json=decode)


def compose_api(rag_app):
    """Compose the real API over the real binding client and a RAG app."""

    def wire(tamper=None):
        binding = AsgiBinding(rag_app, tamper)
        client = RagServiceBindingClient(binding)
        return create_api(rag_client=client, chat_client=client), binding

    return wire


class GatewayFetch:
    """Fake gateway transport returning a canned chat completion."""

    def __init__(self, content):
        """Configure the model content and record requests."""
        self.content, self.status, self.calls = content, 200, []

    async def __call__(self, url, **kwargs):
        """Record the request and return this object as the response."""
        self.calls.append((url, kwargs))
        return self

    async def json(self):
        """Return an OpenAI-compatible completion wrapping the content."""
        message = {"role": "assistant", "content": json.dumps(self.content)}
        return {"choices": [{"message": message, "finish_reason": "stop"}]}


def lexical_rag(db, fetch):
    """Build the real RAG app over D1 (lexical mode) with a fake gateway."""
    settings = Settings(
        ai_gateway_api_key=SecretStr("fake-key"), retrieval_mode="lexical"
    )
    return build_app(settings, fetch=fetch, bindings=Bindings(db, None))

import asyncio
import json
from dataclasses import replace
from typing import Any

import pytest
from fastapi import FastAPI
from test_retrieval import CANDIDATE, ENTRY, Catalog, Retriever

from unitropico_rag.core.settings import Settings
from unitropico_rag.main import build_app


def make_app(retriever=None, catalog=None) -> FastAPI:
    """Compose the real app with test adapters and no generator."""
    return build_app(Settings(), retriever=retriever, catalog=catalog)


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


def test_default_unavailable() -> None:
    """Report unconfigured retrieval explicitly without a fake answer."""
    assert request(make_app(), {"question": "apoyos"}) == (
        503,
        {"status": "unavailable", "evidence": []},
    )


@pytest.mark.parametrize("question,limit", [(" x ", 1), (" " + "x" * 500, 20)])
def test_empty_and_boundaries(question: str, limit: int) -> None:
    """Normalize bounded requests and distinguish empty operational retrieval."""
    retriever, catalog = Retriever(), Catalog()
    app = make_app(retriever, catalog)
    assert request(app, {"question": question, "limit": limit}) == (
        200,
        {"status": "abstained", "evidence": []},
    )
    assert retriever.calls == [(question.strip(), limit * 3)]
    assert catalog.calls == []


@pytest.mark.parametrize("section", [None, "Sección real"])
def test_exact_evidence_and_current_eligibility(section: str | None) -> None:
    """Expose exact physical provenance and recheck current publication each time."""
    candidate = replace(CANDIDATE, section=section)
    retriever = Retriever([candidate])
    catalog = Catalog(replace(ENTRY, section=section))
    app = make_app(retriever, catalog)
    assert request(app, {"question": "  apoyos  "}) == (
        200,
        {
            "status": "evidence_found",
            "evidence": [evidence_json(candidate, catalog.entry)],
        },
    )
    assert retriever.calls == [("apoyos", 15)]
    for changes in ({"status": "unpublished"}, {"requires_review": True}):
        catalog.entry = replace(ENTRY, section=section, **changes)
        assert request(app, {"question": "apoyos"}) == (
            200,
            {"status": "abstained", "evidence": []},
        )
    assert catalog.calls == ["c1", "c1", "c1"]


@pytest.mark.parametrize("dependency", ["retriever", "catalog", "partial"])
def test_infrastructure_failure_is_private(dependency, caplog) -> None:
    """Return 503 without partial text; logs carry no question or content."""

    class FailingCatalog(Catalog):
        """Fail after one eligible candidate when testing partial results."""

        async def get_current(self, chunk_id):
            """Simulate an outage on the second chunk only."""
            if dependency == "partial" and chunk_id == "c2":
                raise RuntimeError("SECRET catalog content")
            return await super().get_current(chunk_id)

    retriever = Retriever(
        [CANDIDATE, replace(CANDIDATE, chunk_id="c2")],
        fail=dependency == "retriever",
    )
    catalog = FailingCatalog(fail=dependency == "catalog")
    app = make_app(retriever, catalog)
    assert request(app, {"question": "SECRET question"}) == (
        503,
        {"status": "unavailable", "evidence": []},
    )
    assert "retrieval_failed" in caplog.text
    assert "SECRET" not in caplog.text and CANDIDATE.text not in caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"question": ""},
        {"question": " \t\n"},
        {"question": "x" * 501},
        {"question": 42},
        {"question": None},
        {"question": "x", "limit": 0},
        {"question": "x", "limit": 21},
        {"question": "x", "limit": True},
        {"question": "x", "limit": "1"},
        {"question": "x", "limit": 1.0},
        {"question": "x", "answer": "forbidden"},
    ],
)
def test_invalid_input_never_calls_adapters(payload: object) -> None:
    """Reject invalid contracts without touching adapters or subsequent requests."""
    retriever, catalog = Retriever(), Catalog()
    app = make_app(retriever, catalog)
    status, body = request(app, payload)
    assert status == 422
    assert isinstance(body["detail"], list)
    assert retriever.calls == catalog.calls == []
    assert request(app, {"question": "ok"}) == (
        200,
        {"status": "abstained", "evidence": []},
    )
    assert retriever.calls == [("ok", 15)]
    assert catalog.calls == []


def test_health_and_removed_routes() -> None:
    """Keep liveness; the PoC route and /chat do not exist in the RAG."""
    app = make_app()
    assert request(app, path="/health", method="GET") == (200, {"status": "ok"})
    for path in ("/poc/query", "/chat"):
        assert request(app, {"question": "x"}, path=path) == (
            404,
            {"detail": "Not Found"},
        )

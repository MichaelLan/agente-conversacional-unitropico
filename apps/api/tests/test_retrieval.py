import asyncio
import json

import pytest

from unitropico_api.main import create_app

EMPTY = {"status": "unavailable", "evidence": []}
EVIDENCE = {
    "document_id": " doc ",
    "version_id": "v1",
    "chunk_id": "c1",
    "source_sha256": "a" * 64,
    "page": 1,
    "section": None,
    "text": " Texto exacto\ncon espacios. ",
}


class Client:
    """Double confined to tests, recording calls and returning upstream JSON."""

    def __init__(self, result: object) -> None:
        """Keep the configured upstream outcome and call history."""
        self.result = result
        self.calls = []

    def __bool__(self) -> bool:
        """Ensure composition does not mistake a falsey client for absence."""
        return False

    async def query(self, request: object) -> object:
        """Capture the validated request without providing runtime retrieval."""
        self.calls.append(request.model_dump())
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def post(app, payload: object, path: str = "/retrieval/query"):
    """Exercise POST through ASGI without sockets or HTTP dependencies."""
    messages = []

    async def receive():
        """Provide one JSON request body."""
        return {"type": "http.request", "body": json.dumps(payload).encode()}

    async def send(message):
        """Collect HTTP response messages."""
        messages.append(message)

    async def run():
        """Await the complete native asynchronous application invocation."""
        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "query_string": b"",
                "root_path": "",
                "headers": [(b"content-type", b"application/json")],
            },
            receive,
            send,
        )

    asyncio.run(run())
    assert (b"content-type", b"application/json") in messages[0]["headers"]
    assert not messages[-1].get("more_body", False)
    return messages[0]["status"], json.loads(
        b"".join(message["body"] for message in messages[1:])
    )


def test_default_unavailable_and_chat_absent():
    """Default composition has no adapter, answer generation or chat route."""
    app = create_app()
    assert post(app, {"question": "ayuda"}) == (503, EMPTY)
    assert post(app, {"question": "ayuda"}, "/chat") == (404, {"detail": "Not Found"})


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"question": " \n "},
        {"question": "x" * 501},
        {"question": 1},
        {"question": "x", "limit": 0},
        {"question": "x", "limit": 21},
        {"question": "x", "limit": True},
        {"question": "x", "limit": "5"},
        {"question": "x", "answer": "inventada"},
        {"question": "x", "role": "system"},
        {"question": "x", "messages": [{"role": "developer", "content": "override"}]},
    ],
)
def test_invalid_request_never_calls_dependency(payload):
    """Reject invalid or privileged input before invoking the injected port."""
    client = Client(EMPTY)
    status, body = post(create_app(rag_client=client), payload)
    assert status == 422
    assert body["detail"]
    assert client.calls == []


@pytest.mark.parametrize("question,limit", [(" x ", None), ("x", 1), ("x" * 500, 20)])
@pytest.mark.parametrize("evidence", [[], [EVIDENCE]])
def test_proxy_exact_request_and_response(question, limit, evidence):
    """Preserve provenance/text while normalizing only the input question."""
    expected = {
        "status": "evidence_found" if evidence else "abstained",
        "evidence": evidence,
    }
    client = Client(expected)
    payload = {"question": question}
    if limit is not None:
        payload["limit"] = limit
    assert post(create_app(rag_client=client), payload) == (200, expected)
    assert client.calls == [{"question": question.strip(), "limit": limit or 5}]
    assert client.result == expected


@pytest.mark.parametrize(
    "upstream", [None, EMPTY, RuntimeError("secret upstream text")]
)
def test_unavailable_is_safe(upstream, caplog):
    """Dependency absence/failure never reveals question, text or exception."""
    client = Client(upstream)
    assert post(create_app(rag_client=client), {"question": "private question"}) == (
        503,
        EMPTY,
    )
    assert client.calls == [{"question": "private question", "limit": 5}]
    assert "private question" not in caplog.text
    assert "secret upstream text" not in caplog.text


@pytest.mark.parametrize(
    "upstream",
    [
        {},
        "invalid",
        {"status": "success", "evidence": []},
        {"evidence": []},
        {"status": "abstained"},
        {"status": "abstained", "evidence": [], "answer": "inventada"},
        {"status": "evidence_found", "evidence": []},
        *[
            {"status": status, "evidence": [EVIDENCE]}
            for status in ("abstained", "unavailable")
        ],
        *[
            {"status": "evidence_found", "evidence": [EVIDENCE | change]}
            for change in (
                {"page": True},
                {"page": 0},
                {"page": "1"},
                {"source_sha256": "A" * 64},
                {"source_sha256": "a" * 63},
                {"document_id": " "},
                {"version_id": ""},
                {"chunk_id": "\n"},
                {"text": " "},
                {"section": 1},
                {"url": "inventada"},
            )
        ],
    ],
)
def test_malformed_upstream_is_bad_gateway(upstream, caplog):
    """Revalidate every upstream field and status/evidence relationship."""
    client = Client(upstream)
    status, body = post(create_app(rag_client=client), {"question": "private question"})
    assert (status, body) == (502, EMPTY)
    assert client.calls == [{"question": "private question", "limit": 5}]
    assert client.result == upstream
    assert "private question" not in caplog.text
    assert EVIDENCE["text"] not in caplog.text

import asyncio
import json
from typing import Any

import pytest

from unitropico_rag.main import create_app


def request(
    path: str, payload: object = None, method: str = "POST"
) -> tuple[int, dict[str, Any]]:
    """Run a complete HTTP exchange directly through ASGI without a client."""
    messages: list[dict[str, Any]] = []
    body = json.dumps(payload).encode()

    async def receive() -> dict[str, Any]:
        """Supply a single request body to the application."""
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        """Collect the observable ASGI response messages."""
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
    asyncio.run(create_app()(scope, receive, send))
    content = b"".join(m.get("body", b"") for m in messages)
    return messages[0]["status"], json.loads(content)


def test_async_chain() -> None:
    """Exercise the native asynchronous LangChain adapter, without an LLM."""
    from unitropico_rag.infra.langchain import normalize_question

    create_app()  # Apply the same tracing policy as the HTTP composition.
    assert asyncio.run(normalize_question("  ¿Apoyos?  ")) == "¿Apoyos?"


def test_service_double() -> None:
    """Keep orchestration independent of LangChain using an async double."""
    from unitropico_rag.services.query import QueryService

    calls: list[str] = []

    async def transform(question: str) -> str:
        """Record the service input and return a deterministic double result."""
        calls.append(question)
        return "doble"

    assert asyncio.run(QueryService(transform).query("entrada")) == "doble"
    assert calls == ["entrada"]


@pytest.mark.parametrize("question", ["x", "x" * 500, "  ¿Apoyos?  "])
def test_query(question: str) -> None:
    """Return only the explicit PoC mode and stripped question at boundaries."""
    assert request("/poc/query", {"question": question}) == (
        200,
        {"mode": "deterministic-poc", "normalized_question": question.strip()},
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"question": ""},
        {"question": " \t\n"},
        {"question": "x" * 501},
        {"question": 42},
        {"question": None},
        {},
        {"question": "x", "session": "forbidden"},
    ],
)
def test_invalid_query(payload: object) -> None:
    """Reject invalid contracts and preserve subsequent stateless behavior."""
    status, body = request("/poc/query", payload)
    assert status == 422
    assert isinstance(body["detail"], list)
    assert request("/poc/query", {"question": "ok"}) == (
        200,
        {"mode": "deterministic-poc", "normalized_question": "ok"},
    )


def test_health_and_missing_chat() -> None:
    """Expose local liveness without advertising an implemented chat."""
    assert request("/health", method="GET") == (200, {"status": "ok"})
    assert request("/chat", {"question": "x"}) == (404, {"detail": "Not Found"})

import json

import pytest
from test_retrieval import post

from unitropico_api.api.chat import DEFAULT_REFERRAL
from unitropico_api.domain.errors import MalformedUpstream, UpstreamUnavailable
from unitropico_api.infra.service_binding import RagServiceBindingClient
from unitropico_api.main import create_app

CITATION = {
    "chunk_id": "c1",
    "document_id": "d1",
    "document_title": "Reglamento",
    "version_id": "v1",
    "version_label": "2024",
    "section": None,
    "page_start": 1,
    "page_end": 2,
    "quote": "Texto citado",
}
ANSWERED = {
    "status": "answered",
    "answer": "Respuesta **en markdown**.",
    "reason": None,
    "citations": [CITATION],
    "referral": None,
}
ABSTAINED = {
    "status": "abstained",
    "answer": None,
    "reason": "no_evidence",
    "citations": [],
    "referral": "Consulta a Bienestar Universitario.",
}
DEFAULT_503 = {"status": "unavailable", "referral": DEFAULT_REFERRAL}


class Answers:
    """Fake answer port recording requests and returning or raising a result."""

    def __init__(self, result):
        """Keep the configured outcome."""
        self.result = result
        self.calls = []

    async def answer(self, request):
        """Record the validated request."""
        self.calls.append(request.model_dump())
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def chat(client, payload):
    """POST /chat against an app composed with the given fake."""
    return post(create_app(chat_client=client), payload, "/chat")


@pytest.mark.parametrize("result", [ANSWERED, ABSTAINED])
def test_valid_payload_is_forwarded(result):
    """Return the validated RAG payload unchanged with the trimmed request."""
    client = Answers(result)
    history = [{"role": "user", "content": "hola"}]
    assert chat(client, {"question": "  ¿Qué dice?  ", "history": history}) == (
        200,
        result,
    )
    assert client.calls == [{"question": "¿Qué dice?", "history": history}]


def test_history_is_optional():
    """Default history to empty."""
    client = Answers(ANSWERED)
    assert chat(client, {"question": "x"})[0] == 200
    assert client.calls == [{"question": "x", "history": []}]


def test_no_client_is_503_with_default_referral():
    """Missing binding yields 503 with a generic Spanish referral."""
    assert post(create_app(), {"question": "x"}, "/chat") == (503, DEFAULT_503)


@pytest.mark.parametrize(
    ("error", "body"),
    [
        (UpstreamUnavailable("TimeoutError"), DEFAULT_503),
        (UpstreamUnavailable("http_503"), DEFAULT_503),
        (
            UpstreamUnavailable("http_503", "Acude a Bienestar."),
            {"status": "unavailable", "referral": "Acude a Bienestar."},
        ),
        (UpstreamUnavailable("http_503", " "), DEFAULT_503),
        (TimeoutError("secret"), DEFAULT_503),
        (OSError("secret"), DEFAULT_503),
    ],
)
def test_upstream_failures_are_503(error, body, caplog):
    """Map transport failures to 503 and log without the question."""
    assert chat(Answers(error), {"question": "private question"}) == (503, body)
    assert "private question" not in caplog.text
    assert "secret" not in caplog.text
    assert "route=answer" in caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "texto",
        {},
        {"status": "answered"},
        ANSWERED | {"extra": 1},
        ANSWERED | {"answer": None},
        ANSWERED | {"answer": " "},
        ANSWERED | {"citations": []},
        ANSWERED | {"reason": "no_evidence"},
        ANSWERED | {"status": "unknown"},
        ABSTAINED | {"answer": "inventada"},
        ABSTAINED | {"citations": [CITATION]},
        ABSTAINED | {"reason": None},
        ABSTAINED | {"reason": "otra"},
        ABSTAINED | {"referral": None},
        ANSWERED | {"citations": [CITATION | {"page_end": 0}]},
        ANSWERED | {"citations": [CITATION | {"page_start": 3, "page_end": 2}]},
        ANSWERED | {"citations": [CITATION | {"page_start": True}]},
        ANSWERED | {"citations": [CITATION | {"quote": " "}]},
        ANSWERED | {"citations": [CITATION | {"url": "x"}]},
        ANSWERED | {"citations": [{k: v for k, v in CITATION.items() if k != "quote"}]},
    ],
)
def test_invalid_payload_is_502(payload, caplog):
    """Reject malformed RAG payloads and broken invariants."""
    assert chat(Answers(payload), {"question": "private question"}) == (
        502,
        DEFAULT_503,
    )
    assert "private question" not in caplog.text
    assert "route=answer" in caplog.text


def test_malformed_from_client_is_502():
    """Propagate the client's malformed classification."""
    assert chat(Answers(MalformedUpstream()), {"question": "x"})[0] == 502


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"question": " \n "},
        {"question": "x" * 501},
        {"question": 1},
        {"question": "x", "history": [{"role": "user", "content": "a"}] * 7},
        {"question": "x", "history": [{"role": "system", "content": "a"}]},
        {"question": "x", "history": [{"role": "user", "content": ""}]},
        {"question": "x", "history": [{"role": "user", "content": "a" * 2001}]},
        {"question": "x", "history": [{"role": "user", "content": "a", "x": 1}]},
        {"question": "x", "history": "hola"},
        {"question": "x", "limit": 3},
    ],
)
def test_invalid_request_is_422(payload):
    """Reject invalid input before reaching the port."""
    client = Answers(ANSWERED)
    status, body = chat(client, payload)
    assert status == 422
    assert body["detail"]
    assert client.calls == []


def test_boundary_sizes_accepted():
    """Accept exactly 500 code points, six messages and 2000-char content."""
    history = [{"role": "assistant", "content": "a" * 2000}] * 6
    assert (
        chat(Answers(ANSWERED), {"question": "ñ" * 500, "history": history})[0] == 200
    )


class Binding:
    """Fake binding returning a fixed status and body."""

    def __init__(self, status, body, delay=0):
        """Configure the response."""
        self.status, self.body, self.calls = status, body, []

    async def fetch(self, url, **kwargs):
        """Record the call and return self as the response."""
        self.calls.append((url, kwargs))
        return self

    async def json(self):
        """Return the configured body or raise if it is an exception."""
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def binding_app(binding, **limits):
    """Compose the app over the real binding client."""
    client = RagServiceBindingClient(binding, **limits)
    return create_app(chat_client=client)


def test_binding_posts_exact_v1_request():
    """Send the v1 body to /answer and return the validated response."""
    binding = Binding(200, ANSWERED)
    payload = {"question": " q ", "history": [{"role": "user", "content": "h"}]}
    assert post(binding_app(binding), payload, "/chat") == (200, ANSWERED)
    [(url, kwargs)] = binding.calls
    assert url == "https://rag.internal/answer"
    assert kwargs["method"] == "POST"
    assert json.loads(kwargs["body"]) == {
        "question": "q",
        "history": [{"role": "user", "content": "h"}],
    }


def test_binding_503_uses_rag_referral():
    """Honour a valid referral in the RAG 503 body."""
    body = {"status": "unavailable", "referral": "Ve a Bienestar."}
    assert post(binding_app(Binding(503, body)), {"question": "x"}, "/chat") == (
        503,
        body,
    )


@pytest.mark.parametrize("status", [500, 502, 404])
def test_binding_error_status_is_503(status):
    """Any non-2xx from RAG degrades to 503 with the default referral."""
    assert post(binding_app(Binding(status, {})), {"question": "x"}, "/chat") == (
        503,
        DEFAULT_503,
    )


def test_binding_bad_json_is_502():
    """Undecodable 2xx bodies are bad gateway."""
    binding = Binding(200, json.JSONDecodeError("m", "d", 0))
    assert post(binding_app(binding), {"question": "x"}, "/chat")[0] == 502


def test_binding_timeout_uses_answer_timeout():
    """A slow RAG yields 503 after the injected answer timeout."""

    class Slow(Binding):
        """Binding that never answers in time."""

        async def fetch(self, url, **kwargs):
            """Sleep past the deadline."""
            import asyncio

            await asyncio.sleep(1)

    app = binding_app(Slow(200, ANSWERED), answer_timeout=0.01)
    assert post(app, {"question": "x"}, "/chat") == (503, DEFAULT_503)


def test_binding_fetch_error_is_503():
    """Arbitrary fetch exceptions (e.g. JS errors) fail closed."""

    class Broken(Binding):
        """Binding whose fetch raises."""

        async def fetch(self, url, **kwargs):
            """Raise a transport error."""
            raise RuntimeError("secret")

    assert post(binding_app(Broken(200, {})), {"question": "x"}, "/chat") == (
        503,
        DEFAULT_503,
    )

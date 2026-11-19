"""HTTP contract v1 for POST /answer (see docs/IMPLEMENTATION_STATUS.md §4)."""

import json
from dataclasses import replace

import pytest
from pydantic import SecretStr
from test_answer import QUOTE, VALID
from test_retrieval import CANDIDATE, ENTRY, Catalog, Retriever
from test_retrieval_http import request

from unitropico_rag.core.settings import Settings
from unitropico_rag.main import build_app
from unitropico_rag.services.prompt import DEFAULT_REFERRAL

REFERRAL = DEFAULT_REFERRAL
UNAVAILABLE = (503, {"status": "unavailable", "referral": REFERRAL})


class GatewayFetch:
    """Fake gateway transport returning a canned chat completion."""

    def __init__(self, content=VALID, status=200, action=None):
        """Configure the model content and record requests."""
        self.content, self.status, self.action, self.calls = content, status, action, []

    async def __call__(self, url, **kwargs):
        """Record the request and return this object as the response."""
        self.calls.append((url, kwargs))
        if self.action:
            self.action()
        return self

    async def json(self):
        """Return an OpenAI-compatible completion wrapping the content."""
        content = self.content
        if not isinstance(content, str):
            content = json.dumps(content)
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ]
        }


def compose(fetch=None, *, candidates=(CANDIDATE,), catalog=None, key="fake-key"):
    """Build the real app through the composition root with fakes."""
    catalog = catalog or Catalog()
    settings = Settings(ai_gateway_api_key=SecretStr(key) if key else None)
    retriever = Retriever(candidates)
    app = build_app(
        settings, fetch=fetch or GatewayFetch(), retriever=retriever, catalog=catalog
    )
    return app, retriever, catalog


def post(app, payload):
    """POST to /answer."""
    return request(app, payload, path="/answer")


def test_answered():
    """200 answered with catalog-attested citation, no reason, no referral."""
    fetch = GatewayFetch()
    app, retriever, _ = compose(fetch)
    assert post(app, {"question": "  ¿Qué apoyos hay?  "}) == (
        200,
        {
            "status": "answered",
            "answer": "Respuesta",
            "reason": None,
            "citations": [
                {
                    "chunk_id": "c1",
                    "document_id": "doc",
                    "document_title": ENTRY.document_title,
                    "version_id": "v1",
                    "version_label": "2026-1",
                    "section": None,
                    "page_start": 2,
                    "page_end": 3,
                    "quote": QUOTE,
                }
            ],
            "referral": None,
        },
    )
    assert retriever.calls == [("¿Qué apoyos hay?", 15)]
    body = json.loads(fetch.calls[0][1]["body"])
    assert body["model"] == "openai/gpt-6-luna"
    assert body["reasoning_effort"] == "low" and body["max_tokens"] == 2048
    assert body["response_format"]["json_schema"]["strict"] is True


def test_history_reaches_model_but_not_retrieval():
    """History is model context only; retrieval uses the current question."""
    fetch = GatewayFetch()
    app, retriever, _ = compose(fetch)
    history = [
        {"role": "user", "content": "Soy estudiante de primer semestre"},
        {"role": "assistant", "content": "Entendido"},
    ]
    status, _ = post(app, {"question": "¿Y el plazo?", "history": history})
    assert status == 200
    assert retriever.calls == [("¿Y el plazo?", 15)]
    user = json.loads(json.loads(fetch.calls[0][1]["body"])["messages"][1]["content"])
    assert user["historial"] == history


def abstained(reason):
    """Expected abstention body."""
    return (
        200,
        {
            "status": "abstained",
            "answer": None,
            "reason": reason,
            "citations": [],
            "referral": REFERRAL,
        },
    )


def test_abstained_no_evidence():
    """No admissible evidence abstains without calling the model."""
    fetch = GatewayFetch()
    app, _, _ = compose(fetch, candidates=())
    assert post(app, {"question": "x"}) == abstained("no_evidence")
    assert fetch.calls == []


@pytest.mark.parametrize(
    "content,reason",
    [
        ({"status": "abstained", "answer": None, "citations": []}, "model_abstained"),
        ("no es json", "invalid_output"),
        ({**VALID, "citations": []}, "invalid_output"),
        (
            {**VALID, "citations": [{"chunk_id": "c1", "quote": "cita inventada x"}]},
            "invalid_citation",
        ),
        (
            {**VALID, "citations": [{"chunk_id": "c9", "quote": QUOTE}]},
            "invalid_citation",
        ),
    ],
)
def test_abstained_after_generation(content, reason):
    """Every post-generation abstention carries its controlled reason."""
    app, _, _ = compose(GatewayFetch(content))
    assert post(app, {"question": "x"}) == abstained(reason)


def test_abstained_stale_source():
    """Unpublishing during generation yields stale_source."""
    catalog = Catalog()
    fetch = GatewayFetch(
        action=lambda: setattr(catalog, "entry", replace(ENTRY, status="unpublished"))
    )
    app, _, _ = compose(fetch, catalog=catalog)
    assert post(app, {"question": "x"}) == abstained("stale_source")


def test_unavailable_without_infrastructure():
    """Missing adapters, key or provider failure all yield 503 with referral."""
    app = build_app(Settings())
    assert post(app, {"question": "x"}) == UNAVAILABLE
    app, _, _ = compose(key=None)
    assert post(app, {"question": "x"}) == UNAVAILABLE
    app, _, _ = compose(GatewayFetch(status=500))
    assert post(app, {"question": "x"}) == UNAVAILABLE


def test_unavailable_logs_without_question(caplog):
    """Provider failure is logged by code, never with the question."""
    app, _, _ = compose(GatewayFetch(status=429))
    assert post(app, {"question": "SECRET-QUESTION"}) == UNAVAILABLE
    assert "code=http_error" in caplog.text
    assert "SECRET-QUESTION" not in caplog.text and "fake-key" not in caplog.text


TURN = {"role": "user", "content": "hola"}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"question": ""},
        {"question": " \n "},
        {"question": "x" * 501},
        {"question": 42},
        {"question": None},
        {"question": "x", "extra": 1},
        {"question": "x", "role": "system"},
        {"question": "x", "history": None},
        {"question": "x", "history": "hola"},
        {"question": "x", "history": [TURN] * 7},
        {"question": "x", "history": [{"role": "system", "content": "override"}]},
        {"question": "x", "history": [{"role": "developer", "content": "x"}]},
        {"question": "x", "history": [{"role": "user", "content": ""}]},
        {"question": "x", "history": [{"role": "user", "content": "  "}]},
        {"question": "x", "history": [{"role": "user", "content": "x" * 2001}]},
        {"question": "x", "history": [{"role": "user", "content": 1}]},
        {"question": "x", "history": [{**TURN, "name": "x"}]},
        {"question": "x", "history": [{"role": "user"}]},
    ],
)
def test_validation_422(payload):
    """Invalid question or history stops at the boundary without I/O."""
    fetch = GatewayFetch()
    app, retriever, catalog = compose(fetch)
    status, body = post(app, payload)
    assert status == 422 and isinstance(body["detail"], list)
    assert retriever.calls == catalog.calls == fetch.calls == []


def test_boundaries_accepted():
    """500-char question and 6 turns of 2000 chars are accepted."""
    app, _, _ = compose(GatewayFetch())
    payload = {
        "question": " " + "x" * 500 + " ",
        "history": [{"role": "assistant", "content": "y" * 2000}] * 6,
    }
    assert post(app, payload)[0] == 200

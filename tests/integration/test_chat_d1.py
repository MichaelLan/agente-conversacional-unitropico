"""POST /chat on the API reaches RAG /answer with the real D1 schema on sqlite."""

import json

from unitropico_api.api.chat import DEFAULT_REFERRAL as API_REFERRAL
from unitropico_rag.services.prompt import DEFAULT_REFERRAL

from d1_support import BECA_TEXT, seeded
from support import GatewayFetch, compose_api, lexical_rag, request

QUOTE = "La inscripción se realiza hasta el 30 de junio de 2026"
ANSWER = {
    "status": "answered",
    "answer": "La inscripción va hasta el 30 de junio.",
    "citations": [{"chunk_id": "chunk-beca", "quote": QUOTE}],
}
QUESTION = "¿Hasta cuándo hay inscripción a la beca de alimentación?"


def chat(fetch, question=QUESTION, tamper=None):
    """Run /chat through API, binding and RAG over the seeded D1."""
    app, binding = compose_api(lexical_rag(seeded(), fetch))(tamper)
    return request(app, {"question": question}, path="/chat"), binding


def test_chat_answered_with_citations_from_d1():
    """200 answered; citation metadata comes from D1, not from the model."""
    fetch = GatewayFetch(ANSWER)
    (status, body), binding = chat(fetch)
    assert status == 200, body
    assert body["status"] == "answered"
    assert body["answer"] == ANSWER["answer"]
    assert body["referral"] is None
    assert body["citations"] == [
        {
            "chunk_id": "chunk-beca",
            "document_id": "doc-beca",
            "document_title": "Reglamento de Bienestar Universitario",
            "version_id": "ver-2026",
            "version_label": "2026-1",
            "section": "Capítulo II",
            "page_start": 3,
            "page_end": 4,
            "quote": QUOTE,
        }
    ]
    assert [c[0] for c in binding.calls] == ["https://rag.internal/answer"]
    prompt = json.loads(json.loads(fetch.calls[0][1]["body"])["messages"][1]["content"])
    # Exact FTS hit first; the transport chunk shares no question term and is
    # only backfilled through the synonym expansion beca -> apoyo.
    assert [s["chunk_id"] for s in prompt["fuentes"]] == [
        "chunk-beca",
        "chunk-transporte",
    ]
    assert prompt["fuentes"][0]["texto"] == BECA_TEXT


def test_chat_abstains_without_evidence_and_never_calls_model():
    """no_evidence abstention is relayed with its referral; no generation."""
    fetch = GatewayFetch(ANSWER)
    (status, body), binding = chat(fetch, question="zzzz qqqq inexistente")
    assert status == 200
    assert body["status"] == "abstained"
    assert body["reason"] == "no_evidence"
    assert body["citations"] == []
    assert body["referral"] == DEFAULT_REFERRAL
    assert fetch.calls == []
    assert binding.responses[0][0] == 200


def test_chat_contract_violation_from_rag_is_502():
    """A RAG payload breaking the API contract becomes 502, not a relay."""

    def corrupt(payload):
        return {**payload, "citations": [{"chunk_id": "x"}]}

    (status, body), binding = chat(GatewayFetch(ANSWER), tamper=corrupt)
    assert binding.responses[0][0] == 200
    assert status == 502
    assert body == {"status": "unavailable", "referral": API_REFERRAL}

"""End-to-end /answer through the real composition root (in-process CPython).

D1 is the real migration on sqlite3 behind a fake ``DB`` binding; Vectorize is a
fake binding; one fake fetch transport serves both the embeddings endpoint and
the chat completion endpoint. Nothing touches the network.
"""

import json

import pytest
from d1_support import BECA_TEXT, seeded
from pydantic import SecretStr
from test_retrieval_http import request

from unitropico_rag.core.settings import Settings
from unitropico_rag.main import Bindings, build_app
from unitropico_rag.services.prompt import DEFAULT_REFERRAL

QUOTE = "La inscripción se realiza hasta el 30 de junio de 2026"
ANSWER = {
    "status": "answered",
    "answer": "La inscripción a la beca de alimentación va hasta el 30 de junio.",
    "citations": [{"chunk_id": "chunk-beca", "quote": QUOTE}],
}


class Response:
    """Fake fetch response."""

    def __init__(self, payload, status=200):
        """Store payload and status."""
        self.payload, self.status = payload, status

    async def json(self):
        """Return the payload."""
        return self.payload


class Transport:
    """Route embeddings and chat completion requests to canned responses."""

    def __init__(self, completion=ANSWER, before_completion=None, embed_status=200):
        """Configure the LLM output and an optional side effect."""
        self.completion, self.before_completion = completion, before_completion
        self.embed_status, self.calls = embed_status, []

    async def __call__(self, url, **kwargs):
        """Serve the request by URL."""
        body = json.loads(kwargs["body"])
        self.calls.append((url, body))
        if url.endswith("/v1/embeddings"):
            data = [
                {"index": i, "embedding": [0.01] * 1536}
                for i in range(len(body["input"]))
            ]
            return Response({"data": data}, self.embed_status)
        if self.before_completion:
            self.before_completion()
        message = {"role": "assistant", "content": json.dumps(self.completion)}
        return Response({"choices": [{"message": message, "finish_reason": "stop"}]})

    def urls(self):
        """Return requested endpoint suffixes."""
        return [url.rsplit("/", 1)[-1] for url, _ in self.calls]


class Vectorize:
    """Fake VECTORIZE binding with fixed matches."""

    def __init__(self, ids):
        """Store match ids (best first)."""
        self.ids, self.calls = ids, []

    async def query(self, vector, **options):
        """Return matches in the documented shape."""
        self.calls.append((len(vector), options))
        return {
            "count": len(self.ids),
            "matches": [
                {"id": chunk_id, "score": 0.9 - n / 10}
                for n, chunk_id in enumerate(self.ids)
            ],
        }


def compose(db, transport, vectorize, mode="hybrid", key="fake-key"):
    """Build the app exactly as the Worker does, with fake bindings."""
    settings = Settings(
        ai_gateway_api_key=SecretStr(key) if key else None, retrieval_mode=mode
    )
    return build_app(settings, fetch=transport, bindings=Bindings(db, vectorize))


def ask(app, question):
    """POST /answer."""
    return request(app, {"question": question}, path="/answer")


def test_answered_with_catalog_metadata_from_d1():
    """Hybrid retrieval → LLM → citation enriched only from D1 metadata."""
    db, transport = seeded(), Transport()
    # Vectorize also returns a draft chunk (not published in D1): it is dropped.
    vectorize = Vectorize(["chunk-draft", "chunk-beca"])
    status, body = ask(
        compose(db, transport, vectorize), "¿Hasta cuándo hay inscripción a la beca?"
    )
    assert status == 200, body
    assert body == {
        "status": "answered",
        "answer": ANSWER["answer"],
        "reason": None,
        "referral": None,
        "citations": [
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
        ],
    }
    assert transport.urls() == ["embeddings", "completions"]
    assert vectorize.calls == [
        (1536, {"topK": 50, "returnValues": False, "returnMetadata": "none"})
    ]
    prompt = json.loads(transport.calls[1][1]["messages"][1]["content"])
    # "beca" expands to "apoyo" in FTS, so the transport chunk follows.
    assert [source["chunk_id"] for source in prompt["fuentes"]] == [
        "chunk-beca",
        "chunk-transporte",
    ]
    assert prompt["fuentes"][0]["texto"] == BECA_TEXT
    assert prompt["fuentes"][0]["vigente_hasta"] == "2026-06-30"


def test_unpublished_between_retrieval_and_answer_is_stale_source():
    """Unpublishing during generation makes the citation stale."""
    db = seeded()
    transport = Transport(
        before_completion=lambda: db.set_status("ver-2026", "unpublished")
    )
    status, body = ask(
        compose(db, transport, Vectorize(["chunk-beca"])), "beca inscripción"
    )
    assert (status, body["status"], body["reason"]) == (
        200,
        "abstained",
        "stale_source",
    )
    assert body["citations"] == [] and body["referral"] == DEFAULT_REFERRAL


def test_no_matches_is_no_evidence_without_generation():
    """No lexical hits and only unpublished vectors → no_evidence, no LLM call."""
    db, transport = seeded(), Transport()
    status, body = ask(
        compose(db, transport, Vectorize(["chunk-draft", "ghost"])),
        "horario piscina olímpica",
    )
    assert (status, body["status"], body["reason"]) == (200, "abstained", "no_evidence")
    assert body["referral"] == DEFAULT_REFERRAL
    assert transport.urls() == ["embeddings"]


def test_semantic_channel_failure_degrades_to_lexical(caplog):
    """A failing embeddings call still answers from FTS."""
    db, transport = seeded(), Transport(embed_status=500)
    status, body = ask(
        compose(db, transport, Vectorize(["chunk-beca"])), "inscripción beca"
    )
    assert (status, body["status"]) == (200, "answered")
    assert (
        "retrieval_channel_failed channel=semantic error=EmbeddingError" in caplog.text
    )


def test_hybrid_without_vectorize_degrades_to_lexical(caplog):
    """Missing VECTORIZE in hybrid mode composes lexical-only with a warning."""
    db, transport = seeded(), Transport()
    status, body = ask(compose(db, transport, None), "inscripción beca")
    assert (status, body["status"]) == (200, "answered")
    assert (
        "retrieval_degraded mode=hybrid effective=lexical missing=VECTORIZE"
        in caplog.text
    )
    assert transport.urls() == ["completions"]


def test_lexical_mode_never_embeds():
    """RETRIEVAL_MODE=lexical skips embeddings and Vectorize."""
    db, transport, vectorize = seeded(), Transport(), Vectorize(["chunk-beca"])
    status, body = ask(
        compose(db, transport, vectorize, mode="lexical"), "inscripción beca"
    )
    assert (status, body["status"]) == (200, "answered")
    assert transport.urls() == ["completions"] and vectorize.calls == []


def test_semantic_mode_uses_only_vectors():
    """RETRIEVAL_MODE=semantic answers from Vectorize hits even without FTS terms."""
    db, transport = seeded(), Transport()
    vectorize = Vectorize(["chunk-beca"])
    status, body = ask(compose(db, transport, vectorize, mode="semantic"), "¿?¡!")
    assert (status, body["status"]) == (200, "answered")
    assert transport.urls() == ["embeddings", "completions"]


def test_semantic_mode_without_vectorize_is_unavailable(caplog):
    """Semantic mode never silently falls back to lexical."""
    status, body = ask(compose(seeded(), Transport(), None, mode="semantic"), "beca")
    assert (status, body["status"]) == (503, "unavailable")
    assert "retrieval_unconfigured mode=semantic missing=VECTORIZE" in caplog.text


@pytest.mark.parametrize("mode", ["hybrid", "lexical", "semantic"])
def test_missing_db_is_unavailable(mode):
    """Without the DB binding nothing is answered."""
    status, body = ask(
        compose(None, Transport(), Vectorize(["chunk-beca"]), mode), "beca"
    )
    assert (status, body) == (
        503,
        {"status": "unavailable", "referral": DEFAULT_REFERRAL},
    )


def test_d1_failure_is_unavailable(caplog):
    """A D1 error during retrieval fails closed without leaking content."""
    db = seeded()
    db.fail = True
    status, body = ask(compose(db, Transport(), None, mode="lexical"), "beca SECRET")
    assert (status, body["status"]) == (503, "unavailable")
    assert "SECRET" not in caplog.text


def test_retrieval_endpoint_returns_d1_evidence():
    """/retrieval/query exposes D1 text and catalog metadata."""
    db = seeded()
    status, body = request(
        compose(db, Transport(), None, mode="lexical"),
        {"question": "transporte municipio", "limit": 3},
    )
    assert status == 200 and body["status"] == "evidence_found"
    assert [item["chunk_id"] for item in body["evidence"]] == ["chunk-transporte"]
    assert body["evidence"][0]["page_start"] == 5

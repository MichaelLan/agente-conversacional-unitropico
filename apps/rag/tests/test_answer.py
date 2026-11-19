import asyncio
import json
from dataclasses import replace
from datetime import date
from hashlib import sha256

import pytest
from test_retrieval import CANDIDATE, ENTRY, Catalog, Retriever, chunk

from unitropico_rag.domain.answer import (
    ANSWER_CANDIDATE_SCHEMA,
    AnswerCandidate,
    AnswerCitation,
    AnswerQuery,
    AnswerResult,
    ConversationTurn,
)
from unitropico_rag.domain.evidence import RetrievalResult
from unitropico_rag.domain.generation import GeneratedText, GenerationError, TokenUsage
from unitropico_rag.services.answer import AnswerService, quote_matches
from unitropico_rag.services.prompt import DEFAULT_REFERRAL, SYSTEM_PROMPT
from unitropico_rag.services.retrieval import RetrievalService

QUOTE = "Texto de prueba sobre apoyos"
VALID = {
    "status": "answered",
    "answer": "Respuesta",
    "citations": [{"chunk_id": "c1", "quote": QUOTE}],
}
TODAY = date(2026, 11, 18)


class Generator:
    """Generate only offline and optionally change publication while awaiting."""

    def __init__(self, payload=VALID, action=None):
        """Keep calls and simulated asynchronous effects local to each test."""
        self.payload, self.action, self.calls = payload, action, []

    async def complete(self, system_prompt, user_content):
        """Record the envelope and simulate completion or dependency failure."""
        self.calls.append((system_prompt, user_content))
        await asyncio.sleep(0)
        if self.action:
            self.action()
        if isinstance(self.payload, BaseException):
            raise self.payload
        if isinstance(self.payload, dict):
            return GeneratedText(json.dumps(self.payload), TokenUsage(total_tokens=7))
        return self.payload


def service(retrieval, generator, catalog, **kwargs):
    """Build the service with test defaults for required limits."""
    config = {"max_input_chars": 20000, "clock": lambda: TODAY} | kwargs
    return AnswerService(retrieval, generator, catalog, **config)


def run(
    payload=VALID,
    *,
    entry=ENTRY,
    candidates=(CANDIDATE,),
    action=None,
    history=(),
    question=" pregunta ",
    **kwargs,
):
    """Exercise real retrieval and answer orchestration through public ports."""
    catalog, retriever = Catalog(entry), Retriever(candidates)
    generator = Generator(payload, lambda: action(catalog) if action else None)
    answer = service(RetrievalService(retriever, catalog), generator, catalog, **kwargs)
    query = AnswerQuery(question=question.strip(), history=tuple(history))
    return asyncio.run(answer.query(query)), generator, catalog, retriever


def test_answer_with_catalog_citation_and_prompt_data():
    """Cite with catalog metadata and send question, history and sources as data."""
    history = [ConversationTurn("user", "Hola"), ConversationTurn("assistant", "Hola")]
    result, generator, catalog, retriever = run(history=history)
    assert result == AnswerResult(
        "answered",
        "Respuesta",
        citations=(
            AnswerCitation(
                chunk_id="c1",
                document_id="doc",
                document_title="Reglamento sintético de prueba",
                version_id="v1",
                version_label="2026-1",
                section=None,
                page_start=2,
                page_end=3,
                quote=QUOTE,
            ),
        ),
        generation_attempted=True,
        usage=TokenUsage(total_tokens=7),
    )
    system, user = generator.calls[0]
    assert system == SYSTEM_PROMPT
    assert json.loads(user) == {
        "pregunta": "pregunta",
        "historial": [
            {"role": "user", "content": "Hola"},
            {"role": "assistant", "content": "Hola"},
        ],
        "fecha_consulta": "2026-11-18",
        "fuentes": [
            {
                "chunk_id": "c1",
                "titulo": "Reglamento sintético de prueba",
                "version": "2026-1",
                "vigencia": "Vigente durante 2026-1",
                "vigente_desde": "2026-02-01",
                "vigente_hasta": "2026-06-30",
                "paginas": "2-3",
                "seccion": None,
                "texto": CANDIDATE.text,
            }
        ],
    }
    # Retrieval uses only the current question, never the history.
    assert retriever.calls == [("pregunta", 15)]
    assert catalog.calls == ["c1", "c1"]


def test_system_prompt_rules():
    """Keep the Spanish policy: evidence only, citations, abstention, conflicts."""
    for fragment in (
        "español",
        "únicamente con información presente",
        "No inventes requisitos, fechas",
        "chunk_id",
        "abstente",
        "disposiciones diferentes",
        "no elijas",
        "vigencia",
        "texto plano",
    ):
        assert fragment in SYSTEM_PROMPT


def test_citation_uses_current_catalog_title():
    """Presentation metadata comes from the catalog read after generation."""
    renamed = replace(ENTRY, document_title="Título actualizado", version_label=None)
    result, *_ = run(action=lambda catalog: setattr(catalog, "entry", renamed))
    assert result.status == "answered"
    assert result.citations[0].document_title == "Título actualizado"
    assert result.citations[0].version_label is None


@pytest.mark.parametrize(
    "quote,ok",
    [
        (QUOTE, True),
        ("  Texto   de\nprueba\tsobre apoyos ", True),
        ("sobre apoyos á", True),
        ("Texto de", False),
        ("prueba apoyos", False),
        ("Texto de prueba sobre apoyos á", False),
        ("Texto inventado completamente", False),
    ],
)
def test_quote_normalization_and_minimum(quote, ok):
    """Compare modulo whitespace runs and require 12 visible characters."""
    assert quote_matches(quote, CANDIDATE.text) is ok


def test_quote_is_returned_normalized():
    """Return the whitespace-normalized quote."""
    payload = {
        **VALID,
        "citations": [{"chunk_id": "c1", "quote": " Texto  de\nprueba "}],
    }
    result, *_ = run(payload)
    assert result.citations[0].quote == "Texto de prueba"


def assert_abstained(result, reason, attempted=True):
    """Check the full abstention shape of contract v1."""
    assert result.status == "abstained"
    assert (result.answer, result.citations) == (None, ())
    assert result.reason == reason and result.referral == DEFAULT_REFERRAL
    assert result.generation_attempted is attempted


def test_no_evidence():
    """Operational retrieval with nothing admissible abstains before generation."""
    result, generator, _, _ = run(candidates=())
    assert_abstained(result, "no_evidence", attempted=False)
    assert generator.calls == []


def test_model_abstained():
    """A well-formed abstention keeps reason model_abstained."""
    result, *_ = run({"status": "abstained", "answer": None, "citations": []})
    assert_abstained(result, "model_abstained")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {**VALID, "status": "unknown"},
        {**VALID, "answer": None},
        {**VALID, "answer": " \n"},
        {**VALID, "answer": 1},
        {**VALID, "citations": []},
        {**VALID, "extra": "secret"},
        {**VALID, "citations": None},
        {**VALID, "citations": [{"chunk_id": 1, "quote": None}]},
        {**VALID, "citations": [{"chunk_id": "c1", "quote": QUOTE, "page": 2}]},
        {"status": "abstained", "answer": "no", "citations": []},
        {"status": "abstained", "answer": None, "citations": VALID["citations"]},
        GeneratedText("{broken"),
        GeneratedText("```json\n{}\n```"),
        GeneratedText('{"status":"answered","status":"abstained"}'),
        GeneratedText(json.dumps(VALID) + " {}"),
        GeneratedText("null"),
        GeneratedText(""),
    ],
)
def test_invalid_output(payload):
    """Reject the whole candidate without repairs."""
    result, generator, catalog, _ = run(payload)
    assert_abstained(result, "invalid_output")
    assert len(generator.calls) == 1 and catalog.calls == ["c1"]


@pytest.mark.parametrize(
    "citations",
    [
        [{"chunk_id": "other", "quote": QUOTE}],
        [{"chunk_id": "c1", "quote": "inventada por el modelo"}],
        [{"chunk_id": "c1", "quote": " "}],
        [{"chunk_id": "c1", "quote": "apoyos"}],
        VALID["citations"] * 2,
    ],
)
def test_invalid_citation(citations):
    """Unknown chunk, non-literal or too-short quote, or duplicate chunk."""
    result, *_ = run({**VALID, "citations": citations})
    assert_abstained(result, "invalid_citation")


CHANGES = [
    None,
    {"status": "unpublished"},
    {"requires_review": True},
    {"formally_superseded": True},
    {"version_id": "v2"},
    {"page_end": 4},
    {"text_sha256": "b" * 64},
]


@pytest.mark.parametrize("changes", CHANGES)
def test_stale_source_after_generation(changes):
    """A citation whose source changed during generation discards the answer."""
    entry = replace(ENTRY, **changes) if changes is not None else None
    result, generator, *_ = run(action=lambda catalog: setattr(catalog, "entry", entry))
    assert_abstained(result, "stale_source")
    assert len(generator.calls) == 1


@pytest.mark.parametrize("changes", CHANGES)
def test_stale_before_generation_is_no_evidence(changes):
    """Ineligible evidence is filtered by retrieval before spending."""
    entry = replace(ENTRY, **changes) if changes is not None else None
    result, generator, *_ = run(entry=entry)
    assert_abstained(result, "no_evidence", attempted=False)
    assert generator.calls == []


@pytest.mark.parametrize(
    "payload,attempted",
    [
        (GenerationError("SECRET", False), False),
        (GenerationError("SECRET", True), True),
        (TimeoutError("SECRET"), True),
        (RuntimeError("SECRET"), True),
        (object(), True),
        (GeneratedText(None), True),
    ],
)
def test_generation_failure(payload, attempted, caplog):
    """Fail closed with referral; logs carry phase and type, never content."""
    result, generator, _, _ = run(payload, question="SECRET-QUESTION")
    assert result == AnswerResult(
        "unavailable", referral=DEFAULT_REFERRAL, generation_attempted=attempted
    )
    assert len(generator.calls) == 1
    assert "phase=generation" in caplog.text
    assert "SECRET-QUESTION" not in caplog.text and "prueba" not in caplog.text


def test_unconfigured_ports_are_unavailable():
    """Missing ports report unavailable with referral and never generate."""
    for retrieval, generator, catalog in [
        (None, Generator(), Catalog()),
        (RetrievalService(), Generator(), Catalog()),
        (RetrievalService(Retriever(), Catalog()), None, Catalog()),
        (RetrievalService(Retriever(), Catalog()), Generator(), None),
    ]:
        result = asyncio.run(
            service(retrieval, generator, catalog).query(AnswerQuery("x"))
        )
        assert result == AnswerResult("unavailable", referral=DEFAULT_REFERRAL)
        assert generator is None or generator.calls == []


def test_depends_on_retrieval_protocol():
    """Any object with async query() works as the retrieval port."""

    class Port:
        """Minimal retrieval port double."""

        def __init__(self, result):
            """Store the canned result."""
            self.result, self.requests = result, []

        async def query(self, request):
            """Return the canned result and record the request."""
            self.requests.append(request)
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

    port = Port(RetrievalResult("abstained"))
    answer = service(port, Generator(), Catalog(), evidence_limit=2)
    assert_abstained(
        asyncio.run(answer.query(AnswerQuery("x"))), "no_evidence", attempted=False
    )
    assert (port.requests[0].question, port.requests[0].limit) == ("x", 2)
    for result in (RetrievalResult("unavailable"), RuntimeError("SECRET")):
        answer = service(Port(result), Generator(), Catalog())
        assert asyncio.run(answer.query(AnswerQuery("x"))).status == "unavailable"


def test_catalog_failure_after_generation():
    """Discard output and report unavailable if the catalog fails post-generation."""
    result, *_ = run(action=lambda catalog: setattr(catalog, "fail", True))
    assert result == AnswerResult(
        "unavailable", referral=DEFAULT_REFERRAL, generation_attempted=True
    )


def test_budget_trims_history_then_sources():
    """Oldest history goes first, then lowest-ranked sources; else unavailable."""
    long_text = "y" * 3000
    c2, e2 = chunk("c2", long_text)
    catalog = Catalog(entries={"c2": e2})
    generator = Generator()
    retrieval = RetrievalService(Retriever([CANDIDATE, c2]), catalog)
    history = tuple(ConversationTurn("user", f"{i}" * 1500) for i in range(4))
    budget = len(SYSTEM_PROMPT) + 2500
    answer = service(retrieval, generator, catalog, max_input_chars=budget)
    result = asyncio.run(answer.query(AnswerQuery("pregunta", history)))
    assert result.status == "answered"
    sent = json.loads(generator.calls[0][1])
    assert len(SYSTEM_PROMPT) + len(generator.calls[0][1]) <= budget
    assert sent["historial"] == []
    assert [source["chunk_id"] for source in sent["fuentes"]] == ["c1"]

    big = "z" * 3000
    candidate = replace(CANDIDATE, text=big)
    entry = replace(ENTRY, text_sha256=sha256(big.encode()).hexdigest())
    result, generator, _, _ = run(
        entry=entry, candidates=(candidate,), max_input_chars=budget
    )
    assert result.status == "unavailable" and generator.calls == []


def test_history_kept_when_it_fits():
    """Recent history survives trimming before older turns."""
    history = [ConversationTurn("user", "a" * 1000), ConversationTurn("user", "b")]
    budget = len(SYSTEM_PROMPT) + 1100
    _, generator, *_ = run(history=history, max_input_chars=budget)
    sent = json.loads(generator.calls[0][1])
    assert sent["historial"] == [{"role": "user", "content": "b"}]


def test_candidate_schema_matches_model():
    """The strict JSON Schema mirrors the Pydantic candidate."""
    assert set(ANSWER_CANDIDATE_SCHEMA["required"]) == set(AnswerCandidate.model_fields)
    assert ANSWER_CANDIDATE_SCHEMA["additionalProperties"] is False
    items = ANSWER_CANDIDATE_SCHEMA["properties"]["citations"]["items"]
    assert items["additionalProperties"] is False
    assert set(items["required"]) == {"chunk_id", "quote"}


def test_invalid_configuration_and_request():
    """Reject invalid limits and untyped requests."""
    for kwargs in ({"max_input_chars": 0}, {"evidence_limit": 21}, {"referral": " "}):
        with pytest.raises(ValueError):
            service(None, None, None, **kwargs)
    with pytest.raises(TypeError):
        asyncio.run(service(None, None, None).query({"question": "x"}))

"""LangChain Core boundary: retriever, embeddings, chat model, prompt and LCEL chain.

All offline: fake transports and fake models only. The wrappers must stay thin
(our validators decide), async-only, and the chain must produce exactly the
results of the pre-LangChain orchestration for answered and every reason.
"""

import asyncio
import json
import logging
from dataclasses import replace

import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.retrievers import BaseRetriever
from pydantic import SecretStr
from test_answer import QUOTE, TODAY, VALID, Generator
from test_embeddings import Fetch as EmbeddingFetch
from test_embeddings import embedder, vector
from test_fusion import Channel, Source
from test_gateway import GOOD
from test_gateway import Fetch as GatewayFetch
from test_retrieval import CANDIDATE, ENTRY, Catalog, Retriever

from unitropico_rag.domain.answer import (
    ANSWER_CANDIDATE_SCHEMA,
    AnswerCitation,
    AnswerQuery,
    AnswerResult,
)
from unitropico_rag.domain.evidence import ChunkCandidate, RetrievalQuery
from unitropico_rag.domain.generation import (
    GeneratedText,
    GenerationError,
    StructuredOutput,
    TokenUsage,
)
from unitropico_rag.infra.embeddings import EmbeddingError, GatewayEmbeddings
from unitropico_rag.infra.gateway import GatewayClient
from unitropico_rag.services.answer import AnswerService
from unitropico_rag.services.chat_model import GeneratorChatModel, message_usage
from unitropico_rag.services.fusion import HybridRetriever
from unitropico_rag.services.prompt import (
    DEFAULT_REFERRAL,
    SYSTEM_PROMPT,
    build_answer_prompt,
)
from unitropico_rag.services.retrieval import RetrievalService
from unitropico_rag.services.retriever import (
    ChunkRetriever,
    InvalidCandidateError,
    candidate_document,
    document_candidate,
)


def run(coroutine):
    """Run a coroutine synchronously."""
    return asyncio.run(coroutine)


# --- Retriever -------------------------------------------------------------


def test_chunk_retriever_is_base_retriever_with_fusion_metadata():
    """Hybrid results become Documents with provenance, RRF score and ranks."""
    lexical, semantic = Channel(["a", "b"]), Channel(["b", "c"])
    hybrid = HybridRetriever(Source(), lexical=lexical, semantic=semantic, rrf_k=60)
    retriever = ChunkRetriever(base=hybrid)
    assert isinstance(retriever, BaseRetriever)
    documents = run(retriever.ainvoke("beca", k=2))
    assert [document.id for document in documents] == ["b", "a"]
    assert all(isinstance(document, Document) for document in documents)
    first, second = documents
    assert first.page_content == CANDIDATE.text
    assert first.metadata == {
        "document_id": "doc",
        "version_id": "v1",
        "chunk_id": "b",
        "source_sha256": "a" * 64,
        "page_start": 2,
        "page_end": 3,
        "section": None,
        "rrf_score": pytest.approx(1 / 62 + 1 / 61),
        # Channel fakes score hits 0, -1, -2...: native scores are preserved.
        "rank_lexical": 2,
        "score_lexical": -1.0,
        "rank_semantic": 1,
        "score_semantic": 0.0,
    }
    assert second.metadata["rrf_score"] == pytest.approx(1 / 61)
    assert (second.metadata["rank_lexical"], second.metadata["score_lexical"]) == (
        1,
        0.0,
    )
    assert second.metadata["rank_semantic"] is None
    assert second.metadata["score_semantic"] is None
    assert lexical.calls == [("beca", 50)]


def test_chunk_retriever_single_channel_and_default_k():
    """Lexical-only mode keeps ranks for its channel; k defaults to the field."""
    hybrid = HybridRetriever(Source(), lexical=Channel(["a", "b", "c"]))
    documents = run(ChunkRetriever(base=hybrid, k=2).ainvoke("beca"))
    assert [document.metadata["rank_lexical"] for document in documents] == [1, 2]
    assert "rank_semantic" not in documents[0].metadata


def test_chunk_retriever_over_plain_async_retriever():
    """A plain AsyncRetriever yields provenance only (no fusion trace)."""
    base = Retriever([CANDIDATE])
    documents = run(ChunkRetriever(base=base).ainvoke("pregunta", k=7))
    assert base.calls == [("pregunta", 7)]
    assert documents[0].metadata["rrf_score"] is None
    assert document_candidate(documents[0]) == CANDIDATE


def test_chunk_retriever_sync_path_refuses():
    """The sync path raises a clear error instead of using threads."""
    with pytest.raises(NotImplementedError, match="async-only"):
        ChunkRetriever(base=Retriever()).invoke("x")


@pytest.mark.parametrize(
    "changes",
    [{"page_start": True}, {"source_sha256": "bad"}, {"text": ""}, {"section": " "}],
)
def test_document_round_trip_preserves_values_for_later_validation(changes):
    """Conversion copies values verbatim; admissibility is decided by the catalog."""
    candidate = replace(CANDIDATE, **changes)
    assert document_candidate(candidate_document(candidate)) == candidate


@pytest.mark.parametrize(
    "value",
    [object(), Document(page_content="x"), Document(page_content="x", metadata={})],
)
def test_document_candidate_rejects_foreign_documents(value):
    """Documents without our provenance keys are not candidates."""
    with pytest.raises(InvalidCandidateError):
        document_candidate(value)


def test_candidate_document_rejects_impostors_and_non_text():
    """Only real ChunkCandidate objects with string text convert."""
    with pytest.raises(InvalidCandidateError):
        candidate_document(object())
    with pytest.raises(InvalidCandidateError):
        candidate_document(replace(CANDIDATE, text=None))


def test_retrieval_service_uses_base_retriever_and_keeps_revalidation():
    """RetrievalService wraps plain retrievers and accepts any BaseRetriever."""
    service = RetrievalService(Retriever([CANDIDATE]), Catalog())
    assert isinstance(service.retriever, ChunkRetriever)

    class Custom(BaseRetriever):
        """A third-party style retriever returning our documents."""

        def _get_relevant_documents(self, query, *, run_manager):
            raise NotImplementedError

        async def _aget_relevant_documents(self, query, *, run_manager):
            return [candidate_document(CANDIDATE)]

    custom = RetrievalService(Custom(), Catalog(replace(ENTRY, status="draft")))
    result = run(custom.query(RetrievalQuery(question="x")))
    assert result.status == "abstained"  # catalog still decides
    published = RetrievalService(Custom(), Catalog())
    assert run(published.query(RetrievalQuery(question="x"))).status == (
        "evidence_found"
    )


# --- Embeddings --------------------------------------------------------------


def test_embeddings_async_query_and_documents():
    """aembed_query and aembed_documents delegate to GatewayEmbedder."""
    fetch = EmbeddingFetch({"data": [{"index": 0, "embedding": vector(1)}]})
    embeddings = GatewayEmbeddings(embedder(fetch))
    assert isinstance(embeddings, Embeddings) and embeddings.dimensions == 4
    assert run(embeddings.aembed_query("hola")) == vector(1)
    body = json.loads(fetch.calls[0][1]["body"])
    assert body == {"model": "openai/text-embedding-3-small", "input": ["hola"]}
    assert run(embeddings.aembed_documents(["a"])) == [vector(1)]


def test_embeddings_documents_are_batched():
    """More than 100 texts are sent in sequential batches, order preserved."""

    class Echo(EmbeddingFetch):
        async def json(self):
            texts = json.loads(self.calls[-1][1]["body"])["input"]
            return {
                "data": [
                    {"index": i, "embedding": vector(float(t))}
                    for i, t in enumerate(texts)
                ]
            }

    fetch = Echo()
    texts = [str(n) for n in range(150)]
    vectors = run(GatewayEmbeddings(embedder(fetch)).aembed_documents(texts))
    assert vectors == [vector(float(t)) for t in texts]
    sizes = [len(json.loads(call[1]["body"])["input"]) for call in fetch.calls]
    assert sizes == [100, 50]


def test_embeddings_sync_methods_refuse():
    """Sync methods raise: LangChain's default fallbacks would need threads."""
    embeddings = GatewayEmbeddings(embedder(EmbeddingFetch()))
    with pytest.raises(NotImplementedError, match="async-only"):
        embeddings.embed_query("x")
    with pytest.raises(NotImplementedError, match="async-only"):
        embeddings.embed_documents(["x"])


def test_embeddings_errors_propagate_and_inputs_validated():
    """Provider errors keep their code; bad inputs never reach the network."""
    fetch = EmbeddingFetch(status=500)
    with pytest.raises(EmbeddingError) as caught:
        run(GatewayEmbeddings(embedder(fetch)).aembed_query("x"))
    assert caught.value.code == "http_error"
    quiet = EmbeddingFetch()
    for bad in ("text", [], None):
        with pytest.raises(EmbeddingError):
            run(GatewayEmbeddings(embedder(quiet)).aembed_documents(bad))
    with pytest.raises(EmbeddingError):
        run(GatewayEmbeddings(embedder(quiet)).aembed_query(None))
    assert quiet.calls == []
    with pytest.raises(EmbeddingError):
        GatewayEmbeddings(object())


# --- Chat model --------------------------------------------------------------

MESSAGES = [SystemMessage(content="sistema"), HumanMessage(content='{"q": 1}')]


def test_chat_model_maps_content_and_usage():
    """Content and token counts surface in AIMessage metadata."""
    usage = TokenUsage(prompt_tokens=11, completion_tokens=5, total_tokens=16)
    generator = Generator(GeneratedText("salida", usage))
    model = GeneratorChatModel(generator=generator, model_name="openai/gpt-6-luna")
    assert isinstance(model, BaseChatModel)
    message = run(model.ainvoke(MESSAGES))
    assert isinstance(message, AIMessage) and message.content == "salida"
    assert generator.calls == [("sistema", '{"q": 1}')]
    assert message.usage_metadata == {
        "input_tokens": 11,
        "output_tokens": 5,
        "total_tokens": 16,
    }
    assert message.response_metadata["token_usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 5,
        "total_tokens": 16,
    }
    assert message.response_metadata["model_name"] == "openai/gpt-6-luna"
    assert message_usage(message) == usage


@pytest.mark.parametrize(
    "usage,standard",
    [
        (None, None),
        (TokenUsage(total_tokens=7), None),
        (
            TokenUsage(prompt_tokens=2, completion_tokens=3),
            {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        ),
    ],
)
def test_chat_model_partial_usage(usage, standard):
    """Unknown counts stay unknown; standard metadata needs input and output."""
    message = run(
        GeneratorChatModel(generator=Generator(GeneratedText("x", usage))).ainvoke(
            MESSAGES
        )
    )
    assert message.usage_metadata == standard
    assert message_usage(message) == usage


@pytest.mark.parametrize(
    "messages,kwargs",
    [
        ([HumanMessage(content="x")], {}),
        ([HumanMessage(content="x"), SystemMessage(content="y")], {}),
        ([SystemMessage(content="s"), HumanMessage(content=[{"type": "text"}])], {}),
        (MESSAGES, {"stop": ["}"]}),
        (MESSAGES, {"tools": []}),
    ],
)
def test_chat_model_rejects_unexpected_input(messages, kwargs):
    """Only one system + one human string message, no stop or extra kwargs."""
    generator = Generator()
    with pytest.raises(GenerationError) as caught:
        run(GeneratorChatModel(generator=generator).ainvoke(messages, **kwargs))
    assert caught.value.code == "invalid_input"
    assert caught.value.request_attempted is False
    assert generator.calls == []


@pytest.mark.parametrize(
    "payload,code,attempted",
    [
        (GenerationError("timeout", True), "timeout", True),
        (GenerationError("invalid_input", False), "invalid_input", False),
        (GeneratedText(None), "invalid_response", True),
        (object(), "invalid_response", True),
    ],
)
def test_chat_model_errors(payload, code, attempted):
    """Generator errors propagate unchanged; malformed results are rejected."""
    with pytest.raises(GenerationError) as caught:
        run(GeneratorChatModel(generator=Generator(payload)).ainvoke(MESSAGES))
    assert (caught.value.code, caught.value.request_attempted) == (code, attempted)


def test_chat_model_sync_path_refuses():
    """invoke() reaches _generate, which refuses instead of using threads."""
    with pytest.raises(NotImplementedError, match="async-only"):
        GeneratorChatModel(generator=Generator()).invoke(MESSAGES)


def gateway(fetch):
    """Real GatewayClient with the strict answer schema and a fake transport."""
    return GatewayClient(
        model="openai/gpt-6-luna",
        api_key=SecretStr("fake-key"),
        fetch=fetch,
        url="https://gateway.test/v1/chat/completions",
        timeout=1,
        max_tokens=2048,
        max_input_chars=20000,
        reasoning_effort="low",
        structured_output=StructuredOutput("answer_candidate", ANSWER_CANDIDATE_SCHEMA),
    )


def test_chat_model_over_gateway_keeps_provider_controls():
    """The request still carries strict json_schema, reasoning_effort, max_tokens."""
    payload = {**GOOD, "usage": {"prompt_tokens": 9, "completion_tokens": 4}}
    fetch = GatewayFetch(payload)
    message = run(GeneratorChatModel(generator=gateway(fetch)).ainvoke(MESSAGES))
    assert message.content == " Candidato á "
    assert message.usage_metadata["total_tokens"] == 13
    body = json.loads(fetch.calls[0][1]["body"])
    assert body["messages"] == [
        {"role": "system", "content": "sistema"},
        {"role": "user", "content": '{"q": 1}'},
    ]
    assert (body["max_tokens"], body["reasoning_effort"]) == (2048, "low")
    assert body["response_format"]["json_schema"]["strict"] is True
    assert fetch.calls[0][1]["redirect"] == "manual"


@pytest.mark.parametrize(
    "choice,code",
    [
        ({"message": {"role": "assistant", "content": "x"}, "finish_reason": "length"},
         "truncated"),
        ({"message": {"role": "assistant", "content": "x", "tool_calls": []},
          "finish_reason": "stop"}, "invalid_response"),
    ],
)  # fmt: skip
def test_chat_model_over_gateway_rejects_truncation_and_tool_calls(choice, code):
    """GatewayClient's rejections surface as GenerationError through the model."""
    fetch = GatewayFetch({"choices": [choice]})
    with pytest.raises(GenerationError) as caught:
        run(GeneratorChatModel(generator=gateway(fetch)).ainvoke(MESSAGES))
    assert caught.value.code == code and caught.value.request_attempted is True


# --- Prompt ------------------------------------------------------------------


def test_prompt_template_keeps_system_literal_and_content_verbatim():
    """System braces are not template fields; user content passes unchanged."""
    content = '{"pregunta": "¿{x}?", "fuentes": []}'
    value = run(build_answer_prompt().ainvoke({"content": content}))
    system, human = value.to_messages()
    assert isinstance(system, SystemMessage) and system.content == SYSTEM_PROMPT
    assert isinstance(human, HumanMessage) and human.content == content
    assert build_answer_prompt().input_variables == ["content"]


# --- LCEL chain equivalence ---------------------------------------------------


class FakeChatModel(BaseChatModel):
    """Third-party style chat model returning a canned AIMessage."""

    content: str
    usage: dict[str, int] | None = None

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        message = AIMessage(content=self.content, usage_metadata=self.usage)
        return ChatResult(generations=[ChatGeneration(message=message)])


CITATION = AnswerCitation(
    chunk_id="c1",
    document_id="doc",
    document_title=ENTRY.document_title,
    version_id="v1",
    version_label="2026-1",
    section=None,
    page_start=2,
    page_end=3,
    quote=QUOTE,
)
REFERRAL = DEFAULT_REFERRAL


def abstained(reason, usage=None, attempted=True):
    """Expected abstention result."""
    return AnswerResult(
        "abstained",
        reason=reason,
        referral=REFERRAL,
        generation_attempted=attempted,
        usage=usage,
    )


USAGE = TokenUsage(total_tokens=7)
CASES = [
    ("answered", VALID, None, AnswerResult(
        "answered", "Respuesta", citations=(CITATION,),
        generation_attempted=True, usage=USAGE)),
    ("model_abstained", {"status": "abstained", "answer": None, "citations": []},
     None, abstained("model_abstained", USAGE)),
    ("invalid_output", {**VALID, "extra": 1}, None, abstained("invalid_output", USAGE)),
    ("invalid_citation", {**VALID, "citations": [{"chunk_id": "c1", "quote": "no"}]},
     None, abstained("invalid_citation", USAGE)),
    ("stale_source", VALID, {"status": "unpublished"},
     abstained("stale_source", USAGE)),
]  # fmt: skip


def answer_with(model, *, candidates=(CANDIDATE,), stale=None):
    """Run AnswerService with the given chat model or generator."""
    catalog = Catalog()
    if stale is not None:
        inner = model

        class Staling(Generator):
            async def complete(self, system_prompt, user_content):
                catalog.entry = replace(ENTRY, **stale)
                return await inner.complete(system_prompt, user_content)

        model = Staling()
    service = AnswerService(
        RetrievalService(Retriever(candidates), catalog),
        model,
        catalog,
        max_input_chars=20000,
        clock=lambda: TODAY,
    )
    return run(service.query(AnswerQuery("pregunta")))


@pytest.mark.parametrize(
    "name,payload,stale,expected", CASES, ids=[c[0] for c in CASES]
)
def test_chain_matches_previous_results(name, payload, stale, expected):
    """Plain generator, explicit GeneratorChatModel: identical outcomes."""
    assert answer_with(Generator(payload), stale=stale) == expected
    if stale is None:
        wrapped = GeneratorChatModel(generator=Generator(payload))
        assert answer_with(wrapped) == expected


def test_no_evidence_skips_the_chain():
    """no_evidence never invokes the chat model."""
    generator = Generator()
    assert answer_with(generator, candidates=()) == abstained(
        "no_evidence", attempted=False
    )
    assert generator.calls == []


@pytest.mark.parametrize(
    "payload,expected",
    [
        (VALID, "answered"),
        ({"status": "abstained", "answer": None, "citations": []}, "model_abstained"),
        ({}, "invalid_output"),
        ({**VALID, "citations": [{"chunk_id": "zz", "quote": QUOTE}]},
         "invalid_citation"),
    ],
)  # fmt: skip
def test_chain_accepts_any_base_chat_model(payload, expected):
    """A non-adapter BaseChatModel works; usage comes from usage_metadata."""
    usage = {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
    result = answer_with(FakeChatModel(content=json.dumps(payload), usage=usage))
    assert (result.reason or result.status) == expected
    assert result.usage == TokenUsage(3, 2, 5)


def test_chain_shape():
    """The chain is prompt -> model (assigned) -> parse -> verify -> revalidate."""
    service = AnswerService(
        RetrievalService(Retriever(), Catalog()),
        Generator(),
        Catalog(),
        max_input_chars=20000,
    )
    steps = [step.get_name() for step in service.chain.steps]  # type: ignore[union-attr]
    assert steps[1:] == ["parse_candidate", "verify_citations", "revalidate_catalog"]
    unconfigured = AnswerService(None, None, None, max_input_chars=20000)
    assert unconfigured.chain is None


# --- Metrics log ---------------------------------------------------------------


def test_metrics_log_has_phases_and_tokens_without_content(caplog):
    """INFO answer_metrics: status, reason, latencies, tokens; never content."""
    ticks = iter(float(n) for n in range(100))
    usage = TokenUsage(prompt_tokens=120, completion_tokens=30, total_tokens=150)

    class Usage(Generator):
        async def complete(self, system_prompt, user_content):
            self.calls.append((system_prompt, user_content))
            return GeneratedText(json.dumps(VALID), usage)

    catalog = Catalog()
    service = AnswerService(
        RetrievalService(Retriever([CANDIDATE]), catalog),
        Usage(),
        catalog,
        max_input_chars=20000,
        timer=lambda: next(ticks),
    )
    with caplog.at_level(logging.INFO, logger="unitropico_rag"):
        result = run(service.query(AnswerQuery("SECRET-QUESTION")))
    assert result.status == "answered" and result.usage == usage
    (line,) = [r.getMessage() for r in caplog.records if "answer_metrics" in r.message]
    # timer: start=0, retrieved=1, chain=2, generated=3, end=4 (ms = s*1000).
    assert line == (
        "answer_metrics status=answered reason=- sources=1 retrieval_ms=1000.0 "
        "generation_ms=1000.0 validation_ms=1000.0 prompt_tokens=120 "
        "completion_tokens=30 total_tokens=150 followup_strategy=none "
        "followup_used=none followup_fallback=false rewrite_ms=- rewrite_tokens=- "
        "threshold=off below_threshold=false dropped_citations=0"
    )
    assert "SECRET" not in caplog.text and "prueba" not in caplog.text


def test_metrics_log_for_abstention_before_generation(caplog):
    """no_evidence logs retrieval latency only."""
    service = AnswerService(
        RetrievalService(Retriever([]), Catalog()),
        Generator(),
        Catalog(),
        max_input_chars=20000,
        timer=iter([0.0, 0.5, 0.5]).__next__,
    )
    with caplog.at_level(logging.INFO, logger="unitropico_rag"):
        run(service.query(AnswerQuery("x")))
    assert (
        "answer_metrics status=abstained reason=no_evidence sources=0 "
        "retrieval_ms=500.0 generation_ms=- validation_ms=- prompt_tokens=- "
        "completion_tokens=- total_tokens=- followup_strategy=none "
        "followup_used=none followup_fallback=false rewrite_ms=- rewrite_tokens=- "
        "threshold=off below_threshold=false dropped_citations=0"
    ) in caplog.text


def test_candidate_type_is_dataclass():
    """Document conversion yields the domain dataclass, not a Document."""
    assert isinstance(document_candidate(candidate_document(CANDIDATE)), ChunkCandidate)

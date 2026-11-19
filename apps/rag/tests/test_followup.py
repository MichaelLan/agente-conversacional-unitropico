"""Follow-up retrieval strategies (none/concat/rewrite) and the relevance gate.

Everything is offline: fake generators/rewriters, fake retrievers and, for the
composition tests, the real D1 migration on sqlite3 with fake Vectorize and a
fake fetch transport.
"""

import asyncio
import json
import logging

import pytest
from d1_support import seeded
from pydantic import SecretStr
from test_answer import Generator, service
from test_d1_integration import Response, Transport, Vectorize
from test_retrieval import CANDIDATE, Catalog, Retriever
from test_retrieval_http import request

from unitropico_rag.core.settings import Settings, SettingsError
from unitropico_rag.domain.answer import (
    STANDALONE_QUESTION_SCHEMA,
    AnswerQuery,
    ConversationTurn,
)
from unitropico_rag.domain.generation import GeneratedText, GenerationError, TokenUsage
from unitropico_rag.main import Bindings, build_app
from unitropico_rag.services.answer import (
    CONCAT_MAX_WORDS,
    MAX_RETRIEVAL_CHARS,
    AnswerService,
    concat_question,
)
from unitropico_rag.services.fusion import ChannelHit, FusedCandidate
from unitropico_rag.services.prompt import REWRITE_SYSTEM_PROMPT, SYSTEM_PROMPT
from unitropico_rag.services.retrieval import RetrievalService

PREVIOUS = "¿Qué promedio necesito para el apoyo de alimentación?"
FOLLOW_UP = "¿Y cuáles son los requisitos?"
HISTORY = (
    ConversationTurn("user", PREVIOUS),
    ConversationTurn("assistant", "Se requiere un promedio igual o superior a 3.0."),
)
STANDALONE = "¿Cuáles son los requisitos del apoyo de alimentación?"


class Rewriter:
    """Fake rewrite generator: canned payload, exception or a slow response."""

    def __init__(self, payload=None, delay=0.0):
        """Default to a valid standalone question."""
        self.payload = {"question": STANDALONE} if payload is None else payload
        self.delay, self.calls = delay, []

    async def complete(self, system_prompt, user_content):
        """Record the envelope and return/raise the configured outcome."""
        self.calls.append((system_prompt, user_content))
        await asyncio.sleep(self.delay)
        if isinstance(self.payload, BaseException):
            raise self.payload
        content = self.payload if isinstance(self.payload, str) else None
        if content is None:
            content = json.dumps(self.payload)
        return GeneratedText(content, TokenUsage(3, 2, 5))


def answer(question=FOLLOW_UP, history=HISTORY, **kwargs):
    """Run AnswerService with fake ports; return result, retriever and generator."""
    catalog, retriever, generator = Catalog(), Retriever((CANDIDATE,)), Generator()
    svc = service(RetrievalService(retriever, catalog), generator, catalog, **kwargs)
    result = asyncio.run(svc.query(AnswerQuery(question, tuple(history))))
    return result, retriever, generator


def metrics(caplog) -> str:
    """Return the single answer_metrics line."""
    (line,) = [r.getMessage() for r in caplog.records if "answer_metrics" in r.message]
    return line


# --- concat ----------------------------------------------------------------------


def test_none_strategy_retrieves_with_current_question_only():
    """``none`` (and the service default) keeps today's behavior."""
    for kwargs in ({}, {"followup_strategy": "none"}):
        _, retriever, _ = answer(**kwargs)
        assert retriever.calls == [(FOLLOW_UP, 15)]


def test_concat_prefixes_last_user_question_for_short_questions():
    """Short question + user turn in history → '<previous> <current>'."""
    _, retriever, _ = answer(followup_strategy="concat")
    assert retriever.calls == [(f"{PREVIOUS} {FOLLOW_UP}", 15)]


def test_concat_rule_boundaries():
    """Word limit, last *user* turn, no history, whitespace and 500-char bound."""
    at_limit = " ".join(["palabra"] * CONCAT_MAX_WORDS)
    over_limit = " ".join(["palabra"] * (CONCAT_MAX_WORDS + 1))
    assert concat_question(AnswerQuery(at_limit, HISTORY)) == f"{PREVIOUS} {at_limit}"
    assert concat_question(AnswerQuery(over_limit, HISTORY)) is None
    assert concat_question(AnswerQuery(FOLLOW_UP, ())) is None
    only_assistant = (ConversationTurn("assistant", "Hola"),)
    assert concat_question(AnswerQuery(FOLLOW_UP, only_assistant)) is None
    turns = (
        ConversationTurn("user", "primera\n  pregunta"),
        ConversationTurn("assistant", "respuesta"),
        ConversationTurn("user", "segunda   pregunta"),
        ConversationTurn("assistant", "respuesta"),
    )
    assert concat_question(AnswerQuery("¿y eso?", turns)) == "segunda pregunta ¿y eso?"
    long_previous = (ConversationTurn("user", "x" * 2000),)
    combined = concat_question(AnswerQuery(FOLLOW_UP, long_previous))
    assert combined is not None and len(combined) == MAX_RETRIEVAL_CHARS
    assert combined.endswith(" " + FOLLOW_UP)


def test_concat_without_history_or_long_question_is_unchanged():
    """The rule does not apply: retrieval uses the current question."""
    _, retriever, _ = answer(followup_strategy="concat", history=())
    long_question = (
        "¿Cuáles son todos los requisitos para solicitar el apoyo de transporte?"
    )
    _, other, _ = answer(followup_strategy="concat", question=long_question)
    assert retriever.calls == [(FOLLOW_UP, 15)]
    assert other.calls == [(long_question, 15)]


@pytest.mark.parametrize("strategy", ["concat", "rewrite"])
def test_generation_receives_original_question_and_history(strategy):
    """Only retrieval changes: the prompt keeps the original question + history."""
    result, retriever, generator = answer(
        followup_strategy=strategy, rewriter=Rewriter()
    )
    assert result.status == "answered"
    assert retriever.calls[0][0] != FOLLOW_UP
    system, user = generator.calls[0]
    assert system == SYSTEM_PROMPT
    data = json.loads(user)
    assert data["pregunta"] == FOLLOW_UP
    assert data["historial"] == [
        {"role": turn.role, "content": turn.content} for turn in HISTORY
    ]


# --- rewrite ---------------------------------------------------------------------


def test_rewrite_uses_structured_question_and_sends_history_as_data():
    """The rewrite chain gets the rewrite prompt and JSON data; output is used."""
    rewriter = Rewriter()
    _, retriever, _ = answer(followup_strategy="rewrite", rewriter=rewriter)
    assert retriever.calls == [(STANDALONE, 15)]
    ((system, user),) = rewriter.calls
    assert system == REWRITE_SYSTEM_PROMPT
    assert json.loads(user) == {
        "historial": [{"role": t.role, "content": t.content} for t in HISTORY],
        "pregunta": FOLLOW_UP,
    }


def test_rewrite_collapses_whitespace_and_skips_without_history():
    """Rewritten text is normalized; with no history no rewrite call is made."""
    rewriter = Rewriter({"question": "  ¿Requisitos\n del  apoyo? "})
    _, retriever, _ = answer(followup_strategy="rewrite", rewriter=rewriter)
    assert retriever.calls == [("¿Requisitos del apoyo?", 15)]
    idle = Rewriter()
    _, retriever, _ = answer(followup_strategy="rewrite", rewriter=idle, history=())
    assert idle.calls == [] and retriever.calls == [(FOLLOW_UP, 15)]


@pytest.mark.parametrize(
    "payload,code",
    [
        ("no es json", "invalid_output"),
        ('{"question": "a", "question": "b"}', "invalid_output"),
        ({"question": "q", "extra": 1}, "invalid_output"),
        ({"question": 7}, "invalid_output"),
        ({"question": "   "}, "invalid_output"),
        ({"question": "x" * (MAX_RETRIEVAL_CHARS + 1)}, "invalid_output"),
        (["question"], "invalid_output"),
        (GenerationError("truncated", request_attempted=True), "truncated"),
        (RuntimeError("SECRET"), "RuntimeError"),
    ],
)
def test_rewrite_falls_back_to_concat(payload, code, caplog):
    """Invalid output or provider errors → concat, logged without content."""
    with caplog.at_level(logging.INFO, logger="unitropico_rag"):
        result, retriever, generator = answer(
            followup_strategy="rewrite", rewriter=Rewriter(payload)
        )
    assert result.status == "answered" and len(generator.calls) == 1
    assert retriever.calls == [(f"{PREVIOUS} {FOLLOW_UP}", 15)]
    assert f"answer_rewrite_fallback error={code}" in caplog.text
    line = metrics(caplog)
    assert "followup_strategy=rewrite followup_used=concat followup_fallback=true" in (
        line
    )
    assert "SECRET" not in caplog.text and "alimentación" not in caplog.text


def test_rewrite_timeout_falls_back_to_concat(caplog):
    """A slow rewriter is cut by rewrite_timeout and concat is used."""
    with caplog.at_level(logging.INFO, logger="unitropico_rag"):
        _, retriever, _ = answer(
            followup_strategy="rewrite",
            rewriter=Rewriter(delay=5),
            rewrite_timeout=0.01,
        )
    assert retriever.calls == [(f"{PREVIOUS} {FOLLOW_UP}", 15)]
    assert "answer_rewrite_fallback error=timeout" in caplog.text


def test_rewrite_requires_a_rewriter_when_generation_is_configured():
    """Misconfiguration fails at construction, not per request."""
    with pytest.raises(ValueError):
        service(None, Generator(), Catalog(), followup_strategy="rewrite")
    # Unconfigured service (no generator) stays unavailable rather than failing.
    AnswerService(None, None, None, max_input_chars=20000, followup_strategy="rewrite")
    with_rewriter = service(
        None, Generator(), Catalog(), followup_strategy="rewrite", rewriter=Rewriter()
    )
    assert with_rewriter.rewrite_chain is not None
    assert service(None, Generator(), Catalog()).rewrite_chain is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"followup_strategy": "magic"},
        {"rewrite_timeout": 0},
        {"rewrite_timeout": 61},
        {"rewrite_timeout": float("nan")},
        {"min_relevance": 0},
        {"min_relevance": 1.5},
        {"min_relevance": "0.5"},
        {"rewrite_system_prompt": " "},
    ],
)
def test_service_rejects_invalid_followup_or_threshold_config(kwargs):
    """Constructor validation for the new options."""
    with pytest.raises(ValueError):
        service(None, Generator(), Catalog(), **kwargs)


# --- metrics ---------------------------------------------------------------------


def test_metrics_record_strategy_without_content(caplog):
    """answer_metrics: strategy, effective rule, fallback, rewrite cost; no text."""
    ticks = iter(float(n) for n in range(100))
    with caplog.at_level(logging.INFO, logger="unitropico_rag"):
        answer(followup_strategy="rewrite", rewriter=Rewriter(), timer=ticks.__next__)
    line = metrics(caplog)
    # timer: start=0, rewrite 1→2, retrieved=3 (retrieval_ms excludes rewrite).
    assert "retrieval_ms=1000.0 " in line
    assert line.endswith(
        "followup_strategy=rewrite followup_used=rewrite followup_fallback=false "
        "rewrite_ms=1000.0 rewrite_tokens=5 threshold=off below_threshold=false"
    )
    for fragment in ("requisitos", "alimentación", "promedio", "Texto"):
        assert fragment not in caplog.text
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="unitropico_rag"):
        answer(followup_strategy="concat")
    assert (
        "followup_strategy=concat followup_used=concat followup_fallback=false "
        "rewrite_ms=- rewrite_tokens=-"
    ) in metrics(caplog)


# --- relevance threshold ---------------------------------------------------------


class Fused:
    """Fused retriever fake carrying a semantic (cosine) score per candidate."""

    channels = ("lexical", "semantic")

    def __init__(self, semantic_score):
        """None means the semantic channel did not return the chunk."""
        self.semantic_score, self.calls = semantic_score, []

    async def retrieve_fused(self, question, limit):
        """Return the test candidate with its fusion trace."""
        self.calls.append((question, limit))
        trace = {"lexical": ChannelHit(1, -3.2)}
        if self.semantic_score is not None:
            trace["semantic"] = ChannelHit(1, self.semantic_score)
        return [FusedCandidate(CANDIDATE, 0.03, trace)]

    async def retrieve(self, question, limit):
        """Unused plain path (protocol completeness)."""
        return [item.candidate for item in await self.retrieve_fused(question, limit)]


def gated(score, min_relevance):
    """Run one question through RetrievalService(Fused) and the gate."""
    catalog, generator = Catalog(), Generator()
    retrieval = RetrievalService(Fused(score), catalog)
    svc = service(retrieval, generator, catalog, min_relevance=min_relevance)
    result = asyncio.run(svc.query(AnswerQuery("¿Clima en Yopal?")))
    return result, generator


def test_retrieval_result_reports_best_semantic_score():
    """RetrievalService exposes the cosine score internally (not over HTTP)."""
    from unitropico_rag.domain.evidence import RetrievalQuery

    for score, expected in ((0.42, 0.42), (None, None)):
        result = asyncio.run(
            RetrievalService(Fused(score), Catalog()).query(
                RetrievalQuery(question="q")
            )
        )
        assert result.status == "evidence_found" and result.relevance == expected


def test_threshold_disabled_changes_nothing(caplog):
    """Default (None): a low score still reaches generation."""
    with caplog.at_level(logging.INFO, logger="unitropico_rag"):
        result, generator = gated(0.05, None)
    assert result.status == "answered" and len(generator.calls) == 1
    assert "threshold=off below_threshold=false" in metrics(caplog)


def test_threshold_below_abstains_with_no_evidence(caplog):
    """Best cosine below MIN_RELEVANCE → no_evidence without calling the LLM."""
    with caplog.at_level(logging.INFO, logger="unitropico_rag"):
        result, generator = gated(0.2, 0.5)
    assert (result.status, result.reason) == ("abstained", "no_evidence")
    assert result.generation_attempted is False and generator.calls == []
    assert "threshold=below below_threshold=true" in metrics(caplog)


def test_threshold_pass_and_unscored_generate(caplog):
    """At/above the threshold, or without semantic scores, generation proceeds."""
    with caplog.at_level(logging.INFO, logger="unitropico_rag"):
        passed, _ = gated(0.5, 0.5)
        unscored, _ = gated(None, 0.5)
    assert passed.status == unscored.status == "answered"
    assert "threshold=pass below_threshold=false" in caplog.text
    assert "threshold=unscored below_threshold=false" in caplog.text


# --- settings and composition ----------------------------------------------------


def test_followup_and_threshold_settings():
    """Defaults: concat, threshold off; values parse; invalid ones are rejected."""
    defaults = Settings.from_mapping({"MIN_RELEVANCE": ""})
    assert defaults.followup_strategy == "concat" and defaults.min_relevance is None
    assert (defaults.rewrite_max_tokens, defaults.rewrite_timeout_seconds) == (256, 8)
    settings = Settings.from_mapping(
        {
            "FOLLOWUP_STRATEGY": "rewrite",
            "FOLLOWUP_REWRITE_MAX_TOKENS": 128,
            "FOLLOWUP_REWRITE_TIMEOUT_SECONDS": "5",
            "MIN_RELEVANCE": 0.35,
        }
    )
    assert settings.followup_strategy == "rewrite"
    assert (settings.rewrite_max_tokens, settings.rewrite_timeout_seconds) == (128, 5)
    assert settings.min_relevance == 0.35
    for invalid in (
        {"FOLLOWUP_STRATEGY": "hyde"},
        {"FOLLOWUP_REWRITE_MAX_TOKENS": 0},
        {"FOLLOWUP_REWRITE_TIMEOUT_SECONDS": 120},
        {"MIN_RELEVANCE": "0"},
        {"MIN_RELEVANCE": "1.2"},
        {"MIN_RELEVANCE": "alto"},
    ):
        with pytest.raises(SettingsError):
            Settings.from_mapping(invalid)


class RewriteTransport(Transport):
    """Serve the rewrite schema with a standalone question, the rest as usual."""

    def __init__(self, question, **kwargs):
        """Store the rewritten question returned for the rewrite schema."""
        super().__init__(**kwargs)
        self.question = question

    async def __call__(self, url, **kwargs):
        """Answer the rewrite request; delegate everything else."""
        body = json.loads(kwargs["body"])
        schema = body.get("response_format", {}).get("json_schema", {})
        if schema.get("name") == "standalone_question":
            self.calls.append((url, body))
            message = {"role": "assistant", "content": json.dumps(self.question)}
            choice = {"message": message, "finish_reason": "stop"}
            return Response({"choices": [choice]})
        return await super().__call__(url, **kwargs)


def compose(transport, vectorize, **settings):
    """Build the Worker app with the real D1 schema and fake bindings."""
    config = Settings(ai_gateway_api_key=SecretStr("fake-key"), **settings)
    return build_app(config, fetch=transport, bindings=Bindings(seeded(), vectorize))


def test_composition_rewrite_sends_strict_schema_with_low_token_cap():
    """FOLLOWUP_STRATEGY=rewrite: one extra call, strict json_schema, own limits."""
    transport = RewriteTransport({"question": "¿Hasta cuándo es la inscripción?"})
    app = compose(
        transport,
        Vectorize(["chunk-beca"]),
        followup_strategy="rewrite",
        rewrite_max_tokens=128,
    )
    payload = {
        "question": "¿Y hasta cuándo?",
        "history": [{"role": "user", "content": "¿Cómo pido la beca?"}],
    }
    status, body = request(app, payload, path="/answer")
    assert status == 200 and body["status"] == "answered"
    assert transport.urls() == ["completions", "embeddings", "completions"]
    rewrite_body = transport.calls[0][1]
    assert rewrite_body["max_tokens"] == 128
    assert rewrite_body["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "standalone_question",
            "strict": True,
            "schema": STANDALONE_QUESTION_SCHEMA,
        },
    }
    assert rewrite_body["messages"][0]["content"] == REWRITE_SYSTEM_PROMPT
    # The answer request still carries the original question.
    answer_body = transport.calls[2][1]
    assert answer_body["response_format"]["json_schema"]["name"] == "answer_candidate"
    data = json.loads(answer_body["messages"][1]["content"])
    assert data["pregunta"] == "¿Y hasta cuándo?"


def test_composition_default_concat_makes_no_extra_call():
    """Default settings (concat) never add an LLM call."""
    transport = Transport()
    app = compose(transport, Vectorize(["chunk-beca"]))
    payload = {
        "question": "¿Y hasta cuándo?",
        "history": [{"role": "user", "content": "¿Cómo pido la beca?"}],
    }
    status, _ = request(app, payload, path="/answer")
    assert status == 200
    assert transport.urls() == ["embeddings", "completions"]
    assert transport.calls[0][1]["input"] == ["¿Cómo pido la beca? ¿Y hasta cuándo?"]


@pytest.mark.parametrize("min_relevance,expected", [(0.95, "abstained"), (0.5, None)])
def test_composition_min_relevance_uses_vectorize_cosine(min_relevance, expected):
    """Fake Vectorize scores the beca chunk 0.9: gate at 0.95 abstains, 0.5 passes."""
    transport = Transport()
    app = compose(transport, Vectorize(["chunk-beca"]), min_relevance=min_relevance)
    status, body = request(
        app, {"question": "¿Hasta cuándo hay inscripción a la beca?"}, path="/answer"
    )
    assert status == 200
    if expected == "abstained":
        assert (body["status"], body["reason"]) == ("abstained", "no_evidence")
        assert transport.urls() == ["embeddings"]
    else:
        assert body["status"] == "answered"

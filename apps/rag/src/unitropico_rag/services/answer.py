import asyncio
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Any, Protocol, get_args

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable, RunnableLambda, RunnablePassthrough

from unitropico_rag.core.logger import logger
from unitropico_rag.domain.answer import (
    AbstentionReason,
    AnswerCandidate,
    AnswerCitation,
    AnswerQuery,
    AnswerResult,
    ConversationTurn,
    FollowupStrategy,
    StandaloneQuestion,
)
from unitropico_rag.domain.evidence import (
    Evidence,
    RetrievalQuery,
    RetrievalResult,
    is_admissible,
)
from unitropico_rag.domain.generation import (
    GenerationError,
    TextGenerator,
    TokenUsage,
)
from unitropico_rag.services.chat_model import GeneratorChatModel, message_usage
from unitropico_rag.services.prompt import (
    DEFAULT_REFERRAL,
    REWRITE_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_answer_prompt,
    build_rewrite_prompt,
)
from unitropico_rag.services.quotes import match_quote
from unitropico_rag.services.retrieval import AsyncEvidenceCatalog

# concat rule: a question of at most this many whitespace-separated words is
# treated as a follow-up and prefixed with the last user question of history.
CONCAT_MAX_WORDS = 10
# Same bound as RetrievalQuery.question.
MAX_RETRIEVAL_CHARS = 500


class EvidenceRetrieval(Protocol):
    """Return admissible evidence for one retrieval query."""

    async def query(self, request: RetrievalQuery) -> RetrievalResult:
        """Retrieve and revalidate evidence or report unavailable/abstained."""
        ...


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON keys through the standard parser, without repairing."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def normalize_whitespace(text: str) -> str:
    """Collapse every run of Unicode whitespace to one space and trim the ends."""
    return " ".join(text.split())


def _pages(item: Evidence) -> str:
    """Render the physical page range as shown to the model."""
    if item.page_start == item.page_end:
        return str(item.page_start)
    return f"{item.page_start}-{item.page_end}"


def _source(item: Evidence) -> dict[str, object]:
    """Describe one source with catalog metadata for the prompt."""
    return {
        "chunk_id": item.chunk_id,
        "titulo": item.document_title,
        "version": item.version_label,
        "vigencia": item.validity_label,
        "vigente_desde": item.effective_from and item.effective_from.isoformat(),
        "vigente_hasta": item.effective_until and item.effective_until.isoformat(),
        "paginas": _pages(item),
        "seccion": item.section,
        "texto": item.text,
    }


class _Abstention(Exception):
    """Stop the chain with a controlled abstention reason (internal signal)."""

    def __init__(self, reason: AbstentionReason, usage: TokenUsage | None):
        """Store the reason and the provider usage already incurred."""
        super().__init__(reason)
        self.reason, self.usage = reason, usage


class _Unavailable(Exception):
    """Stop the chain as unavailable after generation was attempted."""

    def __init__(self, phase: str):
        """Store the failing phase."""
        super().__init__(phase)
        self.phase = phase


@dataclass
class _Trace:
    """Per-request phase timestamps and usage for the metrics log (no content)."""

    started: float
    retrieved: float | None = None
    chain_started: float | None = None
    generated: float | None = None
    usage: TokenUsage | None = None
    sources: int = 0
    followup_used: str = "none"
    followup_fallback: bool = False
    rewrite_started: float | None = None
    rewrite_ended: float | None = None
    rewrite_usage: TokenUsage | None = None
    threshold: str = "off"
    dropped_citations: int = 0


def _ms(start: float | None, end: float | None) -> str:
    """Format a duration in milliseconds, or ``-`` when the phase did not run."""
    if start is None or end is None:
        return "-"
    return f"{(end - start) * 1000:.1f}"


def _count(value: int | None) -> str:
    """Format an optional token count."""
    return "-" if value is None else str(value)


def _last_user_question(history: Sequence[ConversationTurn]) -> str | None:
    """Return the most recent user turn of the history, if any."""
    for turn in reversed(history):
        if turn.role == "user":
            return turn.content
    return None


def concat_question(query: AnswerQuery) -> str | None:
    """Apply the ``concat`` rule; return None when it does not apply.

    Rule: if the history has a user turn and the current question has at most
    ``CONCAT_MAX_WORDS`` whitespace-separated words, the retrieval question is
    ``"<last user question> <current question>"`` (whitespace collapsed). The
    previous question is cut from its end so the result stays within
    ``MAX_RETRIEVAL_CHARS``; the current question is never cut.
    """
    previous = _last_user_question(query.history)
    if previous is None or len(query.question.split()) > CONCAT_MAX_WORDS:
        return None
    budget = MAX_RETRIEVAL_CHARS - len(query.question) - 1
    prefix = normalize_whitespace(previous)[: max(budget, 0)].strip()
    if not prefix:
        return None
    return f"{prefix} {query.question}"


class _InvalidRewrite(ValueError):
    """The rewrite model returned something that is not a usable question."""


class AnswerService:
    """Retrieve, generate once, validate citations and re-read current publication.

    Generation and validation run as one async LCEL chain::

        prompt (ChatPromptTemplate) -> chat model (BaseChatModel)
            -> strict parser -> citation check -> catalog revalidation

    The chat model is any LangChain ``BaseChatModel``; a plain
    ``TextGenerator`` (``GatewayClient``) is wrapped in ``GeneratorChatModel``.
    Only ``ainvoke`` is used (no threads in Workers). Literal quotes and the
    fixed prompt do not guarantee semantic grounding or prompt-injection
    resistance. No retries occur. Logs never include questions, history,
    evidence text or provider payloads; ``answer_metrics`` (INFO) records only
    status, reason, phase latencies, source count, token counts, the follow-up
    strategy/fallback, the relevance-gate outcome and the number of dropped
    (invalid or duplicate) citations.

    Retrieval question (``followup_strategy``): ``none`` uses the current
    question; ``concat`` applies ``concat_question``; ``rewrite`` asks
    ``rewriter`` (a chat model with the ``StandaloneQuestion`` schema) for a
    standalone question via its own LCEL chain and falls back to ``concat`` on
    any failure, timeout or invalid output. Generation always receives the
    original question plus history. ``min_relevance`` (None = off) abstains
    with ``no_evidence`` when the best semantic score of the evidence is
    below it; unscored evidence (no semantic channel) is not gated.
    """

    def __init__(
        self,
        retrieval: EvidenceRetrieval | None,
        generator: BaseChatModel | TextGenerator | None,
        catalog: AsyncEvidenceCatalog | None,
        *,
        max_input_chars: int,
        evidence_limit: int = 5,
        system_prompt: str = SYSTEM_PROMPT,
        referral: str = DEFAULT_REFERRAL,
        clock: Callable[[], date] = date.today,
        timer: Callable[[], float] = time.perf_counter,
        followup_strategy: FollowupStrategy = "none",
        rewriter: BaseChatModel | TextGenerator | None = None,
        rewrite_timeout: float = 8.0,
        rewrite_system_prompt: str = REWRITE_SYSTEM_PROMPT,
        min_relevance: float | None = None,
    ) -> None:
        """Receive explicit ports and limits; absent ports never produce answers."""
        if (
            followup_strategy not in get_args(FollowupStrategy)
            or type(rewrite_timeout) not in (int, float)
            or not isfinite(rewrite_timeout)
            or not 0 < rewrite_timeout <= 60
            or not rewrite_system_prompt.strip()
            or (
                min_relevance is not None
                and (
                    type(min_relevance) not in (int, float)
                    or not isfinite(min_relevance)
                    or not 0 < min_relevance <= 1
                )
            )
            or (
                followup_strategy == "rewrite"
                and generator is not None
                and rewriter is None
            )
        ):
            raise ValueError("invalid answer service configuration")
        if (
            type(max_input_chars) is not int
            or max_input_chars < 1
            or type(evidence_limit) is not int
            or not 1 <= evidence_limit <= 20
            or not system_prompt.strip()
            or not referral.strip()
        ):
            raise ValueError("invalid answer service configuration")
        if generator is not None and not isinstance(generator, BaseChatModel):
            generator = GeneratorChatModel(generator=generator)
        self._retrieval, self._model, self._catalog = retrieval, generator, catalog
        self._max_input_chars, self._evidence_limit = max_input_chars, evidence_limit
        self._system_prompt, self._referral = system_prompt, referral
        self._clock, self._timer = clock, timer
        self._strategy, self._min_relevance = followup_strategy, min_relevance
        self._rewrite_timeout = rewrite_timeout
        self._rewrite_chain: Runnable[dict[str, Any], str] | None = None
        if rewriter is not None and followup_strategy == "rewrite":
            if not isinstance(rewriter, BaseChatModel):
                rewriter = GeneratorChatModel(generator=rewriter)
            self._rewrite_chain = RunnablePassthrough.assign(
                message=build_rewrite_prompt(rewrite_system_prompt) | rewriter
            ) | RunnableLambda(self._aparse_rewrite, name="parse_rewrite")
        self._prompt = build_answer_prompt(system_prompt)
        self._chain: Runnable[dict[str, Any], AnswerResult] | None = None
        if generator is not None:
            self._chain = (
                RunnablePassthrough.assign(message=self._prompt | generator)
                | RunnableLambda(self._aparse, name="parse_candidate")
                | RunnableLambda(self._averify, name="verify_citations")
                | RunnableLambda(self._arevalidate, name="revalidate_catalog")
            )

    @property
    def chain(self) -> Runnable[dict[str, Any], AnswerResult] | None:
        """Return the generation/validation chain (None when unconfigured)."""
        return self._chain

    @property
    def rewrite_chain(self) -> Runnable[dict[str, Any], str] | None:
        """Return the standalone-question chain (None unless ``rewrite``)."""
        return self._rewrite_chain

    def _unavailable(self, phase: str, attempted: bool = False) -> AnswerResult:
        """Build a referral-bearing unavailable result and log its phase."""
        logger.warning("answer_unavailable phase=%s", phase)
        return AnswerResult(
            "unavailable", referral=self._referral, generation_attempted=attempted
        )

    def _abstain(
        self, reason: AbstentionReason, usage: TokenUsage | None = None
    ) -> AnswerResult:
        """Build a referral-bearing abstention with a controlled reason."""
        logger.info("answer_abstained reason=%s", reason)
        return AnswerResult(
            "abstained",
            reason=reason,
            referral=self._referral,
            generation_attempted=reason != "no_evidence",
            usage=usage,
        )

    async def _retrieval_question(self, query: AnswerQuery, trace: _Trace) -> str:
        """Return the text used for retrieval according to ``followup_strategy``.

        Without history every strategy uses the current question unchanged.
        ``trace.followup_used`` records which rule produced the text.
        """
        if self._strategy == "none" or not query.history:
            return query.question
        if self._strategy == "rewrite" and self._rewrite_chain is not None:
            trace.rewrite_started = self._timer()
            try:
                question = await self._rewrite(query, trace)
            except Exception as error:  # Any rewrite failure falls back to concat.
                if isinstance(error, GenerationError):
                    code = error.code
                elif isinstance(error, TimeoutError):
                    code = "timeout"
                elif isinstance(error, ValueError | TypeError | RecursionError):
                    code = "invalid_output"
                else:
                    code = type(error).__name__
                logger.warning("answer_rewrite_fallback error=%s", code)
                trace.followup_fallback = True
            else:
                trace.followup_used = "rewrite"
                return question
            finally:
                trace.rewrite_ended = self._timer()
        combined = concat_question(query)
        if combined is None:
            return query.question
        trace.followup_used = "concat"
        return combined

    async def _rewrite(self, query: AnswerQuery, trace: _Trace) -> str:
        """Run the rewrite chain within ``rewrite_timeout`` seconds."""
        content = json.dumps(
            {
                "historial": [
                    {"role": turn.role, "content": turn.content}
                    for turn in query.history
                ],
                "pregunta": query.question,
            },
            ensure_ascii=False,
        )
        async with asyncio.timeout(self._rewrite_timeout):
            return await self._rewrite_chain.ainvoke(  # type: ignore[union-attr]
                {"content": content, "trace": trace}
            )

    async def _aparse_rewrite(self, state: dict[str, Any]) -> str:
        """Strictly parse ``{"question": str}``; reject blank or overlong text."""
        message = state["message"]
        if not isinstance(message, BaseMessage) or not isinstance(message.content, str):
            raise _InvalidRewrite("message")
        state["trace"].rewrite_usage = message_usage(message)
        parsed = StandaloneQuestion.model_validate(
            json.loads(message.content, object_pairs_hook=_unique_object)
        )
        question = normalize_whitespace(parsed.question)
        if not question or len(question) > MAX_RETRIEVAL_CHARS:
            raise _InvalidRewrite("question")
        return question

    def _content(
        self,
        query: AnswerQuery,
        history: list[ConversationTurn],
        sources: list[Evidence],
    ) -> str:
        """Serialize question, bounded history and sources as prompt data."""
        return json.dumps(
            {
                "pregunta": query.question,
                "historial": [
                    {"role": turn.role, "content": turn.content} for turn in history
                ],
                "fecha_consulta": self._clock().isoformat(),
                "fuentes": [_source(item) for item in sources],
            },
            ensure_ascii=False,
        )

    def _fit(
        self, query: AnswerQuery, evidence: tuple[Evidence, ...]
    ) -> tuple[str, tuple[Evidence, ...]] | None:
        """Drop oldest history, then lowest-ranked sources, until within budget."""
        history, sources = list(query.history), list(evidence)
        budget = self._max_input_chars - len(self._system_prompt)
        while True:
            content = self._content(query, history, sources)
            if len(content) <= budget:
                return content, tuple(sources)
            if history:
                history.pop(0)
            elif len(sources) > 1:
                sources.pop()
            else:
                return None

    async def _aparse(self, state: dict[str, Any]) -> dict[str, Any]:
        """Strictly parse the model message into an ``AnswerCandidate``."""
        trace: _Trace = state["trace"]
        trace.generated = self._timer()
        message = state["message"]
        if not isinstance(message, BaseMessage) or not isinstance(message.content, str):
            raise _Unavailable("generation")
        usage = trace.usage = message_usage(message)
        try:
            candidate = AnswerCandidate.model_validate(
                json.loads(message.content, object_pairs_hook=_unique_object)
            )
        except ValueError, TypeError, RecursionError:
            raise _Abstention("invalid_output", usage) from None
        if candidate.status == "abstained":
            if candidate.answer is not None or candidate.citations:
                raise _Abstention("invalid_output", usage)
            raise _Abstention("model_abstained", usage)
        if (
            candidate.answer is None
            or not candidate.answer.strip()
            or not candidate.citations
        ):
            raise _Abstention("invalid_output", usage)
        return {**state, "candidate": candidate, "usage": usage}

    async def _averify(self, state: dict[str, Any]) -> dict[str, Any]:
        """Keep citations with a known, unique chunk id and a literal quote.

        Invalid citations (unknown chunk id, repeated chunk, or a quote that
        ``match_quote`` rejects) are dropped and counted; the answer survives
        backed by the remaining ones. Abstain with ``invalid_citation`` only
        when no valid citation remains. Each kept quote is the matching span
        of the chunk text, never the model's spelling.
        """
        by_id = {item.chunk_id: item for item in state["sources"]}
        cited: list[tuple[Evidence, str]] = []
        dropped = 0
        for reference in state["candidate"].citations:
            chunk = by_id.get(reference.chunk_id)
            quote = None
            if chunk is not None and not any(item is chunk for item, _ in cited):
                quote = match_quote(reference.quote, chunk.text)
            if quote is None:
                dropped += 1
                continue
            cited.append((chunk, quote))  # type: ignore[arg-type]
        trace = state.get("trace")
        if isinstance(trace, _Trace):
            trace.dropped_citations = dropped
        if not cited:
            raise _Abstention("invalid_citation", state["usage"])
        return {**state, "cited": tuple(cited)}

    async def _arevalidate(self, state: dict[str, Any]) -> AnswerResult:
        """Re-read each cited chunk; build citations only from catalog metadata."""
        usage = state["usage"]
        citations = []
        stale = False
        try:
            for chunk, quote in state["cited"]:
                entry = await self._catalog.get_current(chunk.chunk_id)  # type: ignore[union-attr]
                if not is_admissible(chunk, entry):
                    stale = True
                    break
                citations.append(
                    AnswerCitation(
                        chunk_id=chunk.chunk_id,
                        document_id=chunk.document_id,
                        document_title=entry.document_title,  # type: ignore[union-attr]
                        version_id=chunk.version_id,
                        version_label=entry.version_label,  # type: ignore[union-attr]
                        section=chunk.section,
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                        quote=quote,
                    )
                )
        except Exception as error:  # Catalog adapter failure after generation.
            logger.warning("answer_catalog_error error=%s", type(error).__name__)
            raise _Unavailable("catalog") from None
        if stale:
            raise _Abstention("stale_source", usage)
        return AnswerResult(
            "answered",
            state["candidate"].answer,
            citations=tuple(citations),
            generation_attempted=True,
            usage=usage,
        )

    async def query(self, query: AnswerQuery) -> AnswerResult:
        """Answer with verified citations, abstain with a reason, or be unavailable."""
        if not isinstance(query, AnswerQuery):
            raise TypeError("query must be AnswerQuery")
        if self._retrieval is None or self._chain is None or self._catalog is None:
            return self._unavailable("unconfigured")
        trace = _Trace(self._timer())
        result = await self._answer(query, trace)
        self._log_metrics(result, trace)
        return result

    async def _answer(self, query: AnswerQuery, trace: _Trace) -> AnswerResult:
        """Run retrieval, then the generation/validation chain."""
        request = RetrievalQuery(
            question=await self._retrieval_question(query, trace),
            limit=self._evidence_limit,
        )
        try:
            retrieval = await self._retrieval.query(request)  # type: ignore[union-attr]
        except Exception as error:  # Port implementations may raise anything.
            logger.warning("answer_retrieval_error error=%s", type(error).__name__)
            return self._unavailable("retrieval")
        finally:
            trace.retrieved = self._timer()
        if retrieval.status == "unavailable":
            return self._unavailable("retrieval")
        if retrieval.status != "evidence_found" or not retrieval.evidence:
            return self._abstain("no_evidence")
        if self._min_relevance is not None:
            if retrieval.relevance is None:
                trace.threshold = "unscored"
            elif retrieval.relevance < self._min_relevance:
                trace.threshold = "below"
                return self._abstain("no_evidence")
            else:
                trace.threshold = "pass"
        fitted = self._fit(query, retrieval.evidence)
        if fitted is None:
            return self._unavailable("input_budget")
        content, sources = fitted
        trace.sources = len(sources)
        trace.chain_started = self._timer()
        try:
            return await self._chain.ainvoke(  # type: ignore[union-attr]
                {"content": content, "sources": sources, "trace": trace}
            )
        except _Abstention as signal:
            return self._abstain(signal.reason, signal.usage)
        except _Unavailable as signal:
            return self._unavailable(signal.phase, True)
        except GenerationError as error:
            logger.warning("answer_generation_error code=%s", error.code)
            return self._unavailable("generation", error.request_attempted)
        except Exception as error:  # Unknown chat model adapters; fail closed.
            logger.warning("answer_generation_error error=%s", type(error).__name__)
            return self._unavailable("generation", True)

    def _log_metrics(self, result: AnswerResult, trace: _Trace) -> None:
        """Log outcome, phase latencies and token counts; never request content."""
        end = self._timer()
        usage = trace.usage or TokenUsage()
        logger.info(
            "answer_metrics status=%s reason=%s sources=%d retrieval_ms=%s "
            "generation_ms=%s validation_ms=%s prompt_tokens=%s "
            "completion_tokens=%s total_tokens=%s followup_strategy=%s "
            "followup_used=%s followup_fallback=%s rewrite_ms=%s rewrite_tokens=%s "
            "threshold=%s below_threshold=%s dropped_citations=%d",
            result.status,
            result.reason or "-",
            trace.sources,
            _ms(
                trace.started if trace.rewrite_ended is None else trace.rewrite_ended,
                trace.retrieved,
            ),
            _ms(trace.chain_started, trace.generated or end)
            if trace.chain_started is not None
            else "-",
            _ms(trace.generated, end),
            _count(usage.prompt_tokens),
            _count(usage.completion_tokens),
            _count(usage.total_tokens),
            self._strategy,
            trace.followup_used,
            str(trace.followup_fallback).lower(),
            _ms(trace.rewrite_started, trace.rewrite_ended),
            _count(trace.rewrite_usage.total_tokens if trace.rewrite_usage else None),
            trace.threshold,
            str(trace.threshold == "below").lower(),
            trace.dropped_citations,
        )

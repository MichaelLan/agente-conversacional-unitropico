import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

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
    SYSTEM_PROMPT,
    build_answer_prompt,
)
from unitropico_rag.services.retrieval import AsyncEvidenceCatalog

# A quote must carry at least this many non-whitespace characters, so that
# trivially short strings ("de", "2026") cannot pass as literal evidence.
MIN_QUOTE_CHARS = 12


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


def quote_matches(quote: str, text: str) -> bool:
    """Accept a quote that is long enough and literal modulo whitespace runs."""
    normalized = normalize_whitespace(quote)
    visible = len(normalized.replace(" ", ""))
    return visible >= MIN_QUOTE_CHARS and normalized in normalize_whitespace(text)


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


def _ms(start: float | None, end: float | None) -> str:
    """Format a duration in milliseconds, or ``-`` when the phase did not run."""
    if start is None or end is None:
        return "-"
    return f"{(end - start) * 1000:.1f}"


def _count(value: int | None) -> str:
    """Format an optional token count."""
    return "-" if value is None else str(value)


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
    status, reason, phase latencies, source count and token counts.
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
    ) -> None:
        """Receive explicit ports and limits; absent ports never produce answers."""
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

    def _retrieval_question(self, query: AnswerQuery) -> str:
        """Return the text used for retrieval.

        Extension point: a standalone reformulation from history may replace this
        once evaluated; today retrieval uses only the current question.
        """
        return query.question

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
        """Require known, unique chunk ids and literal quotes from the sources."""
        by_id = {item.chunk_id: item for item in state["sources"]}
        cited: list[tuple[Evidence, str]] = []
        for reference in state["candidate"].citations:
            chunk = by_id.get(reference.chunk_id)
            if (
                chunk is None
                or any(item is chunk for item, _ in cited)
                or not quote_matches(reference.quote, chunk.text)
            ):
                raise _Abstention("invalid_citation", state["usage"])
            cited.append((chunk, normalize_whitespace(reference.quote)))
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
            question=self._retrieval_question(query), limit=self._evidence_limit
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
            "completion_tokens=%s total_tokens=%s",
            result.status,
            result.reason or "-",
            trace.sources,
            _ms(trace.started, trace.retrieved),
            _ms(trace.chain_started, trace.generated or end)
            if trace.chain_started is not None
            else "-",
            _ms(trace.generated, end),
            _count(usage.prompt_tokens),
            _count(usage.completion_tokens),
            _count(usage.total_tokens),
        )

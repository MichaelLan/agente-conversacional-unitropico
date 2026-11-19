import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import langsmith
from fastapi import FastAPI
from langchain_core.retrievers import BaseRetriever

from unitropico_rag.api.answer import AnswerPort, create_answer_router
from unitropico_rag.api.retrieval import create_retrieval_router
from unitropico_rag.api.routes import create_health_router
from unitropico_rag.core.logger import configure_logger, logger
from unitropico_rag.core.settings import ENV_NAMES, Settings
from unitropico_rag.domain.answer import ANSWER_CANDIDATE_SCHEMA
from unitropico_rag.domain.generation import StructuredOutput
from unitropico_rag.infra.d1 import D1Database, D1EvidenceCatalog, D1LexicalRetriever
from unitropico_rag.infra.embeddings import GatewayEmbedder, GatewayEmbeddings
from unitropico_rag.infra.gateway import Fetch, GatewayClient
from unitropico_rag.infra.vectorize import VectorizeIndex, VectorRetriever
from unitropico_rag.services.answer import AnswerService, EvidenceRetrieval
from unitropico_rag.services.chat_model import GeneratorChatModel
from unitropico_rag.services.fusion import HybridRetriever
from unitropico_rag.services.prompt import DEFAULT_REFERRAL, SYSTEM_PROMPT
from unitropico_rag.services.retrieval import (
    AsyncEvidenceCatalog,
    AsyncRetriever,
    RetrievalService,
)
from unitropico_rag.services.retriever import ChunkRetriever

_TRACING_SWITCHES = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING")


def disable_remote_tracing() -> None:
    """Force LangChain/LangSmith tracing off for this process.

    Sets the environment switches and LangSmith's global override (checked
    before the cached environment lookup), so no tracer is attached to any
    LangChain run: in Workers LangSmith could only post synchronously per event
    (its batching needs threads) and would export request content.
    """
    for name in _TRACING_SWITCHES:
        os.environ[name] = "false"
    langsmith.configure(enabled=False)


def env_mapping(env: object) -> dict[str, object]:
    """Copy known settings names from a Workers ``env`` object into a plain dict."""
    if isinstance(env, Mapping):
        return {name: env.get(name) for name in ENV_NAMES.values()}
    return {name: getattr(env, name, None) for name in ENV_NAMES.values()}


def create_app(
    *, retrieval_service: EvidenceRetrieval, answer_service: AnswerPort
) -> FastAPI:
    """Bind HTTP routes to already composed services."""
    app = FastAPI(title="Unitrópico RAG")
    app.include_router(create_health_router())
    app.include_router(create_retrieval_router(retrieval_service))
    app.include_router(create_answer_router(answer_service))
    return app


@dataclass(frozen=True, eq=False)
class Bindings:
    """Runtime bindings resolved from ``env``; stable for the isolate's lifetime."""

    db: object | None = None
    vectorize: object | None = None

    @classmethod
    def from_env(cls, env: object) -> Bindings:
        """Read ``DB`` and ``VECTORIZE`` from a Workers env (or a mapping)."""
        if isinstance(env, Mapping):
            return cls(env.get("DB"), env.get("VECTORIZE"))
        return cls(getattr(env, "DB", None), getattr(env, "VECTORIZE", None))

    def presence(self) -> tuple[bool, bool]:
        """Return which bindings exist; the app cache keys on this, not identity."""
        return (self.db is not None, self.vectorize is not None)


def compose_retrieval(
    settings: Settings, bindings: Bindings, fetch: Fetch | None
) -> tuple[ChunkRetriever | None, AsyncEvidenceCatalog | None]:
    """Build the LangChain retriever and catalog for ``RETRIEVAL_MODE``.

    The retriever is ``ChunkRetriever`` (``BaseRetriever``) over
    ``HybridRetriever`` (one or two channels, RRF with per-channel trace).

    Without ``DB`` nothing is built (unavailable). ``hybrid`` without
    ``VECTORIZE``, fetch or API key degrades to lexical with a warning;
    ``semantic`` without them stays unavailable so evaluations never silently
    measure another mode.
    """
    if bindings.db is None:
        logger.warning("retrieval_unconfigured missing=DB")
        return None, None
    db: D1Database = bindings.db  # type: ignore[assignment]
    catalog = D1EvidenceCatalog(db)
    mode = settings.retrieval_mode
    semantic = None
    if mode != "lexical":
        missing = [
            name
            for name, value in (
                ("VECTORIZE", bindings.vectorize),
                ("fetch", fetch),
                ("AI_GATEWAY_API_KEY", settings.ai_gateway_api_key),
            )
            if value is None
        ]
        if not missing:
            embedder = GatewayEmbedder(
                model=settings.embedding_model,
                api_key=settings.ai_gateway_api_key,  # type: ignore[arg-type]
                fetch=fetch,  # type: ignore[arg-type]
                url=settings.embedding_url,
                timeout=settings.embedding_timeout_seconds,
                dimensions=settings.embedding_dimensions,
            )
            semantic = VectorRetriever(
                GatewayEmbeddings(embedder), VectorizeIndex(bindings.vectorize)
            )
        elif mode == "semantic":
            logger.warning(
                "retrieval_unconfigured mode=semantic missing=%s", ",".join(missing)
            )
            return None, catalog
        else:
            logger.warning(
                "retrieval_degraded mode=hybrid effective=lexical missing=%s",
                ",".join(missing),
            )
    lexical = None if mode == "semantic" else D1LexicalRetriever(db)
    hybrid = HybridRetriever(
        catalog, lexical=lexical, semantic=semantic, rrf_k=settings.rrf_k
    )
    return ChunkRetriever(base=hybrid), catalog


def build_app(
    settings: Settings,
    *,
    fetch: Fetch | None = None,
    bindings: Bindings | None = None,
    retriever: BaseRetriever | AsyncRetriever | None = None,
    catalog: AsyncEvidenceCatalog | None = None,
) -> FastAPI:
    """Compose logging, adapters and services from settings and bindings.

    Explicit ``retriever``/``catalog`` (tests) take precedence over adapters
    built from ``bindings``. Missing fetch, API key or D1 leave the
    corresponding service unavailable (503) instead of fabricating answers.
    """
    configure_logger(settings.log_level)
    disable_remote_tracing()
    if retriever is None and catalog is None and bindings is not None:
        retriever, catalog = compose_retrieval(settings, bindings, fetch)
    retrieval = RetrievalService(retriever, catalog)
    model = None
    if fetch is not None and settings.ai_gateway_api_key is not None:
        client = GatewayClient(
            model=settings.generation_model,
            api_key=settings.ai_gateway_api_key,
            fetch=fetch,
            url=settings.gateway_url,
            timeout=settings.gateway_timeout_seconds,
            max_tokens=settings.generation_max_tokens,
            max_input_chars=settings.max_input_chars,
            reasoning_effort=settings.reasoning_effort,
            structured_output=StructuredOutput(
                "answer_candidate", ANSWER_CANDIDATE_SCHEMA
            ),
        )
        model = GeneratorChatModel(
            generator=client, model_name=settings.generation_model
        )
    answer = AnswerService(
        retrieval,
        model,
        catalog,
        max_input_chars=settings.max_input_chars,
        evidence_limit=settings.evidence_limit,
        system_prompt=SYSTEM_PROMPT,
        referral=DEFAULT_REFERRAL,
    )
    return create_app(retrieval_service=retrieval, answer_service=answer)


class AppCache:
    """Reuse one composed app per isolate while settings and bindings stay equal.

    Bindings are compared by presence only: within an isolate they do not change,
    so the binding objects captured on first build are reused.
    """

    def __init__(self, factory: Callable[[Settings, Bindings], FastAPI]):
        """Receive the composition function used on first use or config change."""
        self._factory = factory
        self._key: tuple[Settings, tuple[bool, bool]] | None = None
        self._app: FastAPI | None = None

    def get(self, settings: Settings, bindings: Bindings | None = None) -> FastAPI:
        """Return the cached app, rebuilding only when settings or presence differ."""
        bindings = bindings or Bindings()
        key = (settings, bindings.presence())
        if self._app is None or key != self._key:
            self._app = self._factory(settings, bindings)
            self._key = key
        return self._app

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from unitropico_rag.domain.answer import FollowupStrategy
from unitropico_rag.domain.generation import ReasoningEffort

# Settings field -> Workers binding/var/secret name.
ENV_NAMES: dict[str, str] = {
    "log_level": "RAG_LOG_LEVEL",
    "ai_gateway_api_key": "AI_GATEWAY_API_KEY",
    "generation_model": "GENERATION_MODEL",
    "gateway_url": "AI_GATEWAY_URL",
    "gateway_timeout_seconds": "AI_GATEWAY_TIMEOUT_SECONDS",
    "generation_max_tokens": "GENERATION_MAX_TOKENS",
    "reasoning_effort": "GENERATION_REASONING_EFFORT",
    "max_input_chars": "GENERATION_MAX_INPUT_CHARS",
    "evidence_limit": "ANSWER_EVIDENCE_LIMIT",
    "retrieval_mode": "RETRIEVAL_MODE",
    "rrf_k": "RETRIEVAL_RRF_K",
    "embedding_model": "EMBEDDING_MODEL",
    "embedding_dimensions": "EMBEDDING_DIMENSIONS",
    "embedding_url": "EMBEDDING_URL",
    "embedding_timeout_seconds": "EMBEDDING_TIMEOUT_SECONDS",
    "followup_strategy": "FOLLOWUP_STRATEGY",
    "rewrite_max_tokens": "FOLLOWUP_REWRITE_MAX_TOKENS",
    "rewrite_timeout_seconds": "FOLLOWUP_REWRITE_TIMEOUT_SECONDS",
    "min_relevance": "MIN_RELEVANCE",
}

RetrievalMode = Literal["hybrid", "lexical", "semantic"]


class SettingsError(ValueError):
    """Name invalid settings without echoing their values (they may be secrets)."""


class Settings(BaseModel):
    """Validate component configuration; built only at the composition boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    log_level: str = Field(default="WARNING", pattern="^(DEBUG|INFO|WARNING|ERROR)$")
    ai_gateway_api_key: SecretStr | None = None
    generation_model: str = Field(default="openai/gpt-6-luna", min_length=1)
    gateway_url: str = Field(
        default="https://ai-gateway.vercel.sh/v1/chat/completions",
        pattern="^https://",
    )
    gateway_timeout_seconds: float = Field(default=20, gt=0, le=60)
    generation_max_tokens: int = Field(default=2048, ge=1, le=32768)
    reasoning_effort: ReasoningEffort = "low"
    # Sized for 8 sources: system prompt (~2.6k) + 8 x ~2.2k (1800-char chunks
    # as JSON with metadata) + room for history (oldest turns drop first).
    max_input_chars: int = Field(default=32000, ge=1000, le=200000)
    evidence_limit: int = Field(default=8, ge=1, le=20)
    retrieval_mode: RetrievalMode = "hybrid"
    rrf_k: int = Field(default=60, ge=0, le=1000)
    embedding_model: str = Field(default="openai/text-embedding-3-small", min_length=1)
    embedding_dimensions: int = Field(default=1536, ge=1, le=1536)
    embedding_url: str = Field(
        default="https://ai-gateway.vercel.sh/v1/embeddings", pattern="^https://"
    )
    embedding_timeout_seconds: float = Field(default=10, gt=0, le=60)
    followup_strategy: FollowupStrategy = "concat"
    rewrite_max_tokens: int = Field(default=256, ge=16, le=2048)
    rewrite_timeout_seconds: float = Field(default=8, gt=0, le=60)
    # Minimum cosine similarity (semantic channel); None disables the gate.
    min_relevance: float | None = Field(default=None, gt=0, le=1)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> Settings:
        """Build settings from Workers env values; blank or absent use defaults."""
        values = {}
        for field_name, env_name in ENV_NAMES.items():
            value = mapping.get(env_name)
            if type(value) in (int, float):  # JSON-typed wrangler vars
                value = str(value)
            if isinstance(value, str) and value.strip():
                values[field_name] = value.strip()
        try:
            return cls(**values)
        except ValidationError as error:
            fields = sorted({str(item["loc"][0]) for item in error.errors()})
        # Raised outside the handler so the value-bearing error is not chained.
        raise SettingsError(f"invalid settings: {', '.join(fields)}")

from collections.abc import Mapping
from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Settings(BaseModel):
    """Validated configuration built from an explicit environment mapping."""

    model_config = ConfigDict(frozen=True)
    ENV_NAMES: ClassVar[dict[str, str]] = {
        "LOG_LEVEL": "log_level",
        "EXPOSE_DOCS": "expose_docs",
        "RAG_BASE_URL": "rag_base_url",
        "RAG_RETRIEVAL_TIMEOUT_SECONDS": "rag_retrieval_timeout_seconds",
        "RAG_ANSWER_TIMEOUT_SECONDS": "rag_answer_timeout_seconds",
        "ADMIN_TOKEN": "admin_token",
        "ADMIN_MAX_UPLOAD_BYTES": "admin_max_upload_bytes",
    }

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    expose_docs: bool = False
    rag_base_url: str = Field(default="https://rag.internal", min_length=1)
    rag_retrieval_timeout_seconds: float = Field(default=10, gt=0, le=120)
    rag_answer_timeout_seconds: float = Field(default=45, gt=0, le=120)
    # Wrangler secret; absent or empty keeps every /admin route closed (503).
    admin_token: SecretStr | None = None
    # Ingestion rejects PDFs above 50 MB, so a larger upload could never be processed.
    admin_max_upload_bytes: int = Field(default=25 * 1024 * 1024, gt=0, le=50_000_000)

    @classmethod
    def from_mapping(cls, env: Mapping[str, object]) -> Self:
        """Read only the known variable names; absent ones keep their defaults."""
        values = {
            field: env[name] for name, field in cls.ENV_NAMES.items() if name in env
        }
        return cls.model_validate(values)

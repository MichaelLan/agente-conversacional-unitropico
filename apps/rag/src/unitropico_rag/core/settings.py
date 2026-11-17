import os

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """Validate the single optional local setting; never enable remote tracing."""

    log_level: str = Field(default="WARNING", pattern="^(DEBUG|INFO|WARNING|ERROR)$")

    @classmethod
    def from_environment(cls) -> Settings:
        """Read configuration only at the application composition boundary."""
        return cls(log_level=os.environ.get("RAG_LOG_LEVEL", "WARNING"))

    def disable_remote_tracing(self) -> None:
        """Override legacy and current tracing switches for this local-only PoC."""
        for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
            os.environ[name] = "false"

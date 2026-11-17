import os
from typing import Literal

from pydantic import BaseModel, Field


def _read_log_level() -> str:
    """Lee únicamente LOG_LEVEL al solicitar una nueva configuración."""
    return os.environ.get("LOG_LEVEL", "INFO")


class Settings(BaseModel):
    """Valida la configuración local sin conservar estado global."""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default_factory=_read_log_level
    )

    model_config = {"validate_default": True}

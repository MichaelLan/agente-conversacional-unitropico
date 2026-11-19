import re
from collections.abc import Mapping
from dataclasses import dataclass, field

# Settings field -> Workers var/secret name.
ENV_NAMES: dict[str, str] = {
    "firecrawl_api_key": "FIRECRAWL_API_KEY",
    "ai_gateway_api_key": "AI_GATEWAY_API_KEY",
    "embedding_model": "EMBEDDING_MODEL",
    "embedding_dimensions": "EMBEDDING_DIMENSIONS",
    "ocr_version": "OCR_VERSION",
    "ocr_max_pages": "OCR_MAX_PAGES",
    "firecrawl_url": "FIRECRAWL_URL",
    "embedding_url": "EMBEDDING_URL",
    "log_level": "INGESTION_LOG_LEVEL",
}

_KEY_SEGMENT = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_MODEL = re.compile(r"^[A-Za-z0-9._-]{1,64}(/[A-Za-z0-9._-]{1,64})?$")
_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class SettingsError(ValueError):
    """Name invalid settings without echoing their values (they may be secrets)."""


@dataclass(frozen=True)
class Secret:
    """A secret value that never appears in repr or str."""

    value: str = field(repr=False)

    def __repr__(self) -> str:
        """Hide the value."""
        return "Secret('**********')"

    __str__ = __repr__


@dataclass(frozen=True)
class Settings:
    """Validated configuration; built only in ``worker.py`` from the env."""

    firecrawl_api_key: Secret | None = None
    ai_gateway_api_key: Secret | None = None
    embedding_model: str = "openai/text-embedding-3-small"
    embedding_dimensions: int = 1536
    ocr_version: str = "firecrawl-parse-v2-ocr-v1"
    ocr_max_pages: int = 1000
    firecrawl_url: str = "https://api.firecrawl.dev/v2/parse"
    embedding_url: str = "https://ai-gateway.vercel.sh/v1/embeddings"
    log_level: str = "INFO"

    def secrets(self) -> tuple[str, ...]:
        """Return secret values for redaction of persisted details."""
        return tuple(
            s.value for s in (self.firecrawl_api_key, self.ai_gateway_api_key) if s
        )

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> Settings:
        """Build settings from Workers env values; blank or absent use defaults."""
        raw: dict[str, str] = {}
        for name, env_name in ENV_NAMES.items():
            value = mapping.get(env_name)
            if type(value) in (int, float):  # JSON-typed wrangler vars
                value = str(value)
            if isinstance(value, str) and value.strip():
                raw[name] = value.strip()
        invalid: list[str] = []
        values: dict[str, object] = {}
        for name in ("firecrawl_api_key", "ai_gateway_api_key"):
            if name in raw:
                values[name] = Secret(raw[name])
        for name in ("embedding_dimensions", "ocr_max_pages"):
            if name in raw:
                try:
                    values[name] = int(raw[name])
                except ValueError:
                    invalid.append(name)
        for name in ("embedding_model", "ocr_version", "firecrawl_url"):
            if name in raw:
                values[name] = raw[name]
        if "embedding_url" in raw:
            values["embedding_url"] = raw["embedding_url"]
        if "log_level" in raw:
            values["log_level"] = raw["log_level"].upper()
        settings = cls(**values)  # type: ignore[arg-type]
        invalid.extend(settings._invalid_fields())
        if invalid:
            raise SettingsError(f"invalid settings: {', '.join(sorted(set(invalid)))}")
        return settings

    def _invalid_fields(self) -> list[str]:
        checks = {
            "embedding_dimensions": type(self.embedding_dimensions) is int
            and 1 <= self.embedding_dimensions <= 1536,
            "ocr_max_pages": type(self.ocr_max_pages) is int
            and 1 <= self.ocr_max_pages <= 10000,
            "embedding_model": bool(_MODEL.match(self.embedding_model)),
            "ocr_version": bool(_KEY_SEGMENT.match(self.ocr_version)),
            "firecrawl_url": self.firecrawl_url.startswith("https://"),
            "embedding_url": self.embedding_url.startswith("https://"),
            "log_level": self.log_level in _LEVELS,
        }
        return [name for name, ok in checks.items() if not ok]

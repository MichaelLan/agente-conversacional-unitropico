import logging
from types import SimpleNamespace

import pytest

from unitropico_rag.core.logger import configure_logger, logger
from unitropico_rag.core.settings import Settings, SettingsError
from unitropico_rag.main import AppCache, Bindings, build_app, env_mapping


def test_from_mapping_defaults_and_overrides():
    """Absent or blank names use defaults; present ones are parsed."""
    defaults = Settings.from_mapping({})
    assert defaults.generation_model == "openai/gpt-6-luna"
    assert defaults.ai_gateway_api_key is None
    assert (defaults.reasoning_effort, defaults.generation_max_tokens) == ("low", 2048)
    settings = Settings.from_mapping(
        {
            "AI_GATEWAY_API_KEY": "fake-secret",
            "GENERATION_MODEL": " other/model ",
            "GENERATION_REASONING_EFFORT": "medium",
            "GENERATION_MAX_TOKENS": 4096,
            "AI_GATEWAY_TIMEOUT_SECONDS": "15",
            "RAG_LOG_LEVEL": "",
            "UNRELATED": "ignored",
        }
    )
    assert settings.ai_gateway_api_key.get_secret_value() == "fake-secret"
    assert settings.generation_model == "other/model"
    assert settings.reasoning_effort == "medium"
    assert settings.generation_max_tokens == 4096
    assert settings.gateway_timeout_seconds == 15
    assert settings.log_level == "WARNING"


@pytest.mark.parametrize(
    "mapping",
    [
        {"RAG_LOG_LEVEL": "TRACE"},
        {"GENERATION_REASONING_EFFORT": "extreme"},
        {"AI_GATEWAY_URL": "http://insecure"},
        {"GENERATION_MAX_TOKENS": "lots"},
    ],
)
def test_from_mapping_rejects_without_echoing_values(mapping):
    """Invalid values name the field but never echo the value."""
    with pytest.raises(SettingsError) as caught:
        Settings.from_mapping(mapping | {"AI_GATEWAY_API_KEY": "fake-secret"})
    assert "fake-secret" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_env_mapping_reads_attributes_and_mappings():
    """Workers env objects expose bindings as attributes; dicts also work."""
    env = SimpleNamespace(AI_GATEWAY_API_KEY="k", DB=object())
    mapped = env_mapping(env)
    assert mapped["AI_GATEWAY_API_KEY"] == "k" and mapped["GENERATION_MODEL"] is None
    assert "DB" not in mapped
    assert env_mapping({"GENERATION_MODEL": "m"})["GENERATION_MODEL"] == "m"


def test_app_cache_rebuilds_only_on_settings_or_binding_presence_change():
    """One app per isolate while settings and binding presence remain equal."""
    built = []

    def factory(settings, bindings):
        built.append((settings, bindings))
        return object()

    cache = AppCache(factory)
    first = cache.get(Settings.from_mapping({"AI_GATEWAY_API_KEY": "a"}))
    assert cache.get(Settings.from_mapping({"AI_GATEWAY_API_KEY": "a"})) is first
    second = cache.get(Settings.from_mapping({"AI_GATEWAY_API_KEY": "b"}))
    assert second is not first
    assert len(built) == 2
    db = object()
    with_db = cache.get(
        Settings.from_mapping({"AI_GATEWAY_API_KEY": "b"}), Bindings(db)
    )
    assert with_db is not second
    # A new binding proxy for the same binding (new request) reuses the app.
    same = cache.get(
        Settings.from_mapping({"AI_GATEWAY_API_KEY": "b"}), Bindings(object())
    )
    assert same is with_db
    assert len(built) == 3 and built[-1][1].db is db


def test_bindings_from_env_attributes_and_mapping():
    """Missing bindings are None; mappings work for tests."""
    db, vec = object(), object()
    from_attrs = Bindings.from_env(SimpleNamespace(DB=db))
    assert (from_attrs.db, from_attrs.vectorize) == (db, None)
    from_map = Bindings.from_env({"DB": db, "VECTORIZE": vec})
    assert from_map.presence() == (True, True)


def test_logger_has_single_stream_handler_and_level():
    """Composition attaches one StreamHandler and applies the configured level."""
    build_app(Settings(log_level="INFO"))
    build_app(Settings(log_level="ERROR"))
    streams = [h for h in logger.handlers if isinstance(h, logging.StreamHandler)]
    assert len(streams) == 1
    assert logger.level == logging.ERROR
    configure_logger("WARNING")


def test_retrieval_and_embedding_settings():
    """Retrieval mode, RRF k and embedding vars parse with documented defaults."""
    defaults = Settings.from_mapping({})
    assert (defaults.retrieval_mode, defaults.rrf_k) == ("hybrid", 60)
    assert defaults.embedding_model == "openai/text-embedding-3-small"
    assert defaults.embedding_dimensions == 1536
    assert defaults.embedding_url == "https://ai-gateway.vercel.sh/v1/embeddings"
    settings = Settings.from_mapping(
        {"RETRIEVAL_MODE": "lexical", "RETRIEVAL_RRF_K": 10, "EMBEDDING_DIMENSIONS": 8}
    )
    assert (settings.retrieval_mode, settings.rrf_k) == ("lexical", 10)
    for invalid in (
        {"RETRIEVAL_MODE": "dense"},
        {"EMBEDDING_DIMENSIONS": 1537},
        {"EMBEDDING_URL": "http://x"},
    ):
        with pytest.raises(SettingsError):
            Settings.from_mapping(invalid)

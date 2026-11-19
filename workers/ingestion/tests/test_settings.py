import pytest

from unitropico_ingestion.core.settings import Settings, SettingsError


def test_defaults_and_secrets():
    """Defaults and secrets."""
    settings = Settings.from_mapping(
        {
            "FIRECRAWL_API_KEY": " fc-key ",
            "AI_GATEWAY_API_KEY": "gw",
            "EMBEDDING_DIMENSIONS": 1536,
        }
    )
    assert settings.embedding_model == "openai/text-embedding-3-small"
    assert settings.embedding_dimensions == 1536
    assert settings.ocr_version == "firecrawl-parse-v2-ocr-v1"
    assert settings.secrets() == ("fc-key", "gw")
    assert "fc-key" not in repr(settings) and "gw'" not in repr(settings)


def test_missing_secrets_are_allowed_until_needed():
    """Missing secrets are allowed until needed."""
    assert Settings.from_mapping({}).secrets() == ()


@pytest.mark.parametrize(
    ("name", "value", "field"),
    [
        ("EMBEDDING_DIMENSIONS", "2048", "embedding_dimensions"),
        ("EMBEDDING_DIMENSIONS", "abc", "embedding_dimensions"),
        ("OCR_VERSION", "../x", "ocr_version"),
        ("EMBEDDING_MODEL", "a b", "embedding_model"),
        ("FIRECRAWL_URL", "http://x", "firecrawl_url"),
        ("INGESTION_LOG_LEVEL", "LOUD", "log_level"),
    ],
)
def test_invalid_values_name_the_field_not_the_value(name, value, field):
    """Invalid values name the field not the value."""
    with pytest.raises(SettingsError) as info:
        Settings.from_mapping({name: value, "FIRECRAWL_API_KEY": "fc-secret"})
    assert field in str(info.value)
    assert value not in str(info.value) and "fc-secret" not in str(info.value)

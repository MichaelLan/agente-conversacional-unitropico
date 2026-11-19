import pytest

from unitropico_ingestion.domain.chunking import (
    MAX_CHUNK_ID_BYTES,
    Chunk,
    build_chunk,
    make_chunk_id,
    text_sha256,
)


def test_chunk_id_fits_vectorize_limit_for_long_version_ids():
    """A 300-char version id still yields an id of at most 64 bytes."""
    version_id = "sha256:" + "f" * 64 + ":" + "versión-muy-larga" * 14
    chunk_id = make_chunk_id(version_id, "structural-v1", 12345, "b" * 64)
    assert len(chunk_id.encode("utf-8")) <= MAX_CHUNK_ID_BYTES
    assert chunk_id.isascii()


def test_chunk_id_is_deterministic_and_scoped():
    """Same inputs give the same id; version, strategy and ordinal change it."""
    base = make_chunk_id("v1", "structural-v1", 3, "c" * 64)
    assert base == make_chunk_id("v1", "structural-v1", 3, "c" * 64)
    assert base != make_chunk_id("v2", "structural-v1", 3, "c" * 64)
    assert base != make_chunk_id("v1", "fixed-v1", 3, "c" * 64)
    assert base != make_chunk_id("v1", "structural-v1", 4, "c" * 64)
    assert base.split(":")[1] == "0003"
    assert base.endswith("c" * 12)


def test_build_chunk_digest_matches_rag_contract():
    """text_sha256 is the hex SHA-256 of the UTF-8 text, as the RAG verifies."""
    chunk = build_chunk(
        version_id="v1",
        chunker_version="structural-v1",
        ordinal=0,
        text="Artículo 1. Objeto.",
        section="Artículo 1",
        page_start=2,
        page_end=3,
    )
    assert chunk.text_sha256 == text_sha256("Artículo 1. Objeto.")
    assert len(chunk.text_sha256) == 64


@pytest.mark.parametrize(
    ("text", "digest", "pages", "section"),
    [
        ("   ", None, (1, 1), None),
        ("texto", "0" * 64, (1, 1), None),
        ("texto", None, (2, 1), None),
        ("texto", None, (0, 1), None),
        ("texto", None, (1, 1), "  "),
    ],
)
def test_chunk_rejects_invalid_invariants(text, digest, pages, section):
    """Blank text, wrong digest, bad page range or blank section are rejected."""
    with pytest.raises(ValueError):
        Chunk(
            id="x",
            ordinal=0,
            text=text,
            text_sha256=digest or text_sha256(text),
            section=section,
            page_start=pages[0],
            page_end=pages[1],
            chunker_version="v",
        )

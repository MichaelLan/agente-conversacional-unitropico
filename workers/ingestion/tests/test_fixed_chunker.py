import pytest

from unitropico_ingestion.domain.chunking import PageText
from unitropico_ingestion.services.chunker import StructuralChunker
from unitropico_ingestion.services.fixed_chunker import (
    FIXED_CHUNKER_VERSION,
    FixedSizeChunker,
)
from unitropico_ingestion.services.preprocess import pages_from_markdown

PAGES = [
    PageText(1, "ARTÍCULO 1. OBJETO. " + "El programa apoya a los estudiantes. " * 20),
    PageText(
        2, "ARTÍCULO 2. ALCANCE. " + "La convocatoria es pública y abierta. " * 20
    ),
]


def test_windows_respect_size_overlap_and_pages():
    """Windows never exceed size, overlap their neighbors and map to pages."""
    chunks = FixedSizeChunker(size=300, overlap=60).chunk(PAGES, version_id="v")
    assert len(chunks) > 4
    assert all(len(c.text) <= 300 for c in chunks)
    assert all(c.section is None for c in chunks)
    assert all(c.chunker_version == FIXED_CHUNKER_VERSION for c in chunks)
    assert chunks[0].page_start == 1 and chunks[-1].page_end == 2
    assert any((c.page_start, c.page_end) == (1, 2) for c in chunks)
    for left, right in zip(chunks, chunks[1:], strict=False):
        assert right.text.split(" ")[0] in left.text[-80:]


def test_fixed_is_deterministic_and_skips_empty_pages():
    """Same input, same ids; empty pages contribute nothing."""
    pages = [*PAGES, PageText(3, ""), PageText(4, "   ")]
    first = FixedSizeChunker().chunk(pages, version_id="v")
    assert first == FixedSizeChunker().chunk(pages, version_id="v")
    assert max(c.page_end for c in first) == 2
    assert FixedSizeChunker().chunk([PageText(1, "")], version_id="v") == []


def test_ids_differ_from_structural_strategy():
    """Baseline and structural chunks of a version never share ids."""
    fixed = {c.id for c in FixedSizeChunker().chunk(PAGES, version_id="v")}
    structural = {c.id for c in StructuralChunker().chunk(PAGES, version_id="v")}
    assert fixed.isdisjoint(structural)


@pytest.mark.parametrize("kwargs", [{"size": 10}, {"size": 100, "overlap": 50}])
def test_rejects_invalid_parameters(kwargs):
    """Too small windows or overlap of half the size or more fail fast."""
    with pytest.raises(ValueError):
        FixedSizeChunker(**kwargs)


def test_pages_from_markdown_maps_fragment_pages():
    """Markers split pages; a fragment of pages 13–14 maps to physical pages."""
    pages = pages_from_markdown(
        "cronograma inicial\n<!-- page 2 -->\n<table></table> continuación",
        page_map={1: 13, 2: 14},
    )
    assert [p.page_number for p in pages] == [13, 14]
    assert "<!--" not in pages[0].text + pages[1].text
    assert [p.page_number for p in pages_from_markdown("a<!-- page 3 -->b")] == [1, 3]

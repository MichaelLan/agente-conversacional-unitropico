import json
from pathlib import Path

import pytest

from unitropico_ingestion.domain.ingestion import IngestionError
from unitropico_ingestion.services.ocr_parser import parse_ocr_response

FIXTURE = Path(__file__).parent / "fixtures/firecrawl_parse_response.json"


def _body(**data) -> bytes:
    return json.dumps({"success": True, "data": data}).encode()


def test_parses_per_page_markdown_from_synthetic_fixture():
    """Parses per page markdown from synthetic fixture."""
    result = parse_ocr_response(FIXTURE.read_bytes())
    assert result.page_count == 2
    assert [page.page_number for page in result.pages] == [1, 2]
    assert result.pages[0].markdown.startswith("# REGLAMENTO DE PRUEBA")
    assert "| Inscripción |" in result.pages[1].markdown
    assert result.text_pages == 2 and result.failed_pages == ()


def test_falls_back_to_page_markers_when_pages_are_absent():
    """Falls back to page markers when pages are absent."""
    markdown = (
        "Primera página\n\n---\n\n<!-- page 2 -->\n\nSegunda"
        "\n\n<!-- page 4 -->\n\nCuarta"
    )
    result = parse_ocr_response(
        _body(markdown=markdown, metadata={"numPages": 4, "totalPages": 4})
    )
    assert [p.page_number for p in result.pages] == [1, 2, 4]
    assert result.pages[2].markdown.strip() == "Cuarta"
    assert result.page_count == 4


def test_records_failed_block_pages_and_counts_blank_pages():
    """Records failed block pages and counts blank pages."""
    result = parse_ocr_response(
        _body(
            pages=[
                {"pageNumber": 1, "markdown": "Texto"},
                {"pageNumber": 2, "markdown": ""},
            ],
            blocks=[{"pageNumber": 2, "status": "failed", "items": []}],
            metadata={"numPages": 2},
        )
    )
    assert result.failed_pages == (2,) and result.text_pages == 1


@pytest.mark.parametrize(
    ("raw", "code", "detail"),
    [
        (b"not json", "ocr_failed", "invalid_json"),
        (
            json.dumps({"success": False, "error": "x"}).encode(),
            "ocr_failed",
            "unsuccessful_response",
        ),
        (
            _body(pages=[{"pageNumber": 0, "markdown": "x"}]),
            "ocr_failed",
            "invalid_page",
        ),
        (
            _body(
                pages=[
                    {"pageNumber": 1, "markdown": "a"},
                    {"pageNumber": 1, "markdown": "b"},
                ]
            ),
            "ocr_failed",
            "duplicate_page",
        ),
        (
            _body(
                pages=[{"pageNumber": 1, "markdown": "a"}],
                metadata={"numPages": 1, "totalPages": 9},
            ),
            "ocr_failed",
            "truncated pages=1 total=9",
        ),
        (_body(), "ocr_failed", "missing_pages"),
        (_body(markdown="   "), "ocr_empty", "no_pages"),
    ],
)
def test_rejects_invalid_responses_permanently(raw, code, detail):
    """Rejects invalid responses permanently."""
    with pytest.raises(IngestionError) as info:
        parse_ocr_response(raw)
    assert info.value.code == code
    assert info.value.detail == detail
    assert info.value.permanent

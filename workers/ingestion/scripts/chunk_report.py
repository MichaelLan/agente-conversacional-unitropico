"""Offline chunking report over extraction-corpus-v2 (read-only, stdout only).

Usage: uv run python scripts/chunk_report.py [path/to/documents.json]
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from unitropico_ingestion.domain.chunking import Chunk, PageText  # noqa: E402
from unitropico_ingestion.services.chunker import StructuralChunker  # noqa: E402
from unitropico_ingestion.services.fixed_chunker import FixedSizeChunker  # noqa: E402

DEFAULT_CORPUS = (
    ROOT.parents[1] / "evaluations/ocr/.artifacts/extraction-corpus-v2/documents.json"
)


def page_texts(document: dict) -> list[PageText]:
    """Pick one text per physical page: OCR Markdown if present, else pdftotext."""
    pages = []
    for page in document["pages"]:
        texts = {e["method"]: e["page"] for e in page["evidence"]}
        if "firecrawl_parse_ocr" in texts:
            text = texts["firecrawl_parse_ocr"].get("markdown", "")
        else:
            text = texts.get("pdftotext", {}).get("text", "")
        pages.append(PageText(page["physical_page"], text or ""))
    return pages


def describe(chunks: list[Chunk]) -> str:
    """Summarize chunk count, size distribution and section coverage."""
    if not chunks:
        return "0 chunks"
    sizes = sorted(len(c.text) for c in chunks)
    q = (
        statistics.quantiles(sizes, n=10, method="inclusive")
        if len(sizes) > 1
        else [sizes[0]] * 9
    )
    with_section = sum(c.section is not None for c in chunks)
    cross = sum(c.page_end > c.page_start for c in chunks)
    return (
        f"{len(chunks)} chunks | sección {with_section}/{len(chunks)} "
        f"({100 * with_section / len(chunks):.0f}%) | multipágina {cross} | "
        f"chars min {sizes[0]} p10 {q[0]:.0f} p50 {statistics.median(sizes):.0f} "
        f"p90 {q[8]:.0f} max {sizes[-1]}"
    )


def main() -> None:
    """Print a per-document comparison of structural and fixed chunking."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", nargs="?", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--max-chars", type=int, default=1800)
    parser.add_argument("--min-chars", type=int, default=200)
    parser.add_argument("--fixed-size", type=int, default=1000)
    parser.add_argument("--fixed-overlap", type=int, default=150)
    parser.add_argument("--examples", type=int, default=8)
    args = parser.parse_args()

    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    structural = StructuralChunker(max_chars=args.max_chars, min_chars=args.min_chars)
    fixed = FixedSizeChunker(size=args.fixed_size, overlap=args.fixed_overlap)
    print(
        f"structural={structural.version} max={args.max_chars} min={args.min_chars} | "
        f"fixed={fixed.version} size={args.fixed_size} overlap={args.fixed_overlap}"
    )
    totals = {"structural": 0, "fixed": 0, "section": 0}
    for document in corpus["documents"]:
        name = document["direct_document"]["source_name"]
        version_id = document["direct_document"]["id"]
        pages = page_texts(document)
        with_text = sum(bool(p.text.strip()) for p in pages)
        s_chunks = structural.chunk(pages, version_id=version_id)
        f_chunks = fixed.chunk(pages, version_id=version_id)
        totals["structural"] += len(s_chunks)
        totals["fixed"] += len(f_chunks)
        totals["section"] += sum(c.section is not None for c in s_chunks)
        print(f"\n## {name}\npáginas {len(pages)} (con texto {with_text})")
        print(f"  estructural: {describe(s_chunks)}")
        print(f"  fijo:        {describe(f_chunks)}")
        below = sum(len(c.text) < args.min_chars for c in s_chunks)
        print(f"  estructurales < min_chars: {below}")
        seen: list[str] = []
        for chunk in s_chunks:
            if chunk.section and chunk.section not in seen:
                seen.append(chunk.section)
        for section in seen[: args.examples]:
            print(f"    · {section}")
        if len(seen) > args.examples:
            print(f"    · … ({len(seen)} secciones distintas)")
    share = 100 * totals["section"] / max(totals["structural"], 1)
    print(
        f"\nTOTAL estructural {totals['structural']} (sección {share:.0f}%) | "
        f"fijo {totals['fixed']}"
    )


if __name__ == "__main__":
    main()

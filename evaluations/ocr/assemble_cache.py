"""Assemble per-document OCR cache objects for the ingestion Workflow (offline).

Combines, per document, the pages the visual audit marked reusable (ok/minor,
from extraction-corpus-v2) with the selective Firecrawl OCR pages (mapped from
fragment page to physical page). Each output is a Firecrawl-Parse-shaped JSON
accepted by ``parse_ocr_response`` and stored under the R2 key the Workflow's
``ocr`` step reads: ``ocr/{sha256}/{OCR_VERSION}-p{OCR_MAX_PAGES}.json``.
Nothing is uploaded: ``upload-plan.md`` lists the (unexecuted) commands.
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
from pilot import ARTIFACTS, BASE, digest, error_category, write_json
from prepare_selective import evidence
from remaining_send import coverage, decode, safe, sync_dir
from selective_send import EXECUTION_NAME, ROOT, load_manifest, lot_state

REPO = BASE.parents[1]
INGESTION_SRC = REPO / "workers/ingestion/src"
CORPUS = ARTIFACTS / "extraction-corpus-v2/documents.json"
CORPUS_SHA256 = "d0057b4292c2c94d3963aea6c2e33a3e0db368abf3ede936a3c5ce657563f0eb"
OUTPUT = ARTIFACTS / "ingestion-cache-v1"
# Defaults of workers/ingestion Settings and wrangler.jsonc vars (asserted in tests).
OCR_VERSION = "firecrawl-parse-v2-ocr-v1"
OCR_MAX_PAGES = 1000
BUCKET = "unitropico-documents"
SOURCES = ("reused_pdftotext", "reused_firecrawl", "firecrawl_selective_v1")


def require(condition: bool, label: str = "Assembly check failed") -> None:
    """Fail closed with a controlled label (never document text)."""
    if not condition:
        raise ValueError(label)


def cache_key(source_sha256: str) -> str:
    """Same key as ``unitropico_ingestion.services.ingestion.ocr_key``."""
    return f"ocr/{source_sha256}/{OCR_VERSION}-p{OCR_MAX_PAGES}.json"


def new_pages(manifest: dict, responses: dict[str, dict]) -> dict[tuple[str, int], str]:
    """Map every OCR page to (source_path, physical page) -> markdown."""
    result = {}
    for lot in manifest["lots"]:
        value = responses[lot["lot_id"]]
        coverage(value, lot["page_count"])
        for page in value["data"]["pages"]:
            physical = lot["mapping"][str(page["pageNumber"])]
            key = (lot["source_path"], physical)
            require(key not in result, "Duplicate physical page")
            result[key] = page["markdown"]
    return result


def reused_page(corpus_page: dict) -> tuple[str, str]:
    """Prefer the Firecrawl markdown the audit judged, else the pdftotext text."""
    evidence_list = corpus_page["evidence"]
    firecrawl = [e for e in evidence_list if e["method"] == "firecrawl_parse_ocr"]
    if firecrawl:
        require(len(firecrawl) == 1, "Ambiguous Firecrawl evidence")
        return "reused_firecrawl", firecrawl[0]["page"]["markdown"]
    direct = [e for e in evidence_list if e["method"] == "pdftotext"]
    require(len(direct) == 1, "Ambiguous pdftotext evidence")
    return "reused_pdftotext", direct[0]["page"]["text"]


def document_pages(doc: dict, corpus_doc: dict, fresh: dict) -> list[dict]:
    """Every physical page exactly once, with provenance; fail on any gap."""
    corpus_pages = {p["physical_page"]: p for p in corpus_doc["pages"]}
    reuse, selected = set(doc["reuse_pages"]), set(doc["selected_pages"])
    pages = []
    for n in range(1, doc["page_count"] + 1):
        if n in reuse:
            require(n in corpus_pages, "Missing reused page")
            source, markdown = reused_page(corpus_pages[n])
        elif n in selected:
            require((doc["source_path"], n) in fresh, "Missing OCR page")
            source, markdown = "firecrawl_selective_v1", fresh[(doc["source_path"], n)]
        else:
            raise ValueError("Physical page not covered")
        require(
            isinstance(markdown, str) and bool(markdown.strip()),
            "Page without markdown",
        )
        pages.append({"pageNumber": n, "markdown": markdown, "source": source})
    return pages


def cache_body(doc: dict, pages: list[dict], provenance: dict) -> dict:
    """Firecrawl Parse v2 shape; ``source``/``provenance`` are ignored by the parser."""
    markdown = pages[0]["markdown"]
    for page in pages[1:]:
        markdown += f"\n\n<!-- page {page['pageNumber']} -->\n\n" + page["markdown"]
    return {
        "success": True,
        "data": {
            "markdown": markdown,
            "pages": pages,
            "metadata": {"numPages": len(pages), "totalPages": len(pages)},
            "provenance": dict(
                provenance,
                schema="unitropico-ingestion-cache/v1",
                source_path=doc["source_path"],
                source_sha256=doc["source_sha256"],
                page_sources={
                    s: [p["pageNumber"] for p in pages if p["source"] == s]
                    for s in SOURCES
                },
                requires_review=True,
            ),
        },
    }


def encode(body: dict) -> bytes:
    """Deterministic UTF-8 JSON."""
    return (
        json.dumps(body, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    ).encode()


def check_with_ingestion_parser(raw: bytes, pages: list[dict]) -> None:
    """Round-trip through the real Worker parser (explicit sys.path, read-only)."""
    if str(INGESTION_SRC) not in sys.path:
        sys.path.insert(0, str(INGESTION_SRC))
    from unitropico_ingestion.services.ocr_parser import parse_ocr_response

    result = parse_ocr_response(raw)
    require(
        result.page_count == len(pages)
        and result.failed_pages == ()
        and result.text_pages == len(pages)
        and [(p.page_number, p.markdown) for p in result.pages]
        == [(p["pageNumber"], p["markdown"]) for p in pages],
        "Ingestion parser mismatch",
    )


def load_responses(manifest: dict, root: Path) -> tuple[dict, dict]:
    """Require every lot complete; return decoded bodies and their SHA-256."""
    responses, hashes = {}, {}
    for lot in manifest["lots"]:
        folder = root / EXECUTION_NAME / lot["lot_id"]
        state, _ = lot_state(folder)
        require(state == "complete", "Lot not complete")
        attempt = decode(safe(folder / "attempt.json").read_bytes())
        require(
            attempt["fragment_sha256"] == lot["sha256"], "Attempt identity mismatch"
        )
        raw = safe(folder / "response.raw").read_bytes()
        value = decode(raw)
        require(value["success"] is True, "Unsuccessful response")
        responses[lot["lot_id"]] = value
        hashes[lot["lot_id"]] = digest(raw)
    return responses, hashes


def upload_plan(entries: list[dict], output: Path) -> str:
    """Markdown with unexecuted wrangler commands, run from the repo root."""
    lines = [
        "# Plan de subida de caché OCR (NO ejecutado)",
        "",
        "Ejecutar desde la raíz del repositorio, con `wrangler` autenticado, ANTES de",
        "lanzar la ingesta (o el reproceso) de cada documento; si la caché no está,",
        "el paso `ocr` del Workflow pagará un OCR completo del documento.",
        "",
        "El PDF original también debe existir en `raw/{sha256}.pdf`: lo sube la API al",
        "cargar el documento por el panel (no subirlo a mano con otra clave).",
        "La caché es por SHA-256 del PDF: solo se reutiliza si el archivo cargado es",
        "byte a byte el original listado aquí.",
        "",
        f"Claves con `OCR_VERSION={OCR_VERSION}` y `OCR_MAX_PAGES={OCR_MAX_PAGES}`",
        "(valores por defecto de workers/ingestion). Si cambian, regenerar.",
        "",
        "```sh",
    ]
    for entry in entries:
        path = output / entry["key"]
        if path.is_relative_to(REPO):
            path = path.relative_to(REPO)  # Production: repo-relative path.
        lines.append(f"# {entry['source_path']} ({entry['page_count']} págs.)")
        lines.append(
            f"npx wrangler r2 object put {BUCKET}/{entry['key']} --file={path} "
            "--content-type=application/json --remote"
        )
    lines += ["```", ""]
    return "\n".join(lines)


def write_bytes(path: Path, data: bytes) -> None:
    """Exclusive, fsynced write (never replaces an existing object)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    sync_dir(path.parent)


def assemble(
    root: Path = ROOT,
    corpus_path: Path = CORPUS,
    output: Path = OUTPUT,
    artifacts: Path = ARTIFACTS,
) -> list[dict]:
    """Validate all inputs, build every cache object, verify, then write."""
    manifest = load_manifest(root)
    corpus_raw = safe(corpus_path).read_bytes()
    require(digest(corpus_raw) == CORPUS_SHA256, "Corpus identity mismatch")
    corpus = decode(corpus_raw)
    by_path = {d["direct_document"]["source_path"]: d for d in corpus["documents"]}
    responses, response_hashes = load_responses(manifest, root)
    fresh = new_pages(manifest, responses)
    require(len(fresh) == manifest["total_pages"], "OCR page total mismatch")
    built = []
    for doc in manifest["documents"]:
        corpus_doc = by_path[doc["source_path"]]
        identity = corpus_doc["direct_document"]
        require(
            identity["sha256_before"]
            == identity["sha256_after"]
            == doc["source_sha256"]
            and identity["page_count"] == doc["page_count"],
            "Corpus document identity mismatch",
        )
        source = safe(REPO / doc["source_path"])
        require(
            hashlib.sha256(source.read_bytes()).hexdigest() == doc["source_sha256"],
            "Source mismatch",
        )
        pages = document_pages(doc, corpus_doc, fresh)
        lots = [
            {
                "lot_id": lot["lot_id"],
                "response_sha256": response_hashes[lot["lot_id"]],
                "mapping": lot["mapping"],
            }
            for lot in manifest["lots"]
            if lot["source_path"] == doc["source_path"]
        ]
        body = cache_body(
            doc,
            pages,
            {
                "selective_manifest_sha256": digest(
                    safe(root / "manifest.json").read_bytes()
                ),
                "corpus_sha256": CORPUS_SHA256,
                "lots": lots,
            },
        )
        raw = encode(body)
        check_with_ingestion_parser(raw, pages)
        built.append((doc, pages, raw))
    output.mkdir()  # Exclusive: an existing output (even failed) blocks reuse.
    before = evidence(artifacts, output)
    entries = []
    for doc, pages, raw in built:
        key = cache_key(doc["source_sha256"])
        write_bytes(output / key, raw)
        entries.append(
            {
                "source_path": doc["source_path"],
                "source_sha256": doc["source_sha256"],
                "page_count": doc["page_count"],
                "key": key,
                "sha256": digest(raw),
                "bytes": len(raw),
                "pages_by_source": {
                    s: sum(p["source"] == s for p in pages) for s in SOURCES
                },
            }
        )
    write_bytes(output / "upload-plan.md", upload_plan(entries, output).encode())
    require(evidence(artifacts, output) == before, "Prior evidence changed")
    write_json(
        output / "assembly.json",
        {
            "schema_version": 1,
            "state": "assembled_offline_not_uploaded",
            "requires_review": True,
            "ocr_version": OCR_VERSION,
            "ocr_max_pages": OCR_MAX_PAGES,
            "corpus_sha256": CORPUS_SHA256,
            "response_sha256": response_hashes,
            "entries": entries,
        },
    )
    return entries


def main() -> int:
    """Offline only: fixed inputs and output; no network, upload or retries."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        entries = assemble()
    except Exception as error:  # noqa: BLE001 - guarda: toda falla se reporta por categoría
        print(f"Cache assembly failed: {error_category(error)}.", file=sys.stderr)
        return 1
    for entry in entries:
        print(f"{entry['key']} pages={entry['page_count']} {entry['pages_by_source']}")
    print("Assembled offline; nothing uploaded. See upload-plan.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

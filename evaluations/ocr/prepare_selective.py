"""Prepare the authorized 106-page selective OCR offline; never sends or retries.

Reads the visual audit (reports/extraction-quality-audit.json), selects per
document the physical pages classified for Firecrawl OCR or literal patch,
cuts one PDF fragment per lot with the local Poppler image (no network,
read-only source mount) and writes an immutable manifest. Output directory
``.artifacts/selective-ocr-v1/`` is reserved exclusively: an existing one
(even from a failed run) blocks reuse.
"""

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
from pilot import (
    ARTIFACTS,
    BASE,
    IMAGE,
    POPPLER_TIMEOUT,
    digest,
    docker_args,
    error_category,
    log_phase,
    page_count,
    write_json,
)

ROOT = BASE.parents[1]
AUDIT = BASE / "reports/extraction-quality-audit.json"
AUDIT_SHA256 = "5f808c52901a1ef75341af6087748ed0c399b9bb651a6069c0e951e378e06dd9"
OUTPUT = ARTIFACTS / "selective-ocr-v1"
TOTAL_PAGES = 106
MAX_LOT_PAGES = 20  # Far below the 300 s API timeout; 1 credit/page.
MAX_LOT_BYTES = 50_000_000  # Firecrawl Parse limit per request.
RENDER_DPI = 40  # Pixel identity check only, not a quality render.
EMPTY_TEXT_SHA256 = digest(b"\x0c")  # pdftotext emits one form feed per page
DOCS = "documents/documentos_bienestar/"
# Independent pins (extraction-corpus-v2 provenance): path -> (slug, sha256, pages).
ORIGINALS = {
    DOCS + "2. Resolución CBU No. 002-2025.pdf": (
        "res-cbu-002-2025",
        "a10a246e25e22e1df0847570154f27eb8782e745e140b5335d10d44beddd53c3",
        16,
    ),
    DOCS + "Boletin Renta Joven 01 VA.pdf": (
        "boletin-renta-joven",
        "d79766d84240fb74eebc1bba65b98ddf155c6334cab798f30ad2725c54a86a53",
        2,
    ),
    DOCS + "DOCUMENTO TECNICO RENTA JOVEN.pdf": (
        "documento-tecnico",
        "46af91fb3a4efc942070444631141442e0343b75ff5584c5a77f9598f302fc0a",
        62,
    ),
    DOCS + "M-IP-36 MANUAL PROGRAMA RENTA JOVEN V1.pdf": (
        "manual-m-ip-36",
        "7460b38f7360d0a4f965b47a2f21d2dbe6a5c282a015603c2385dc3b80fda214",
        75,
    ),
    DOCS + "Res00137ReglamentaRentaJoven.pdf": (
        "res-00137",
        "c855bd4b0f33947857778afc9271250e9c7fe78b6fdadf890dd3b2b56920e362",
        20,
    ),
    DOCS + "Resolución CBU No. 001 de 2026 - Convocatoria Pública 2026-1 (1).pdf": (
        "convocatoria-2026-1",
        "3ebfd85b26199382c759240321622a1009b791e16dd895f4f7ea34428da2aa26",
        17,
    ),
    DOCS + "politica de bienestar.pdf": (
        "politica-bienestar",
        "f3ea87c496e14e9e525ad3d0da96d5c20bb26289122ae303fcb74e98291019a9",
        24,
    ),
}
REUSE_CLASSES = ("ok", "minor")


def lot_options(page_total: int) -> dict:
    """OCR options as in the pilots; pages/blocks/pageMarkers add no cost."""
    return {
        "formats": ["markdown"],
        "timeout": 300000,
        "parsers": [
            {
                "type": "pdf",
                "mode": "ocr",
                "maxPages": page_total,
                "pages": True,
                "blocks": True,
                "pageMarkers": True,
            }
        ],
    }


def select_pages(audit: dict) -> list[dict]:
    """Return, per audited document, the reuse and reprocess page sets.

    Reprocess = ``firecrawl_ocr_pages`` + ``literal_patch_pages``; reuse = pages
    classified ok/minor. Both must partition the document and agree with the
    per-page classifications; the grand total must be exactly 106.
    """
    if audit.get("schema") != "extraction-quality-audit/v1":
        raise ValueError("Unexpected audit schema")
    documents = audit["documents"]
    if sorted(d["source_path"] for d in documents) != sorted(ORIGINALS):
        raise ValueError("Audit documents differ from pinned originals")
    result = []
    for doc in documents:
        slug, _, pages = ORIGINALS[doc["source_path"]]
        classes = {p["physical_page"]: p["classification"] for p in doc["pages"]}
        if doc["page_count"] != pages or sorted(classes) != list(range(1, pages + 1)):
            raise ValueError("Audit page coverage mismatch")
        ocr, patch = doc["firecrawl_ocr_pages"], doc["literal_patch_pages"]
        selected = sorted(ocr + patch)
        reuse = sorted(doc["reuse_pages"])
        if (
            len(set(selected)) != len(selected)
            or any(type(n) is not int for n in selected + reuse)
            or reuse != sorted(n for n, c in classes.items() if c in REUSE_CLASSES)
            or set(selected) & set(reuse)
            or sorted(selected + reuse) != list(range(1, pages + 1))
        ):
            raise ValueError("Audit selection inconsistent")
        result.append(
            {
                "slug": slug,
                "source_path": doc["source_path"],
                "page_count": pages,
                "reuse_pages": reuse,
                "selected_pages": selected,
                "firecrawl_ocr_pages": sorted(ocr),
                "literal_patch_pages": sorted(patch),
            }
        )
    if sum(len(d["selected_pages"]) for d in result) != TOTAL_PAGES:
        raise ValueError("Selection is not exactly 106 pages")
    return result


def plan_lots(selection: list[dict]) -> list[dict]:
    """Split each document's pages into balanced contiguous lots of <= 20."""
    lots = []
    for doc in selection:
        pages = doc["selected_pages"]
        if not pages:
            continue
        parts = math.ceil(len(pages) / MAX_LOT_PAGES)
        size = math.ceil(len(pages) / parts)
        for part in range(parts):
            chunk = pages[part * size : (part + 1) * size]
            suffix = f"-{part + 1}of{parts}" if parts > 1 else ""
            lot_id = f"{len(lots) + 1:02d}-{doc['slug']}{suffix}"
            lots.append(
                {
                    "lot_id": lot_id,
                    "slug": doc["slug"],
                    "source_path": doc["source_path"],
                    "file": f"lots/{lot_id}.pdf",
                    "page_count": len(chunk),
                    "physical_pages": chunk,
                    "mapping": {str(i): n for i, n in enumerate(chunk, 1)},
                }
            )
    if any(lot["page_count"] > MAX_LOT_PAGES for lot in lots) or (
        sum(lot["page_count"] for lot in lots) != TOTAL_PAGES
    ):
        raise ValueError("Lot plan invalid")
    return lots


def evidence(artifacts: Path, exclude: Path) -> dict:
    """Hash prior artifact bytes (never interpreted or printed)."""
    result = {}
    for path in sorted(artifacts.rglob("*")):
        if path == exclude or exclude in path.parents:
            continue
        if path.is_symlink():
            raise ValueError("Symlink evidence")
        if path.is_file():
            data = path.read_bytes()
            result[str(path.relative_to(artifacts))] = {
                "sha256": digest(data),
                "bytes": len(data),
            }
    return result


def cut_lot(source: Path, output: Path, lot: dict) -> None:
    """pdfseparate each physical page, then pdfunite in lot order (no network)."""
    if "," in str(source) or "," in str(output):
        raise ValueError("Unsupported mount path")
    work = f"/out/pages/{lot['lot_id']}"
    separate = " && ".join(
        f"pdfseparate -f {n} -l {n} /source.pdf {work}/page-{n}.pdf"
        for n in lot["physical_pages"]
    )
    ordered = " ".join(f"{work}/page-{n}.pdf" for n in lot["physical_pages"])
    (output / "pages" / lot["lot_id"]).mkdir(parents=True)
    subprocess.run(
        docker_args()
        + [
            "--mount",
            f"type=bind,src={source},dst=/source.pdf,readonly",
            "--mount",
            f"type=bind,src={output},dst=/out",
            IMAGE,
            "sh",
            "-c",
            f"{separate} && pdfunite {ordered} /out/{lot['file']}",
        ],
        check=True,
        capture_output=True,
        timeout=POPPLER_TIMEOUT,
    )


def page_fingerprints(source: Path, fragment: Path, lot: dict) -> list[dict]:
    """Compare text, a low-dpi render and page size/rotation per mapped page."""
    render = f"pdftoppm -q -r {RENDER_DPI} -gray"
    lines = []
    for i, n in lot["mapping"].items():
        lines.append(
            f"printf 'O {n} '; pdftotext -q -f {n} -l {n} /source.pdf - | sha256sum; "
            f"printf 'F {i} '; pdftotext -q -f {i} -l {i} /frag.pdf - | sha256sum; "
            f"printf 'I {n} '; {render} -f {n} -l {n} /source.pdf | sha256sum; "
            f"printf 'J {i} '; {render} -f {i} -l {i} /frag.pdf | sha256sum"
        )
    last_source = max(lot["physical_pages"])
    lines.append(f"pdfinfo -f 1 -l {last_source} /source.pdf | sed 's/^/S /'")
    lines.append(f"pdfinfo -f 1 -l {lot['page_count']} /frag.pdf | sed 's/^/G /'")
    output = subprocess.run(
        docker_args()
        + [
            "--mount",
            f"type=bind,src={source},dst=/source.pdf,readonly",
            "--mount",
            f"type=bind,src={fragment.resolve()},dst=/frag.pdf,readonly",
            IMAGE,
            "sh",
            "-c",
            "; ".join(lines),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=POPPLER_TIMEOUT,
    ).stdout
    return compare_fingerprints(output, lot)


def compare_fingerprints(output: str, lot: dict) -> list[dict]:
    """Parse the Poppler report and require every mapped page to match."""
    text = {"O": {}, "F": {}, "I": {}, "J": {}}
    geometry = {"S": {}, "G": {}}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] in text:
            text[parts[0]][int(parts[1])] = parts[2]
        elif len(parts) >= 4 and parts[0] in geometry and parts[1] == "Page":
            # "Page    3 size: 612 x 792 pts (letter)" / "Page    3 rot:  0"
            if parts[3] in ("size:", "rot:"):
                key = (int(parts[2]), parts[3])
                geometry[parts[0]][key] = " ".join(parts[4:])
    pages = []
    for i, n in lot["mapping"].items():
        i = int(i)
        original, fragment = text["O"].get(n), text["F"].get(i)
        same_geometry = all(
            geometry["S"].get((n, k)) == geometry["G"].get((i, k)) is not None
            for k in ("size:", "rot:")
        )
        image, fragment_image = text["I"].get(n), text["J"].get(i)
        if (
            original is None
            or original != fragment
            or image is None
            or image != fragment_image
            or not same_geometry
        ):
            raise ValueError("Fragment page does not match original page")
        pages.append(
            {
                "fragment_page": i,
                "physical_page": n,
                "text_sha256": original,
                "text_layer_empty": original == EMPTY_TEXT_SHA256,
                "render_sha256": image,
                "size": geometry["S"][(n, "size:")],
                "rotation": geometry["S"][(n, "rot:")],
            }
        )
    return pages


def prepare(output: Path = OUTPUT, artifacts: Path = ARTIFACTS) -> dict:
    """Cut every lot, verify it, then write the manifest last (exclusive)."""
    started = time.monotonic()
    audit_raw = AUDIT.read_bytes()
    if digest(audit_raw) != AUDIT_SHA256:
        raise ValueError("Audit identity mismatch")
    selection = select_pages(json.loads(audit_raw))
    lots = plan_lots(selection)
    log_phase("selection_validated", started)
    output.mkdir()  # Exclusive: never exist_ok, a failed run blocks reuse.
    (output / "lots").mkdir()
    before = evidence(artifacts, output)
    sources_before = {}
    for path, (_, sha, pages) in ORIGINALS.items():
        source = ROOT / path
        if source.is_symlink() or not source.is_file():
            raise ValueError("Invalid source")
        if digest(source.read_bytes()) != sha or page_count(source) != pages:
            raise ValueError("Source identity mismatch")
        sources_before[path] = sha
    log_phase("sources_validated", started)
    for lot in lots:
        source = ROOT / lot["source_path"]
        cut_lot(source, output, lot)
        fragment = output / lot["file"]
        data = fragment.read_bytes()
        if (
            not data.startswith(b"%PDF-")
            or not 0 < len(data) <= MAX_LOT_BYTES
            or page_count(fragment) != lot["page_count"]
        ):
            raise ValueError("Fragment integrity mismatch")
        lot["pages"] = page_fingerprints(source, fragment, lot)
        lot["sha256"] = digest(data)
        lot["bytes"] = len(data)
        lot["source_sha256"] = sources_before[lot["source_path"]]
        lot["options"] = lot_options(lot["page_count"])
        log_phase("lot_prepared", started)
    for path, sha in sources_before.items():
        if digest((ROOT / path).read_bytes()) != sha:
            raise ValueError("Source changed")
    after = evidence(artifacts, output)
    if before != after:
        raise ValueError("Prior evidence changed")
    manifest = {
        "schema_version": 1,
        "state": "prepared_offline_not_sent",
        "image": IMAGE,
        "audit": str(AUDIT.relative_to(ROOT)),
        "audit_sha256": AUDIT_SHA256,
        "total_pages": TOTAL_PAGES,
        "estimated_credits": TOTAL_PAGES,
        "spent_by_preparation": 0,
        "max_lot_pages": MAX_LOT_PAGES,
        "max_lot_bytes": MAX_LOT_BYTES,
        "documents": [
            dict(doc, source_sha256=sources_before[doc["source_path"]])
            for doc in selection
        ],
        "lots": lots,
        "evidence_before": before,
        "evidence_after": after,
    }
    write_json(output / "manifest.json", manifest)
    log_phase("manifest_written", started)
    return manifest


def main() -> int:
    """Offline only: fixed audit, sources and output; no credentials or POST."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    started = time.monotonic()
    try:
        manifest = prepare()
    except Exception as error:  # noqa: BLE001 - guarda: toda falla se reporta por categoría, sin reintento
        log_phase("prepare_failed", started, category=error_category(error))
        print(
            "Preparation failed; no POST; do not reuse the directory.", file=sys.stderr
        )
        return 1
    for lot in manifest["lots"]:
        print(
            f"{lot['lot_id']}: {lot['page_count']} pages {lot['physical_pages']} "
            f"{lot['bytes']} bytes sha256={lot['sha256']}"
        )
    sha = digest((OUTPUT / "manifest.json").read_bytes())
    print(f"Prepared offline: {TOTAL_PAGES} pages; manifest sha256={sha}; no POST.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

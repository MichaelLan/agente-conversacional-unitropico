"""Prepare the Azure OCR batch v2 (69 pages) offline; never sends anything.

Same method as ``prepare_alt_pilot.py``: ONE single-page PDF per page, cut with
the local Poppler image (no network, read-only source mount) and verified
against its original physical page (text layer, low-dpi render, size,
rotation). Output ``.artifacts/alt-ocr-v2/`` is reserved exclusively: an
existing one (even from a failed run) blocks reuse.

Selection (decision of the orchestrator, see README "Azure v2"):
 - the 61 physical pages of the selective-ocr-v1 lots 04-07 (same pages), and
 - 8 pages of lots 01-03 that contain charts/diagrams with figures (the audit
   ``selective-ocr-v1-audit.json`` flagged no further ``manual_review`` pages:
   its three, 01 p3, 02 p16 and 02 p54, belong to the pilot already processed).
The 10 pilot pages are excluded.
"""

import argparse
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
from prepare_alt_pilot import MAX_PAGE_BYTES, PILOT, cut_page
from prepare_selective import ORIGINALS, evidence, page_fingerprints

ROOT = BASE.parents[1]
OUTPUT = ARTIFACTS / "alt-ocr-v2"
TOTAL_PAGES = 69
# Azure F0 accepts 4 MB per file; pdfseparate keeps every shared object of the
# scan (19.6 MB per Res. 00137 page, 7.7 MB per Convocatoria page), so those
# pages are re-cut with pdftocairo, which keeps only the page's own JPEG as is.
F0_MAX_FRAGMENT_BYTES = 3_000_000
RENDER_CHECK_DPI = 100
# (document number as in the selective lots, slug, physical pages).
PLAN = (
    ("02", "documento-tecnico", (17, 21, 30, 32, 44, 46)),
    ("03", "manual-m-ip-36", (27, 29, 41, 47, 52, 61, 62, 63, 64, 65, 67, 68, 74, 75)),
    ("05", "res-00137", (1, 2, 3, 4, 5, 9, 11, 13, 14, 17, 18, 19)),
    ("06", "convocatoria-2026-1", tuple(range(1, 18))),
    ("07", "politica-bienestar", (1, 2, 3, 4, *range(7, 19), 20, 22, 23, 24)),
)


def plan_pages() -> list[dict]:
    """Return the fixed 69 pages with their pinned original identities."""
    by_slug = {slug: (path, sha, n) for path, (slug, sha, n) in ORIGINALS.items()}
    pilot = {(slug, n) for _, slug, pages in PILOT for n in pages}
    pages = []
    for number, slug, physical in PLAN:
        path, sha, total = by_slug[slug]
        for n in physical:
            if not 1 <= n <= total or (slug, n) in pilot:
                raise ValueError("Page outside document or already in the pilot")
            page_id = f"{number}-{slug}-p{n:02d}"
            pages.append(
                {
                    "page_id": page_id,
                    "slug": slug,
                    "source_path": path,
                    "source_sha256": sha,
                    "physical_page": n,
                    "file": f"pages/{page_id}.pdf",
                }
            )
    if len(pages) != TOTAL_PAGES or len({p["page_id"] for p in pages}) != TOTAL_PAGES:
        raise ValueError("Batch is not exactly 69 distinct pages")
    return pages


def recut_small(source: Path, output: Path, page: dict) -> None:
    """Replace an oversize pdfseparate fragment by a pdftocairo single-page PDF."""
    if "," in str(source) or "," in str(output):
        raise ValueError("Unsupported mount path")
    (output / page["file"]).unlink()
    subprocess.run(
        docker_args()
        + [
            "--mount",
            f"type=bind,src={source},dst=/source.pdf,readonly",
            "--mount",
            f"type=bind,src={output},dst=/out",
            IMAGE,
            "pdftocairo",
            "-pdf",
            "-f",
            str(page["physical_page"]),
            "-l",
            str(page["physical_page"]),
            "/source.pdf",
            f"/out/{page['file']}",
        ],
        check=True,
        capture_output=True,
        timeout=POPPLER_TIMEOUT,
    )


def same_render(source: Path, fragment: Path, physical_page: int) -> str:
    """100 dpi gray render hash of the original page and of the fragment; must match."""
    n, dpi = physical_page, RENDER_CHECK_DPI
    out = subprocess.run(
        docker_args()
        + [
            "--mount",
            f"type=bind,src={source},dst=/source.pdf,readonly",
            "--mount",
            f"type=bind,src={fragment.resolve()},dst=/frag.pdf,readonly",
            IMAGE,
            "sh",
            "-c",
            f"pdftoppm -q -r {dpi} -gray -f {n} -l {n} /source.pdf | sha256sum; "
            f"pdftoppm -q -r {dpi} -gray -f 1 -l 1 /frag.pdf | sha256sum",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=POPPLER_TIMEOUT,
    ).stdout.split()
    if len(out) != 4 or out[0] != out[2]:
        raise ValueError("Fragment render mismatch")
    return out[0]


def prepare(output: Path = OUTPUT, artifacts: Path = ARTIFACTS) -> dict:
    """Cut and verify every page, then write the manifest last (exclusive)."""
    started = time.monotonic()
    pages = plan_pages()
    output.mkdir()  # Exclusive: never exist_ok, a failed run blocks reuse.
    (output / "pages").mkdir()
    before = evidence(artifacts, output)
    for path, (_, sha, count) in ORIGINALS.items():
        source = ROOT / path
        if source.is_symlink() or not source.is_file():
            raise ValueError("Invalid source")
        if digest(source.read_bytes()) != sha or page_count(source) != count:
            raise ValueError("Source identity mismatch")
    log_phase("sources_validated", started)
    for page in pages:
        source = ROOT / page["source_path"]
        cut_page(source, output, page)
        fragment = output / page["file"]
        page["method"] = "pdfseparate"
        if fragment.stat().st_size > F0_MAX_FRAGMENT_BYTES:
            recut_small(source, output, page)
            page["method"] = "pdftocairo"
        data = fragment.read_bytes()
        if (
            not data.startswith(b"%PDF-")
            or not 0 < len(data) <= MAX_PAGE_BYTES
            or page_count(fragment) != 1
        ):
            raise ValueError("Fragment integrity mismatch")
        check = {
            "mapping": {"1": page["physical_page"]},
            "physical_pages": [page["physical_page"]],
            "page_count": 1,
        }
        page["fingerprint"] = page_fingerprints(source, fragment, check)[0]
        if page["method"] == "pdftocairo":
            page["render_100dpi_sha256"] = same_render(
                source, fragment, page["physical_page"]
            )
            if len(data) > F0_MAX_FRAGMENT_BYTES:
                raise ValueError("Fragment too large for F0")
        page["sha256"] = digest(data)
        page["bytes"] = len(data)
        log_phase("page_prepared", started)
    for path, (_, sha, _) in ORIGINALS.items():
        if digest((ROOT / path).read_bytes()) != sha:
            raise ValueError("Source changed")
    after = evidence(artifacts, output)
    if before != after:
        raise ValueError("Prior evidence changed")
    manifest = {
        "schema_version": 1,
        "campaign": "alt-ocr-v2",
        "state": "prepared_offline_not_sent",
        "image": IMAGE,
        "total_pages": TOTAL_PAGES,
        "spent_by_preparation": 0,
        "pages": pages,
        "evidence_before": before,
        "evidence_after": after,
    }
    write_json(output / "manifest.json", manifest)
    log_phase("manifest_written", started)
    return manifest


def main() -> int:
    """Offline only: fixed sources and output; no credentials, no network."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    started = time.monotonic()
    try:
        manifest = prepare()
    except Exception as error:  # noqa: BLE001 - guarda: toda falla se reporta por categoría, sin reintento
        log_phase("prepare_failed", started, category=error_category(error))
        print("Preparation failed; do not reuse the directory.", file=sys.stderr)
        return 1
    for page in manifest["pages"]:
        print(
            f"{page['page_id']}\t{page['physical_page']}\t{page['bytes']}\t"
            f"{page['sha256']}"
        )
    sha = digest((OUTPUT / "manifest.json").read_bytes())
    print(f"Prepared offline: {TOTAL_PAGES} pages; manifest sha256={sha}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

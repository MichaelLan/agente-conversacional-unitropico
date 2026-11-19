"""Prepare the 10-page alternative-OCR pilot offline; never sends anything.

Cuts ONE single-page PDF per pilot page with the local Poppler image (no
network, read-only source mount), verifies every fragment against its original
page (text layer, low-dpi render, size, rotation) and writes an immutable
manifest. Output ``.artifacts/alt-ocr-pilot-v1/`` is reserved exclusively: an
existing one (even from a failed run) blocks reuse. One page per request keeps
Azure F0 (2 pages per request) within its limit.
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
from prepare_selective import ORIGINALS, evidence, page_fingerprints

ROOT = BASE.parents[1]
OUTPUT = ARTIFACTS / "alt-ocr-pilot-v1"
TOTAL_PAGES = 10
MAX_PAGE_BYTES = 50_000_000
# (document number, slug, physical pages) from docs/research/ocr-alternatives.md §6.
PILOT = (
    ("01", "res-cbu-002-2025", (3, 12, 13, 14)),
    ("02", "documento-tecnico", (16, 18, 20, 22, 54)),
    ("03", "manual-m-ip-36", (38,)),
)


def plan_pages() -> list[dict]:
    """Return the fixed ten pages with their pinned original identities."""
    by_slug = {slug: (path, sha, n) for path, (slug, sha, n) in ORIGINALS.items()}
    pages = []
    for number, slug, physical in PILOT:
        path, sha, total = by_slug[slug]
        for n in physical:
            if not 1 <= n <= total:
                raise ValueError("Page outside document")
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
    if len(pages) != TOTAL_PAGES:
        raise ValueError("Pilot is not exactly 10 pages")
    return pages


def cut_page(source: Path, output: Path, page: dict) -> None:
    """pdfseparate one physical page into pages/<page_id>.pdf (no network)."""
    if "," in str(source) or "," in str(output):
        raise ValueError("Unsupported mount path")
    subprocess.run(
        docker_args()
        + [
            "--mount",
            f"type=bind,src={source},dst=/source.pdf,readonly",
            "--mount",
            f"type=bind,src={output},dst=/out",
            IMAGE,
            "pdfseparate",
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
            f"{page['page_id']}: physical page {page['physical_page']} "
            f"{page['bytes']} bytes sha256={page['sha256']}"
        )
    sha = digest((OUTPUT / "manifest.json").read_bytes())
    print(f"Prepared offline: {TOTAL_PAGES} pages; manifest sha256={sha}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

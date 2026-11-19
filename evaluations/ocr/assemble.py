"""Assemble only approved offline evidence; never correct, index or publish text."""

import argparse
import json
import os
import sys

import audit
from pilot import (
    BASE,
    FRAGMENT_BYTES,
    FRAGMENT_SHA256,
    MAPPING,
    ORIGINAL,
    ORIGINAL_SHA256,
    digest,
    error_category,
    write_json,
)

ROOT = BASE.parents[1]
DIRECT_SHA256 = "c1e08273d010bf6e97c22753ffb02c173f043052836346a78de1dd568e3f2e4e"
AUDIT_SHA256 = "07f51846d73f91d251468680e87135bd7dd0cdcc7cbcf0a2d54464bab456abc1"
REPORT_SHA256 = "3f1b342a588b015a25ed15b84ea24990a8cb3b56778b51f302ae4958d85c392a"
GUARDS = {"requires_review": True, "publication_blocked": True, "published": False}


def require(condition):
    if not condition:
        raise ValueError("Invalid approved evidence")


def safe(path):
    require(
        ".." not in path.parts
        and not any(p.is_symlink() for p in (path, *path.parents))
    )
    return path


def assemble():
    output = safe(BASE / ".artifacts/extraction-corpus-v1")
    output.mkdir()  # Failed reservations also block retries.
    inputs = {}

    def load(relative, expected=None, binary=False):
        path = safe(BASE / relative)
        data = path.read_bytes()
        sha = digest(data)
        require(expected is None or sha == expected)
        inputs[path] = sha
        return data if binary else json.loads(data)

    direct_name = ".artifacts/extraction-direct-v1/documents.json"
    response_name = ".artifacts/second-attempt/response.raw"
    direct = load(direct_name, DIRECT_SHA256)
    manifest = load(".artifacts/extraction-direct-v1/manifest.json")
    previous = load(".artifacts/corpus-audit.json", AUDIT_SHA256)
    load("reports/pilot-02.md", REPORT_SHA256, binary=True)
    require(
        manifest["schema_version"] == direct["schema_version"] == 1
        and manifest["state"] == "complete_requires_review"
        and manifest["published"] is False
        and direct["published"] is False
        and direct["requires_review"] is True
        and manifest["document_count"] == len(direct["documents"]) == 7
        and manifest["documents_sha256"] == DIRECT_SHA256
        and manifest["prior_artifacts"]
        == {str(BASE / ".artifacts/corpus-audit.json"): AUDIT_SHA256}
        and manifest["totals"] == direct["totals"]
    )
    paths = audit.inventory()
    old = {d["file"]: d for d in previous["documents"]}
    docs = {d["source_name"]: d for d in direct["documents"]}
    require(
        len(old) == len(previous["documents"]) == len(docs) == 7
        and set(old) == set(docs) == {p.name for p in paths}
    )
    totals = {
        "documents": 7,
        "pages": 0,
        "direct_text": 0,
        "ocr_evidence": 2,
        "context_available": 0,
        "without_text": 0,
    }
    triage = {"pages": 0, "pending_ocr": 0, "sparse_review": 0, "direct_candidate": 0}
    documents = []
    for path in paths:
        data = safe(path).read_bytes()
        sha = digest(data)
        inputs[path] = sha
        doc, prior = docs[path.name], old[path.name]
        require(
            doc["source_path"] == str(path.relative_to(ROOT))
            and doc["sha256_before"]
            == doc["sha256_after"]
            == prior["sha256_before"]
            == prior["sha256_after"]
            == sha
            and doc["id"] == "sha256:" + sha
            and doc["bytes"] == doc["bytes_after"] == prior["bytes"] == len(data)
            and doc["page_count"] == prior["pages"] == len(doc["pages"])
            and [p["physical_page"] for p in doc["pages"]]
            == list(range(1, doc["page_count"] + 1))
            and [p["charcount"] for p in doc["pages"]] == prior["page_charcounts"]
        )
        pages = []
        for page in doc["pages"]:
            chars = len(page["text"].strip())
            status = (
                "pending_ocr"
                if not chars
                else "sparse_review"
                if chars < 80
                else "direct_candidate"
            )
            require(
                page["charcount"] == chars
                and page["method"] == "pdftotext"
                and page["status"] == status
            )
            triage["pages"] += 1
            triage[status] += 1
            totals["pages"] += 1
            totals["direct_text"] += bool(chars)
            pages.append(
                dict(
                    physical_page=page["physical_page"],
                    **GUARDS,
                    issues=[],
                    context_available=bool(chars),
                    evidence=[
                        {
                            "method": "pdftotext",
                            "page": page,
                            "source_artifact": direct_name,
                            "source_artifact_sha256": DIRECT_SHA256,
                        }
                    ],
                )
            )
        documents.append(
            dict(
                direct_document={k: v for k, v in doc.items() if k != "pages"},
                pages=pages,
                **GUARDS,
            )
        )
    require(
        triage == direct["totals"]
        and totals["pages"] == 216
        and totals["direct_text"] == 179
    )
    provenance = load(".artifacts/second-attempt/manifest.json")
    attempt = load(".artifacts/second-attempt/attempt.json")
    result = load(".artifacts/second-attempt/result.json")
    fragment = load(
        ".artifacts/second-attempt/pages-13-14.pdf", FRAGMENT_SHA256, binary=True
    )
    original = str(ORIGINAL.relative_to(ROOT))
    require(
        provenance["mapping"] == attempt["mapping"] == MAPPING
        and provenance["page_count"] == 2
        and provenance["original_page_count"] == 17
        and provenance["original"] == original
        and provenance["original_sha256"]
        == provenance["original_sha256_after"]
        == ORIGINAL_SHA256
        and inputs[ORIGINAL] == ORIGINAL_SHA256
        and provenance["fragment_sha256"]
        == attempt["fragment_sha256"]
        == FRAGMENT_SHA256
        and provenance["fragment_bytes"]
        == attempt["fragment_bytes"]
        == len(fragment)
        == FRAGMENT_BYTES
        and fragment.startswith(b"%PDF-")
        and result == {"http_status": 200, "state": "response_saved_no_replay"}
        and attempt["state"] == "reserved_may_have_been_sent"
        and attempt["options"]
        == {
            "formats": ["markdown"],
            "timeout": 300000,
            "parsers": [
                {
                    "type": "pdf",
                    "mode": "ocr",
                    "maxPages": 2,
                    "pages": True,
                    "blocks": True,
                    "pageMarkers": True,
                }
            ],
        }
    )
    raw = load(response_name)
    require(raw["success"] is True)
    ocr = raw["data"]
    require(
        ocr["metadata"]["numPages"] == ocr["metadata"]["totalPages"] == 2
        and [p["pageNumber"] for p in ocr["pages"]] == [1, 2]
        and [p["pageNumber"] for p in ocr["blocks"]] == [1, 2]
        and isinstance(ocr["markdown"], str)
        and bool(ocr["markdown"].strip())
    )
    target = next(
        d for d in documents if d["direct_document"]["source_path"] == original
    )
    require(len(target["pages"]) == 17 and set(MAPPING.values()) == {13, 14})
    for page, blocks in zip(ocr["pages"], ocr["blocks"], strict=False):
        n = page["pageNumber"]
        require(
            isinstance(page["markdown"], str)
            and bool(page["markdown"].strip())
            and blocks["status"] == "ok"
            and bool(blocks["items"])
            and len({b["id"] for b in blocks["items"]}) == len(blocks["items"])
        )
        record = target["pages"][MAPPING[str(n)] - 1]
        require(
            not record["context_available"]
        )  # Alternative evidence, never another page.
        record["context_available"] = True
        record["evidence"].append(
            {
                "method": "firecrawl_parse_ocr",
                "faithful_transcription": False,
                "source_artifact": response_name,
                "source_artifact_sha256": inputs[BASE / response_name],
                "mapping": {
                    "fragment_page": n,
                    "original_physical_page": MAPPING[str(n)],
                },
                "provenance": provenance,
                "attempt": attempt,
                "result": result,
                "page": page,
                "blocks": blocks,
            }
        )
        record["issues"] = (
            [
                {
                    "code": "missing_initial_date",
                    "location": "Inscripción × Fecha Inicial",
                },
                {"code": "reading_order_altered", "location": "before artículo 14"},
            ]
            if n == 1
            else [
                {
                    "code": "word_substitution",
                    "location": "Reclamaciones: realizaciones",
                },
                {
                    "code": "data_row_in_thead",
                    "location": "continuación de Verificación",
                },
            ]
        )
        for issue in record["issues"]:
            issue.update(
                source_artifact="reports/pilot-02.md",
                source_artifact_sha256=REPORT_SHA256,
                correction_applied=False,
            )
    totals["context_available"] = sum(
        p["context_available"] for d in documents for p in d["pages"]
    )
    totals["without_text"] = totals["pages"] - totals["context_available"]
    require(totals["context_available"] == 181 and totals["without_text"] == 35)
    require(
        audit.inventory() == paths
        and all(digest(safe(p).read_bytes()) == sha for p, sha in inputs.items())
    )
    payload = dict(
        schema_version=1,
        **GUARDS,
        documents=documents,
        totals=totals,
        ocr_response_data=ocr,
        span_reference="provider original; not recalculated or reinterpreted",
    )
    write_json(output / "documents.json", payload)
    completion = dict(
        schema_version=1,
        state="complete_requires_review",
        **GUARDS,
        totals=totals,
        inputs_sha256_before={
            str(p.relative_to(ROOT)): sha for p, sha in inputs.items()
        },
        inputs_sha256_after={
            str(p.relative_to(ROOT)): digest(safe(p).read_bytes()) for p in inputs
        },
        documents_sha256=digest((output / "documents.json").read_bytes()),
    )
    require(completion["inputs_sha256_before"] == completion["inputs_sha256_after"])
    write_json(output / "manifest.record.json", completion)
    os.link(
        output / "manifest.record.json", output / "manifest.json"
    )  # Atomic exclusive completion marker.


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Fixed approved sources only. Existing output, even failed, blocks retries. Requires review; publication and automatic indexing blocked. No OCR, network, chunks or corrections.",
    )
    parser.parse_args()
    try:
        assemble()
    except Exception as error:  # noqa: BLE001 - guarda: toda falla se reporta por categoría, sin reintento
        print(f"Assembly failed: {error_category(error)}.", file=sys.stderr)
        return 1
    print("Corpus assembled: 7 documents; 216 pages; publication blocked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

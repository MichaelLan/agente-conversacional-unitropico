"""Synthetic selective-OCR fixtures: fake PDFs, no institutional text or credentials."""

import json
from pathlib import Path
from unittest.mock import patch

import prepare_selective as prep
import selective_send as s
from pilot import digest

# doc-a: physical 2 and 4 reprocessed (fragment 1→2, 2→4); doc-b fully reused.
DOCS = (
    {
        "slug": "doc-a",
        "source_path": "docs/a.pdf",
        "page_count": 4,
        "reuse_pages": [1, 3],
        "selected_pages": [2, 4],
    },
    {
        "slug": "doc-b",
        "source_path": "docs/b.pdf",
        "page_count": 2,
        "reuse_pages": [1, 2],
        "selected_pages": [],
    },
    {
        "slug": "doc-c",
        "source_path": "docs/c.pdf",
        "page_count": 1,
        "reuse_pages": [],
        "selected_pages": [1],
    },
)
TOTAL = 3
KEY = "FAKE_SECRET_KEY"


def make_selective(test, root: Path) -> tuple[dict, Path, Path]:
    """Write fake sources, fragments and a manifest; patch the sender pins to them."""
    repo, selective = root / "repo", root / "selective"
    (selective / "lots").mkdir(parents=True)
    documents, lots = [], []
    for doc in DOCS:
        data = b"%PDF-source-" + doc["slug"].encode()
        path = repo / doc["source_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        documents.append(
            dict(
                doc,
                source_sha256=digest(data),
                firecrawl_ocr_pages=doc["selected_pages"],
                literal_patch_pages=[],
            )
        )
        if not doc["selected_pages"]:
            continue
        lot_id = f"{len(lots) + 1:02d}-{doc['slug']}"
        fragment = b"%PDF-fragment-" + lot_id.encode()
        (selective / "lots" / f"{lot_id}.pdf").write_bytes(fragment)
        n = len(doc["selected_pages"])
        lots.append(
            {
                "lot_id": lot_id,
                "slug": doc["slug"],
                "source_path": doc["source_path"],
                "file": f"lots/{lot_id}.pdf",
                "page_count": n,
                "physical_pages": doc["selected_pages"],
                "mapping": {str(i): p for i, p in enumerate(doc["selected_pages"], 1)},
                "sha256": digest(fragment),
                "bytes": len(fragment),
                "source_sha256": digest(data),
                "options": prep.lot_options(n),
                "pages": [],
            }
        )
    manifest = {
        "schema_version": 1,
        "state": "prepared_offline_not_sent",
        "image": s.IMAGE,
        "total_pages": TOTAL,
        "documents": documents,
        "lots": lots,
    }
    raw = json.dumps(manifest, indent=2).encode()
    (selective / "manifest.json").write_bytes(raw)
    pins = tuple(
        (lot["lot_id"], lot["sha256"], lot["bytes"], lot["page_count"]) for lot in lots
    )
    for name, value in (
        ("MANIFEST_SHA256", digest(raw)),
        ("LOT_PINS", pins),
        ("TOTAL_PAGES", TOTAL),
        ("REPO", repo),
        ("ROOT", selective),
    ):
        test.enterContext(patch.object(s, name, value))
    return manifest, selective, repo


def response_body(n: int, markdown=None, credits: object = "auto") -> bytes:
    """A complete synthetic Parse response for an n-page fragment."""
    markdown = markdown or (
        lambda i: f"Texto sintetico de prueba, pagina de fragmento {i}."
    )
    value = {
        "success": True,
        "data": {
            "markdown": "",
            "pages": [
                {"pageNumber": i, "markdown": markdown(i)} for i in range(1, n + 1)
            ],
            "blocks": [
                {"pageNumber": i, "status": "ok", "items": []} for i in range(1, n + 1)
            ],
            "metadata": {"numPages": n, "totalPages": n},
        },
    }
    if credits == "auto":
        value["data"]["metadata"]["creditsUsed"] = n
    elif credits is not None:
        value["data"]["metadata"]["creditsUsed"] = credits
    return json.dumps(value).encode()


class FakeResponse:
    """Minimal urllib response: context manager with status, headers and body."""

    def __init__(self, body: bytes, status: int = 200):
        self.body, self.status, self.headers = body, status, {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self.body


def ok_transport(calls: list, folder_root: Path, markdown=None):
    """Transport that records calls and asserts the ledger exists before HTTP."""

    def transport(request, timeout):
        name = request.data.split(b'filename="', 1)[1].split(b".pdf", 1)[0].decode()
        assert (folder_root / name / "attempt.json").is_file(), (
            "ledger must precede HTTP"
        )
        options = json.loads(
            request.data.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0]
        )
        calls.append((request, timeout, name, options))
        return FakeResponse(response_body(options["parsers"][0]["maxPages"], markdown))

    return transport

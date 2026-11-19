"""Offline public assembly contract; fixtures only, never network or Docker."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import assemble
from pilot import digest, write_json


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "evaluations/ocr"
        self.direct = self.base / ".artifacts/extraction-direct-v1"
        self.second = self.base / ".artifacts/second-attempt"
        self.direct.mkdir(parents=True)
        self.second.mkdir()
        self.sources = self.root / "documents/documentos_bienestar"
        self.sources.mkdir(parents=True)
        docs, old, blank = [], [], 37
        for i, count in enumerate((17, 2, 62, 75, 20, 16, 24)):
            source = self.sources / f"{i}.pdf"
            source.write_bytes(f"%PDF-fixture-{i}".encode())
            sha = digest(source.read_bytes())
            pages = []
            for n in range(1, count + 1):
                text = " \r\n" if blank else "é\r\n" * 50
                blank = max(0, blank - 1)
                pages.append(
                    {
                        "physical_page": n,
                        "text": text,
                        "charcount": len(text.strip()),
                        "method": "pdftotext",
                        "status": "pending_ocr"
                        if not text.strip()
                        else "direct_candidate",
                        "requires_review": True,
                    }
                )
            docs.append(
                {
                    "id": "sha256:" + sha,
                    "source_name": source.name,
                    "source_path": str(source.relative_to(self.root)),
                    "bytes": source.stat().st_size,
                    "bytes_after": source.stat().st_size,
                    "sha256_before": sha,
                    "sha256_after": sha,
                    "page_count": count,
                    "pages": pages,
                    "requires_review": True,
                }
            )
            old.append(
                {
                    "file": source.name,
                    "bytes": source.stat().st_size,
                    "pages": count,
                    "sha256_before": sha,
                    "sha256_after": sha,
                    "page_charcounts": [p["charcount"] for p in pages],
                }
            )
        self.docs = {
            "schema_version": 1,
            "documents": docs,
            "requires_review": True,
            "published": False,
            "totals": {
                "pages": 216,
                "pending_ocr": 37,
                "direct_candidate": 179,
                "sparse_review": 0,
            },
        }
        self.save(".artifacts/corpus-audit.json", {"documents": old})
        self.save(".artifacts/extraction-direct-v1/documents.json", self.docs)
        self.save(
            ".artifacts/extraction-direct-v1/manifest.json",
            {
                "schema_version": 1,
                "state": "complete_requires_review",
                "published": False,
                "document_count": 7,
                "documents_sha256": digest(
                    (self.direct / "documents.json").read_bytes()
                ),
                "prior_artifacts": {
                    str(self.base / ".artifacts/corpus-audit.json"): digest(
                        (self.base / ".artifacts/corpus-audit.json").read_bytes()
                    )
                },
                "totals": self.docs["totals"],
            },
        )
        self.report = self.base / "reports/pilot-02.md"
        self.report.parent.mkdir()
        self.report.write_text("review evidence")
        fragment = self.second / "pages-13-14.pdf"
        fragment.write_bytes(b"%PDF-fragment")
        self.save(
            ".artifacts/second-attempt/manifest.json",
            {
                "mapping": {"1": 13, "2": 14},
                "page_count": 2,
                "original_page_count": 17,
                "original": docs[0]["source_path"],
                "original_sha256": docs[0]["sha256_before"],
                "original_sha256_after": docs[0]["sha256_before"],
                "fragment_sha256": digest(fragment.read_bytes()),
                "fragment_bytes": fragment.stat().st_size,
            },
        )
        self.save(
            ".artifacts/second-attempt/attempt.json",
            {
                "mapping": {"1": 13, "2": 14},
                "fragment_sha256": digest(fragment.read_bytes()),
                "fragment_bytes": fragment.stat().st_size,
                "state": "reserved_may_have_been_sent",
                "options": {
                    "formats": ["markdown"],
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
                    "timeout": 300000,
                },
            },
        )
        self.save(
            ".artifacts/second-attempt/result.json",
            {"http_status": 200, "state": "response_saved_no_replay"},
        )
        self.raw = {
            "success": True,
            "data": {
                "markdown": "GLOBAL—not page offsets",
                "pages": [{"pageNumber": n, "markdown": f"OCR {n}"} for n in (1, 2)],
                "blocks": [
                    {
                        "pageNumber": n,
                        "width": 10,
                        "height": 20,
                        "status": "ok",
                        "items": [
                            {
                                "id": f"p{n}.b0",
                                "bbox": [0, 0, 1, 1],
                                "markdownSpan": [1920, 3671],
                                "readingOrder": 0,
                            }
                        ],
                    }
                    for n in (1, 2)
                ],
                "metadata": {"numPages": 2, "totalPages": 2},
            },
        }
        self.save(".artifacts/second-attempt/response.raw", self.raw)
        for name, value in {
            "BASE": self.base,
            "ROOT": self.root,
            "ORIGINAL": self.sources / "0.pdf",
            "ORIGINAL_SHA256": docs[0]["sha256_before"],
            "FRAGMENT_SHA256": digest(fragment.read_bytes()),
            "FRAGMENT_BYTES": fragment.stat().st_size,
            "DIRECT_SHA256": digest((self.direct / "documents.json").read_bytes()),
            "AUDIT_SHA256": digest(
                (self.base / ".artifacts/corpus-audit.json").read_bytes()
            ),
            "REPORT_SHA256": digest(self.report.read_bytes()),
        }.items():
            self.enterContext(patch.object(assemble, name, value))
        self.enterContext(patch.object(assemble.audit, "SOURCE", self.sources))

    def save(self, name, value):
        path = self.base / name
        path.write_text(json.dumps(value))

    def invoke(self):
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.object(sys, "argv", ["assemble.py"]),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = assemble.main()
        return code, out.getvalue(), err.getvalue()

    def test_mapping_preservation_manifest_and_no_overwrite(self):
        before = {p: p.read_bytes() for p in self.base.rglob("*") if p.is_file()}
        self.assertEqual(
            self.invoke(),
            (0, "Corpus assembled: 7 documents; 216 pages; publication blocked.\n", ""),
        )
        output = self.base / ".artifacts/extraction-corpus-v1"
        data = json.loads((output / "documents.json").read_bytes())
        manifest = json.loads((output / "manifest.json").read_bytes())
        self.assertEqual(
            data["totals"],
            {
                "documents": 7,
                "pages": 216,
                "direct_text": 179,
                "ocr_evidence": 2,
                "context_available": 181,
                "without_text": 35,
            },
        )
        for original, doc in zip(
            self.docs["documents"], data["documents"], strict=False
        ):
            self.assertEqual(
                doc["direct_document"],
                {k: v for k, v in original.items() if k != "pages"},
            )
            for old, page in zip(original["pages"], doc["pages"], strict=False):
                self.assertEqual(page["evidence"][0]["page"], old)
                self.assertTrue(page["requires_review"] and page["publication_blocked"])
        for n, physical in enumerate((13, 14)):
            page = data["documents"][0]["pages"][physical - 1]
            self.assertEqual(len(page["evidence"]), 2)
            self.assertEqual(page["evidence"][1]["page"], self.raw["data"]["pages"][n])
            self.assertEqual(
                page["evidence"][1]["blocks"], self.raw["data"]["blocks"][n]
            )
            self.assertEqual(len(page["issues"]), 2)
        self.assertTrue(data["requires_review"] and data["publication_blocked"])
        self.assertEqual(data["ocr_response_data"], self.raw["data"])
        self.assertEqual(
            manifest["inputs_sha256_before"], manifest["inputs_sha256_after"]
        )
        self.assertEqual(
            manifest["documents_sha256"],
            digest((output / "documents.json").read_bytes()),
        )
        self.assertEqual(manifest["state"], "complete_requires_review")
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        saved = {p: p.read_bytes() for p in output.iterdir()}
        self.assertEqual(
            self.invoke(), (1, "", "Assembly failed: existing_artifact.\n")
        )
        self.assertEqual(saved, {p: p.read_bytes() for p in saved})

    def rejected(self):
        before = {p: p.read_bytes() for p in self.base.rglob("*") if p.is_file()}
        self.assertEqual(self.invoke(), (1, "", "Assembly failed: invalid_input.\n"))
        self.assertFalse(
            (self.base / ".artifacts/extraction-corpus-v1/manifest.json").exists()
        )
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_hash(self):
        (self.direct / "documents.json").write_bytes(b"{}")
        self.rejected()

    def test_success_false(self):
        self.raw["success"] = False
        self.save(".artifacts/second-attempt/response.raw", self.raw)
        self.rejected()

    def test_page_count(self):
        self.raw["data"]["metadata"]["numPages"] = 3
        self.save(".artifacts/second-attempt/response.raw", self.raw)
        self.rejected()

    def test_duplicate(self):
        self.raw["data"]["pages"][1]["pageNumber"] = 1
        self.save(".artifacts/second-attempt/response.raw", self.raw)
        self.rejected()

    def test_incomplete(self):
        self.raw["data"]["blocks"].pop()
        self.save(".artifacts/second-attempt/response.raw", self.raw)
        self.rejected()

    def test_mapping_out_of_range(self):
        self.save(".artifacts/second-attempt/manifest.json", {"mapping": {"1": 999}})
        self.rejected()

    def test_empty_ocr(self):
        self.raw["data"]["pages"][1]["markdown"] = ""
        self.save(".artifacts/second-attempt/response.raw", self.raw)
        self.rejected()

    def test_path_traversal(self):
        self.docs["documents"][0]["source_path"] = "../outside.pdf"
        self.save(".artifacts/extraction-direct-v1/documents.json", self.docs)
        sha = digest((self.direct / "documents.json").read_bytes())
        manifest = json.loads((self.direct / "manifest.json").read_bytes())
        manifest["documents_sha256"] = sha
        self.save(".artifacts/extraction-direct-v1/manifest.json", manifest)
        with patch.object(assemble, "DIRECT_SHA256", sha):
            self.rejected()

    def test_atomic_completion_on_write_failure(self):
        def fail(path, value):
            if path.name == "manifest.record.json":
                path.write_bytes(b"{")
                raise OSError("PRIVATE")
            write_json(path, value)

        with patch.object(assemble, "write_json", side_effect=fail):
            self.assertEqual(
                self.invoke(), (1, "", "Assembly failed: local_io_error.\n")
            )
        self.assertFalse(
            (self.base / ".artifacts/extraction-corpus-v1/manifest.json").exists()
        )
        self.assertEqual(
            self.invoke(), (1, "", "Assembly failed: existing_artifact.\n")
        )

    def test_symlink(self):
        (self.direct / "manifest.json").rename(self.direct / "saved.json")
        (self.direct / "manifest.json").symlink_to(self.report)
        self.rejected()


if __name__ == "__main__":
    unittest.main()

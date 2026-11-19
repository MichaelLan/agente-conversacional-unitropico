"""Cache assembly with small synthetic fixtures, checked against the REAL ingestion parser.

The ingestion Worker parser is imported read-only from ``workers/ingestion/src``
by inserting that directory explicitly into ``sys.path`` (it only needs the
stdlib); nothing from the Worker is installed or modified.
"""

import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))
sys.path.insert(0, str(TESTS))
INGESTION_SRC = TESTS.parents[2] / "workers/ingestion/src"
sys.path.insert(0, str(INGESTION_SRC))  # Explicit: the real Worker parser.
from selective_fixtures import KEY, make_selective, ok_transport
from unitropico_ingestion.core.settings import Settings
from unitropico_ingestion.services.ingestion import ocr_key
from unitropico_ingestion.services.ocr_parser import parse_ocr_response

import assemble_cache as a
import selective_send as s


def fragment_markdown(i: int) -> str:
    return f"Contenido sintetico OCR nuevo del fragmento {i}."


class IngestionContractTests(unittest.TestCase):
    """Constants follow the Worker defaults and its key function."""

    def test_key_and_defaults_match_ingestion(self):
        settings = Settings()
        self.assertEqual(
            (a.OCR_VERSION, a.OCR_MAX_PAGES),
            (settings.ocr_version, settings.ocr_max_pages),
        )
        self.assertEqual(
            a.cache_key("ab" * 32),
            ocr_key("ab" * 32, settings.ocr_version, settings.ocr_max_pages),
        )
        wrangler = (INGESTION_SRC.parent / "wrangler.jsonc").read_text()
        self.assertIn(f'"OCR_VERSION": "{a.OCR_VERSION}"', wrangler)
        self.assertIn(f'"OCR_MAX_PAGES": "{a.OCR_MAX_PAGES}"', wrangler)
        self.assertIn(f'"bucket_name": "{a.BUCKET}"', wrangler)

    def test_parser_ignores_provenance_fields(self):
        doc = {"source_path": "docs/x.pdf", "source_sha256": "0" * 64}
        pages = [
            {"pageNumber": 1, "markdown": "uno", "source": "reused_pdftotext"},
            {"pageNumber": 2, "markdown": "dos", "source": "firecrawl_selective_v1"},
        ]
        raw = a.encode(a.cache_body(doc, pages, {"lots": []}))
        result = parse_ocr_response(raw)
        self.assertEqual(result.page_count, 2)
        self.assertEqual(
            [(x.page_number, x.markdown) for x in result.pages],
            [(1, "uno"), (2, "dos")],
        )
        a.check_with_ingestion_parser(raw, pages)


class AssembleTests(unittest.TestCase):
    """End-to-end offline: fake sender run → assembled cache objects."""

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.manifest, self.sel, self.repo = make_selective(self, self.root)
        self.enterContext(patch.object(a, "REPO", self.repo))
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        self.corpus = self.root / "corpus.json"
        self.write_corpus()
        self.output = self.root / "cache"

    def write_corpus(self, mutate=None):
        def pdftotext(n, text):
            return {"method": "pdftotext", "page": {"physical_page": n, "text": text}}

        docs = []
        for doc in self.manifest["documents"]:
            pages = []
            for n in range(1, doc["page_count"] + 1):
                evidence = [pdftotext(n, f"Texto nativo sintetico {doc['slug']} p{n}.")]
                if doc["slug"] == "doc-a" and n == 3:
                    evidence.append(
                        {
                            "method": "firecrawl_parse_ocr",
                            "page": {
                                "pageNumber": 3,
                                "markdown": "Markdown previo p3.",
                            },
                        }
                    )
                pages.append({"physical_page": n, "evidence": evidence})
            docs.append(
                {
                    "direct_document": {
                        "source_path": doc["source_path"],
                        "sha256_before": doc["source_sha256"],
                        "sha256_after": doc["source_sha256"],
                        "page_count": doc["page_count"],
                    },
                    "pages": pages,
                }
            )
        value = {"documents": docs}
        if mutate:
            mutate(value)
        raw = json.dumps(value).encode()
        self.corpus.write_bytes(raw)
        self.enterContext(patch.object(a, "CORPUS_SHA256", a.digest(raw)))

    def run_sender(self, markdown=fragment_markdown):
        with contextlib.redirect_stdout(io.StringIO()):
            return s.send(
                KEY, ok_transport([], self.sel / s.EXECUTION_NAME, markdown), self.sel
            )

    def assemble(self):
        return a.assemble(self.sel, self.corpus, self.output, self.root)

    def test_assembles_all_documents_with_physical_mapping_and_provenance(self):
        self.assertEqual(self.run_sender(), 0)
        entries = self.assemble()
        self.assertEqual(len(entries), 3)
        by_slug = {d["slug"]: d for d in self.manifest["documents"]}
        entry = next(
            e for e in entries if e["source_path"] == by_slug["doc-a"]["source_path"]
        )
        self.assertEqual(
            entry["key"],
            f"ocr/{by_slug['doc-a']['source_sha256']}/firecrawl-parse-v2-ocr-v1-p1000.json",
        )
        raw = (self.output / entry["key"]).read_bytes()
        result = parse_ocr_response(raw)  # real ingestion parser
        self.assertEqual(result.page_count, 4)
        self.assertEqual(result.failed_pages, ())
        markdown = {x.page_number: x.markdown for x in result.pages}
        self.assertEqual(markdown[1], "Texto nativo sintetico doc-a p1.")
        self.assertEqual(markdown[2], fragment_markdown(1))  # fragment 1 → physical 2
        self.assertEqual(markdown[3], "Markdown previo p3.")
        self.assertEqual(markdown[4], fragment_markdown(2))  # fragment 2 → physical 4
        body = json.loads(raw)
        sources = {x["pageNumber"]: x["source"] for x in body["data"]["pages"]}
        self.assertEqual(
            sources,
            {
                1: "reused_pdftotext",
                2: "firecrawl_selective_v1",
                3: "reused_firecrawl",
                4: "firecrawl_selective_v1",
            },
        )
        self.assertTrue(
            all(
                set(x) == {"pageNumber", "markdown", "source"}
                for x in body["data"]["pages"]
            )
        )
        self.assertEqual(
            body["data"]["provenance"]["page_sources"]["firecrawl_selective_v1"], [2, 4]
        )
        self.assertEqual(
            entry["pages_by_source"],
            {"reused_pdftotext": 1, "reused_firecrawl": 1, "firecrawl_selective_v1": 2},
        )
        reused_only = next(
            e for e in entries if e["source_path"] == by_slug["doc-b"]["source_path"]
        )
        self.assertEqual(reused_only["pages_by_source"]["reused_pdftotext"], 2)
        plan = (self.output / "upload-plan.md").read_text()
        self.assertIn(
            f"npx wrangler r2 object put unitropico-documents/{entry['key']} --file=",
            plan,
        )
        self.assertIn("--remote", plan)
        self.assertIn("raw/{sha256}.pdf", plan)
        self.assertEqual(len(re.findall(r"^npx wrangler", plan, re.MULTILINE)), 3)
        record = json.loads((self.output / "assembly.json").read_text())
        self.assertEqual(record["state"], "assembled_offline_not_uploaded")
        with self.assertRaises(FileExistsError):
            self.assemble()  # output is never reused

    def test_incomplete_lots_block_assembly(self):
        with self.assertRaisesRegex(ValueError, "Lot not complete"):
            self.assemble()
        self.assertFalse(self.output.exists())

    def test_new_page_without_markdown_fails(self):
        self.assertEqual(self.run_sender(lambda i: "   " if i == 2 else "texto"), 0)
        with self.assertRaisesRegex(ValueError, "Page without markdown"):
            self.assemble()
        self.assertFalse(self.output.exists())

    def test_missing_physical_page_fails(self):
        self.assertEqual(self.run_sender(), 0)
        self.write_corpus(lambda v: v["documents"][1]["pages"].pop())
        with self.assertRaisesRegex(ValueError, "Missing reused page"):
            self.assemble()

    def test_empty_reused_page_fails(self):
        self.assertEqual(self.run_sender(), 0)
        self.write_corpus(
            lambda v: v["documents"][0]["pages"][0]["evidence"][0]["page"].update(
                text=""
            )
        )
        with self.assertRaisesRegex(ValueError, "Page without markdown"):
            self.assemble()

    def test_page_outside_reuse_and_selection_fails(self):
        doc = {
            "source_path": "x.pdf",
            "page_count": 2,
            "reuse_pages": [1],
            "selected_pages": [],
        }
        corpus_doc = {
            "pages": [
                {
                    "physical_page": n,
                    "evidence": [{"method": "pdftotext", "page": {"text": "t"}}],
                }
                for n in (1, 2)
            ]
        }
        with self.assertRaisesRegex(ValueError, "not covered"):
            a.document_pages(doc, corpus_doc, {})

    def test_changed_corpus_is_rejected(self):
        self.assertEqual(self.run_sender(), 0)
        self.corpus.write_bytes(self.corpus.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "Corpus identity"):
            self.assemble()


if __name__ == "__main__":
    unittest.main()

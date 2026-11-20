"""Final cache assembly and correction validation, offline, with the REAL ingestion code.

The ingestion Worker code is imported read-only from ``workers/ingestion/src``
by inserting that directory explicitly into ``sys.path`` (stdlib only).
Synthetic fixtures cover every source type; an extra test runs the real
assembly in check mode when the local ``.artifacts`` inputs exist.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))
INGESTION_SRC = TESTS.parents[2] / "workers/ingestion/src"
sys.path.insert(0, str(INGESTION_SRC))  # Explicit: the real Worker code.
from unitropico_ingestion.core.settings import Settings
from unitropico_ingestion.services.ingestion import ocr_key

import assemble_final_cache as afc
import final_corrections as fc
import final_override as fo
import final_sources as fs

API = fc.ingestion()
PATH = "documents/x/doc.pdf"
SHA = "ab" * 32
HEADER = "ENCABEZADO INSTITUCIONAL REPETIDO"
BAD = "ENCABEZAD0 INSTITUCIONAL REPETIDO"  # OCR error repeated on pages 2-5


def plan_item(n, source, corrections=0):
    return {
        "document": "doc",
        "source_pdf": PATH,
        "physical_page": n,
        "source": source,
        "corrections_required": corrections,
        "corrections_ref": None,
    }


def write(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = value if isinstance(value, bytes) else json.dumps(value).encode()
    path.write_bytes(data)
    return path


def fixtures(root: Path) -> fs.SourceStore:
    """One page per source type (pins disabled; paths under the repo)."""
    corpus = {
        "documents": [
            {
                "direct_document": {"source_path": PATH},
                "pages": [
                    {
                        "physical_page": 1,
                        "evidence": [
                            {"method": "pdftotext", "page": {"text": f"{HEADER}\nUno."}}
                        ],
                    },
                    {
                        "physical_page": 2,
                        "evidence": [
                            {
                                "method": "firecrawl_parse_ocr",
                                "page": {"markdown": f"{BAD}\nDos previo."},
                            },
                            {"method": "pdftotext", "page": {"text": "ignorado"}},
                        ],
                    },
                ],
            }
        ]
    }
    write(root / "corpus.json", corpus)
    lot = {
        "lot_id": "01-doc",
        "source_path": PATH,
        "page_count": 1,
        "sha256": "cd" * 32,
        "mapping": {"1": 3},
    }
    write(root / "sel/manifest.json", {"lots": [lot]})
    folder = root / "sel/execution/01-doc"
    write(folder / "result.json", {"state": "complete"})
    write(folder / "attempt.json", {"fragment_sha256": "cd" * 32})
    response = {
        "success": True,
        "data": {
            "pages": [{"pageNumber": 1, "markdown": f"{BAD}\nTres firecrawl."}],
            "blocks": [{"pageNumber": 1, "status": "ok", "items": []}],
            "metadata": {"numPages": 1},
        },
    }
    write(folder / "response.raw", response)
    table = (
        "<table>\n<caption>Tabla 1</caption>\n<tr><th>Línea</th><th>Valor</th></tr>\n"
        "<tr><td>IES4</td><td>$400.000</td></tr>\n</table>"
    )
    write(
        root / "pilot.json",
        {
            "pages": [
                {
                    "document": "doc",
                    "pageNumber": 4,
                    "page_id": "p4",
                    "markdown": f"{BAD}\nCuatro piloto argo plaza.",
                }
            ]
        },
    )
    write(
        root / "v2.json",
        {
            "pages": [
                {
                    "document": "doc",
                    "pageNumber": 5,
                    "page_id": "p5",
                    "markdown": f"{BAD}\nCinco azure.\n\n{table}",
                }
            ]
        },
    )
    return fs.SourceStore(
        root / "corpus.json", root / "sel", root / "pilot.json", root / "v2.json", False
    )


PLAN_SOURCES = (
    "reusable_pdftotext",
    "reusable_firecrawl_prev",
    "firecrawl_v1_lots01_03",
    "azure_pilot_v1",
    "azure_v2",
)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TESTS.parent)
        self.root = Path(self.tmp.name)
        self.store = fixtures(self.root)
        self.plan = {
            PATH: [plan_item(n, s) for n, s in enumerate(PLAN_SOURCES, 1)],
        }

    def tearDown(self):
        self.tmp.cleanup()

    def docs(self):
        return fs.build_documents(self.plan, self.store, {PATH: SHA})


class IngestionContractTests(unittest.TestCase):
    def test_key_matches_ingestion_defaults(self):
        settings = Settings()
        self.assertEqual(
            afc.cache_key(SHA),
            ocr_key(SHA, settings.ocr_version, settings.ocr_max_pages),
        )
        wrangler = (INGESTION_SRC.parent / "wrangler.jsonc").read_text()
        self.assertIn(f'"OCR_VERSION": "{settings.ocr_version}"', wrangler)
        self.assertIn(f'"OCR_MAX_PAGES": "{settings.ocr_max_pages}"', wrangler)


class AssemblyTests(Base):
    def test_every_page_from_its_source_with_provenance(self):
        (doc,) = self.docs()
        self.assertEqual([p.number for p in doc.pages], [1, 2, 3, 4, 5])
        self.assertEqual([p.source for p in doc.pages], list(PLAN_SOURCES))
        self.assertIn("Dos previo.", doc.pages[1].markdown)  # Firecrawl preferred
        body = afc.cache_body(doc, {"audit": "0" * 64})
        prov = body["data"]["provenance"]
        self.assertFalse(prov["corrections_applied"])
        self.assertEqual(len(prov["pages"]), 5)
        self.assertTrue(all(len(p["artifact_sha256"]) == 64 for p in prov["pages"]))
        self.assertEqual(prov["page_sources"]["azure_v2"], [5])
        self.assertEqual(
            [p["source"] for p in body["data"]["pages"]], list(PLAN_SOURCES)
        )

    def test_real_parser_round_trip(self):
        (doc,) = self.docs()
        raw = afc.encode(afc.cache_body(doc, {}))
        afc.check_parser(raw, doc, API)
        result = API["parse_ocr_response"](raw)
        self.assertEqual(result.page_count, 5)
        self.assertNotIn("argo plaza.", result.pages[0].markdown)

    def test_missing_page_fails(self):
        del self.plan[PATH][2]
        with self.assertRaises(ValueError):
            fs.load_plan({"final_source_plan": self.plan[PATH]})

    def test_empty_page_fails(self):
        write(
            self.root / "v2.json",
            {
                "pages": [
                    {
                        "document": "doc",
                        "pageNumber": 5,
                        "page_id": "p5",
                        "markdown": "  ",
                    }
                ]
            },
        )
        self.store = fs.SourceStore(
            self.root / "corpus.json",
            self.root / "sel",
            self.root / "pilot.json",
            self.root / "v2.json",
            False,
        )
        with self.assertRaisesRegex(ValueError, "Page without markdown"):
            self.docs()

    def test_incomplete_lot_page_is_missing(self):
        write(self.root / "sel/execution/01-doc/result.json", {"state": "running"})
        with self.assertRaisesRegex(ValueError, "Selective page missing"):
            self.docs()

    def test_pins_enforced(self):
        store = fs.SourceStore(
            self.root / "corpus.json",
            self.root / "sel",
            self.root / "pilot.json",
            self.root / "v2.json",
            True,
        )
        with self.assertRaisesRegex(ValueError, "Identity mismatch"):
            fs.build_documents(self.plan, store, {PATH: SHA})

    def test_upload_plan_lists_unexecuted_commands(self):
        text = afc.upload_plan(
            [{"source_path": PATH, "page_count": 5, "key": afc.cache_key(SHA)}],
            self.root,
        )
        self.assertIn("NO ejecutado", text)
        self.assertIn(
            f"npx wrangler r2 object put unitropico-documents/{afc.cache_key(SHA)}",
            text,
        )
        self.assertIn("--remote", text)


def corr(page, find, replace, kind="substantive"):
    return fs.Correction("doc", page, find, replace, "motivo", "audit", "x", kind)


class ValidationTests(Base):
    def test_verbatim_reformulated_and_omitted(self):
        (doc,) = self.docs()
        corrections = list(
            enumerate(
                [
                    corr(4, "argo plaza", "largo plazo"),  # verbatim
                    corr(5, "<td>IES4</td>", "<td>IES⁴</td>"),  # HTML: reformulate
                    # Repeated running header: removed by the chunker on pages
                    # 3-5 (first kept on p2), and the corrected line is also
                    # repeated (p1, p3, p4): no visible effect -> omitted.
                    corr(3, BAD, HEADER),
                    corr(4, BAD, HEADER),
                ]
            )
        )
        seeds, outcomes, pages = fc.validate_document(doc, corrections, API)
        status = {o.index: o.status for o in outcomes}
        self.assertEqual(
            status,
            {0: "verbatim", 1: "reformulated", 2: "omitted", 3: "omitted"},
        )
        reformulated = [s for s in seeds if s.status == "reformulated"]
        self.assertEqual(len(reformulated), 1)
        self.assertEqual(reformulated[0].reformulated_from, [1])
        self.assertIn("IES⁴", pages[5])
        for seed in seeds:  # every seed matches exactly once where it is applied
            self.assertLessEqual(len(seed.find_text.strip()), fc.MAX_FIND)
        fc.check_seeds(seeds, API)
        raw = afc.encode(afc.cache_body(doc, {}))
        result = fc.final_check(doc, seeds, raw, API, expected_pages=pages)
        self.assertEqual(result["corrections_applied"], 2)
        self.assertGreater(result["chunks"], 0)
        self.assertIn("largo plazo", result["corrected_pages"][4])

    def test_long_edit_is_split_within_limits(self):
        long_cell = " ".join(f"palabra{i}" for i in range(150))
        current = "Texto previo.\n\n| A | B |\n| --- | --- |\n| x | y |\n\nFin."
        target = (
            f"Texto previo.\n\n| A | B |\n| --- | --- |\n| x {long_cell} | y |\n\nFin."
        )
        edits = fc.reformulate(current, target, 1, API)
        self.assertGreater(len(edits), 1)
        self.assertTrue(all(len(r) <= fc.MAX_REPLACE for _, r in edits))
        self.assertEqual(
            fc.tidy(fc.apply_one_all(current, 1, edits, API)), fc.tidy(target)
        )

    def test_check_seeds_rejects_duplicates_and_limits(self):
        seed = fc.Seed("doc", 1, "a", "b", "r", "o", "verbatim")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            fc.check_seeds([seed, seed], API)
        with self.assertRaisesRegex(ValueError, "find_text length"):
            fc.check_seeds([fc.Seed("doc", 1, "x" * 501, "", "r", "o", "v")], API)
        with self.assertRaisesRegex(ValueError, "Cf"):
            fc.check_seeds([fc.Seed("doc", 1, "a​b", "c", "r", "o", "v")], API)


class OverrideTests(unittest.TestCase):
    def test_override_replaces_block_and_keeps_facts(self):
        page = (
            "<figure>\n\nProsperidad Social\n\n</figure>\n\n"
            f"{fo.BLOCK_START}\nmediano y\nargo plaza\n\n{fo.BLOCK_END_AFTER}\n\n"
            "# 5.5.3. Teoría del cambio\n\nResto."
        )
        text = fo.override_text(page)
        self.assertIn("Insumos → Actividades", text)
        self.assertIn("# 5.5.3. Teoría del cambio", text)
        self.assertNotIn("argo plaza", text)
        for fact in fo.KEY_FACTS:
            self.assertIn(fact, text)
        prepared = API["chunker"].prepare([API["PageText"](54, text)])
        self.assertIn("BPIN 202300000000204", prepared[0].text)

    def test_missing_block_fails(self):
        with self.assertRaises(ValueError):
            fo.override_text("sin diagrama")


@unittest.skipUnless(
    fs.CORPUS.exists() and fs.V2_EXPORT.exists() and fs.PILOT_EXPORT.exists(),
    "local .artifacts inputs not available",
)
class RealInputsTests(unittest.TestCase):
    """Check mode on the real local inputs (no writes)."""

    @classmethod
    def setUpClass(cls):
        cls.state = afc.build(check_files=False)

    def test_totals(self):
        self.assertEqual(len(self.state["corrections"]), 120)
        statuses = [o.status for o in self.state["outcomes"]]
        self.assertEqual(len(statuses), 120)
        self.assertNotIn("pending", statuses)
        self.assertEqual(sum(len(d.pages) for d, _, _ in self.state["documents"]), 216)
        self.assertEqual(len(self.state["documents"]), 7)

    def test_every_document_chunks(self):
        for check in self.state["checks"].values():
            self.assertGreater(check["chunks"], 0)

    def test_payloads(self):
        corrections, overrides, validation = afc.seed_payloads(self.state)
        self.assertEqual(len(corrections["documents"]), 7)
        (override,) = overrides["overrides"]
        self.assertTrue(override["requires_human_verification"])
        self.assertEqual(override["page_number"], 54)
        self.assertEqual(len(validation["outcomes"]), 120)
        seqs = [c["seq"] for d in corrections["documents"] for c in d["corrections"]]
        self.assertEqual(len(seqs), corrections["totals"]["seeds"])


if __name__ == "__main__":
    unittest.main()

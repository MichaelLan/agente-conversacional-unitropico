"""Selection, lot planning and fragment checks; no Docker (subprocess is faked)."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_selective as p

EXPECTED = {
    "res-cbu-002-2025": list(range(1, 17)),
    "boletin-renta-joven": [],
    "documento-tecnico": [
        5,
        16,
        17,
        18,
        20,
        21,
        22,
        30,
        32,
        44,
        45,
        46,
        54,
        57,
        58,
        59,
        61,
    ],
    "manual-m-ip-36": [
        14,
        18,
        19,
        20,
        23,
        27,
        28,
        29,
        35,
        36,
        38,
        40,
        41,
        47,
        52,
        61,
        62,
        63,
        64,
        65,
        67,
        68,
        74,
        75,
    ],
    "res-00137": [1, 2, 3, 4, 5, 9, 11, 13, 14, 17, 18, 19],
    "convocatoria-2026-1": list(range(1, 18)),
    "politica-bienestar": [
        1,
        2,
        3,
        4,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
        20,
        22,
        23,
        24,
    ],
}


def audit() -> dict:
    """The committed visual audit report (reports/, not .artifacts/)."""
    return json.loads(p.AUDIT.read_bytes())


class SelectionTests(unittest.TestCase):
    """Exactly the 106 authorized physical pages come from the audit JSON."""

    def test_audit_report_matches_pinned_identity(self):
        self.assertEqual(p.digest(p.AUDIT.read_bytes()), p.AUDIT_SHA256)

    def test_selects_exact_106_pages(self):
        selection = p.select_pages(audit())
        self.assertEqual({d["slug"]: d["selected_pages"] for d in selection}, EXPECTED)
        self.assertEqual(sum(len(d["selected_pages"]) for d in selection), 106)
        self.assertEqual(sum(len(d["reuse_pages"]) for d in selection), 110)
        self.assertEqual(sum(len(d["literal_patch_pages"]) for d in selection), 20)

    def test_reclassified_page_breaks_the_106_total(self):
        value = audit()
        doc = next(d for d in value["documents"] if d["firecrawl_ocr_pages"])
        page = doc["firecrawl_ocr_pages"].pop()
        doc["reuse_pages"].append(page)
        next(x for x in doc["pages"] if x["physical_page"] == page)[
            "classification"
        ] = "ok"
        with self.assertRaisesRegex(ValueError, "106"):
            p.select_pages(value)

    def test_inconsistent_audit_is_rejected(self):
        for mutate in (
            lambda d: d["reuse_pages"].append(d["firecrawl_ocr_pages"][0]),
            lambda d: d["firecrawl_ocr_pages"].append(d["firecrawl_ocr_pages"][0]),
            lambda d: d["pages"][0].update(classification="ok"),
            lambda d: d.update(page_count=d["page_count"] + 1),
        ):
            value = copy.deepcopy(audit())
            mutate(value["documents"][0])
            with self.assertRaises(ValueError):
                p.select_pages(value)

    def test_unknown_document_is_rejected(self):
        value = audit()
        value["documents"][0]["source_path"] = "documents/other.pdf"
        with self.assertRaises(ValueError):
            p.select_pages(value)


class LotTests(unittest.TestCase):
    """Lots of at most 20 pages with fragment→physical mapping."""

    def test_real_lot_plan(self):
        lots = p.plan_lots(p.select_pages(audit()))
        self.assertEqual(
            [lot["page_count"] for lot in lots], [16, 17, 12, 12, 12, 17, 20]
        )
        manual = [lot for lot in lots if lot["slug"] == "manual-m-ip-36"]
        self.assertEqual(
            [lot["lot_id"] for lot in manual],
            ["03-manual-m-ip-36-1of2", "04-manual-m-ip-36-2of2"],
        )
        self.assertEqual(
            manual[0]["physical_pages"] + manual[1]["physical_pages"],
            EXPECTED["manual-m-ip-36"],
        )
        self.assertEqual(manual[1]["mapping"]["1"], 41)
        self.assertEqual(manual[1]["mapping"]["2"], 47)
        for lot in lots:
            self.assertEqual(list(lot["mapping"].values()), lot["physical_pages"])
            self.assertEqual(
                list(lot["mapping"]), [str(i) for i in range(1, lot["page_count"] + 1)]
            )
            self.assertEqual(lot["file"], f"lots/{lot['lot_id']}.pdf")

    def test_large_document_is_split_into_balanced_lots(self):
        selection = [
            {
                "slug": "big",
                "source_path": "x.pdf",
                "selected_pages": list(range(1, 107)),
            }
        ]
        lots = p.plan_lots(selection)
        self.assertEqual([lot["page_count"] for lot in lots], [18, 18, 18, 18, 18, 16])
        self.assertEqual(lots[-1]["mapping"]["1"], 91)

    def test_options_match_pilots(self):
        self.assertEqual(
            p.lot_options(7),
            {
                "formats": ["markdown"],
                "timeout": 300000,
                "parsers": [
                    {
                        "type": "pdf",
                        "mode": "ocr",
                        "maxPages": 7,
                        "pages": True,
                        "blocks": True,
                        "pageMarkers": True,
                    }
                ],
            },
        )


class FingerprintTests(unittest.TestCase):
    """Each fragment page must match its physical page (text, render, geometry)."""

    LOT = {"mapping": {"1": 13, "2": 14}}

    def report(self, frag_text="aa", frag_render="rr", frag_size="612 x 792 pts"):
        lines = []
        for i, n in (("1", 13), ("2", 14)):
            lines += [
                f"O {n} aa{n}  -",
                f"F {i} {frag_text}{n}  -",
                f"I {n} rr{n}  -",
                f"J {i} {frag_render}{n}  -",
            ]
            lines += [f"S Page   {n} size: 612 x 792 pts", f"S Page   {n} rot:  0"]
            lines += [f"G Page   {i} size: {frag_size}", f"G Page   {i} rot:  0"]
        return "\n".join(lines)

    def test_matching_pages_are_recorded(self):
        pages = p.compare_fingerprints(self.report(), self.LOT)
        self.assertEqual(
            [(x["fragment_page"], x["physical_page"]) for x in pages],
            [(1, 13), (2, 14)],
        )
        self.assertEqual(pages[1]["render_sha256"], "rr14")

    def test_mismatches_are_rejected(self):
        for report in (
            self.report(frag_text="zz"),
            self.report(frag_render="zz"),
            self.report(frag_size="595 x 842 pts"),
            "\n".join(x for x in self.report().splitlines() if not x.startswith("J 2")),
        ):
            with self.assertRaises(ValueError):
                p.compare_fingerprints(report, self.LOT)


class PrepareGuardTests(unittest.TestCase):
    """Isolation of the Poppler call and fail-closed preparation."""

    def test_cut_lot_uses_isolated_readonly_docker(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        lot = {"lot_id": "01-x", "physical_pages": [3, 9], "file": "lots/01-x.pdf"}
        with patch("subprocess.run") as run:
            p.cut_lot(root / "source.pdf", root, lot)
        args, kwargs = run.call_args
        command = args[0]
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertIn("--read-only", command)
        self.assertIn(
            f"type=bind,src={root / 'source.pdf'},dst=/source.pdf,readonly", command
        )
        self.assertIn("pdfseparate -f 9 -l 9 /source.pdf", command[-1])
        self.assertIn(
            "pdfunite /out/pages/01-x/page-3.pdf /out/pages/01-x/page-9.pdf /out/lots/01-x.pdf",
            command[-1],
        )
        self.assertEqual(kwargs["timeout"], p.POPPLER_TIMEOUT)

    def test_changed_audit_stops_before_output(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        fake = root / "audit.json"
        fake.write_text("{}")
        with (
            patch.object(p, "AUDIT", fake),
            patch("subprocess.run", side_effect=AssertionError),
        ):
            with self.assertRaisesRegex(ValueError, "Audit identity"):
                p.prepare(root / "out", root)
        self.assertFalse((root / "out").exists())

    def test_existing_output_is_never_reused(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / "out").mkdir()
        with (
            patch("subprocess.run", side_effect=AssertionError),
            self.assertRaises(FileExistsError),
        ):
            p.prepare(root / "out", root)


if __name__ == "__main__":
    unittest.main()

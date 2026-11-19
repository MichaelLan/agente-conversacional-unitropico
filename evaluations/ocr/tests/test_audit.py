"""Offline public CLI checks; no PDF text leaves the temporary fixtures."""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "pdfs"
        self.source.mkdir()
        for index in range(7):
            (self.source / f"{index}.pdf").write_bytes(b"%PDF-fixture")
        self.output = self.root / "audit.json"

    def invoke(self, text="a" * 80 + "\f", pages=1, error=None, mutate=False):
        def run(args, **kwargs):
            self.assertEqual(kwargs["timeout"], 60)
            self.assertTrue(kwargs["check"])
            self.assertTrue(kwargs["capture_output"])
            for flag, value in (
                ("--pull", "never"),
                ("--network", "none"),
                ("--cap-drop", "ALL"),
                ("--security-opt", "no-new-privileges"),
            ):
                self.assertEqual(args[args.index(flag) + 1], value)
            self.assertIn("--read-only", args)
            mount = args[args.index("--mount") + 1]
            self.assertTrue(mount.endswith(",dst=/source.pdf,readonly"))
            self.assertIn(audit.IMAGE, args)
            if error:
                raise error
            if "pdfinfo" in args:
                return Mock(stdout=f"Pages: {pages}\n")
            self.assertEqual(
                args[-5:], ["pdftotext", "-enc", "UTF-8", "/source.pdf", "-"]
            )
            if mutate:
                (self.source / "0.pdf").write_bytes(b"%PDF-changed")
            return Mock(stdout=text.encode("utf-8") if isinstance(text, str) else text)

        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch.object(audit, "SOURCE", self.source),
            patch.object(audit, "OUTPUT", self.output),
            patch.object(sys, "argv", ["audit.py"]),
            patch.object(subprocess, "run", side_effect=run) as runner,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = audit.main()
        return code, stdout.getvalue(), stderr.getvalue(), runner.call_count

    def test_unicode_thresholds_and_real_blank_pages(self):
        text = "\f \u2003\f" + "é" * 79 + "\f" + "界" * 80 + "\f\f"
        code, out, err, calls = self.invoke(text, 5)
        self.assertEqual(
            (code, out, err, calls), (0, "Audit complete: 7 PDFs.\n", "", 14)
        )
        result = json.loads(self.output.read_text())
        self.assertEqual(
            result["totals"],
            {"pages": 35, "ocr_candidate": 21, "review": 7, "direct_candidate": 7},
        )
        self.assertEqual(len(result["documents"]), 7)
        for doc in result["documents"]:
            self.assertEqual(doc["page_charcounts"], [0, 0, 79, 80, 0])
            self.assertEqual(doc["sha256_before"], doc["sha256_after"])
            self.assertEqual(doc["bytes"], len(b"%PDF-fixture"))
            self.assertEqual(doc["pages"], 5)
        self.assertNotIn("é" * 79, self.output.read_text())
        self.assertEqual((self.source / "0.pdf").read_bytes(), b"%PDF-fixture")

    def test_one_character_and_preserved_default(self):
        for text, expected in [("x\f", 1), ("a" * 80 + "\f", 80)]:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as td:
                self.output = Path(td) / "audit.json"
                code, out, err, calls = self.invoke(text)
                self.assertEqual(
                    (code, out, err, calls), (0, "Audit complete: 7 PDFs.\n", "", 14)
                )
                result = json.loads(self.output.read_text())
                self.assertEqual(result["documents"][0]["page_charcounts"], [expected])

    def test_invalid_boundaries_counts_and_encoding_leave_no_output(self):
        for text, pages in [
            ("text", 1),
            ("x\f\f", 1),
            ("x\f", 2),
            ("", 1),
            ("\f", 0),
            (b"\xff\f", 1),
        ]:
            with self.subTest(text=repr(text), pages=pages):
                code, out, err, _ = self.invoke(text, pages)
                self.assertEqual(
                    (code, out, err), (1, "", "Audit failed: invalid_input.\n")
                )
                self.assertFalse(self.output.exists())
                self.assertEqual((self.source / "0.pdf").read_bytes(), b"%PDF-fixture")

    def test_errors_safe_without_retry_or_partial_success(self):
        for error, category in [
            (subprocess.TimeoutExpired("PRIVATE", 60), "poppler_timeout"),
            (subprocess.CalledProcessError(1, "PRIVATE"), "poppler_error"),
            (OSError("PRIVATE"), "local_io_error"),
        ]:
            with self.subTest(category=category):
                code, out, err, calls = self.invoke(error=error)
                self.assertEqual(
                    (code, out, err, calls), (1, "", f"Audit failed: {category}.\n", 1)
                )
                self.assertFalse(self.output.exists())
                self.assertEqual((self.source / "0.pdf").read_bytes(), b"%PDF-fixture")

    def test_integrity_change_refused(self):
        code, out, err, _ = self.invoke(mutate=True)
        self.assertEqual((code, out, err), (1, "", "Audit failed: invalid_input.\n"))
        self.assertFalse(self.output.exists())

    def test_no_overwrite_and_exact_corpus(self):
        self.output.write_bytes(b"prior evidence")
        self.assertEqual(
            self.invoke()[:3], (1, "", "Audit failed: existing_artifact.\n")
        )
        self.assertEqual(self.invoke()[3], 0)
        self.assertEqual(self.output.read_bytes(), b"prior evidence")
        self.output = self.root / "other.json"
        (self.source / "extra.pdf").write_bytes(b"%PDF-extra")
        self.assertEqual(self.invoke()[:3], (1, "", "Audit failed: invalid_input.\n"))
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()

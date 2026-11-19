"""Offline guards of the selective sender; fake PDFs, fake key, fake transport."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from selective_fixtures import (
    KEY,
    FakeResponse,
    make_selective,
    ok_transport,
    response_body,
)

import selective_send as s


class RealPinsTests(unittest.TestCase):
    """The shipped constants describe exactly the authorized 106 pages."""

    def test_pins_total_106_pages_in_lots_of_at_most_20(self):
        pages = [pin[3] for pin in s.LOT_PINS]
        self.assertEqual(sum(pages), 106)
        self.assertEqual(s.TOTAL_PAGES, 106)
        self.assertTrue(all(0 < n <= 20 for n in pages))
        self.assertRegex(s.MANIFEST_SHA256, r"^[0-9a-f]{64}$")
        self.assertTrue(
            all(len(pin[1]) == 64 and pin[2] < 50_000_000 for pin in s.LOT_PINS)
        )
        self.assertEqual(s.ENDPOINT, "https://api.firecrawl.dev/v2/parse")


class SenderTests(unittest.TestCase):
    """Lot-by-lot sending with durable ledgers and no replay."""

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.manifest, self.sel, self.repo = make_selective(self, self.root)
        self.execution = self.sel / s.EXECUTION_NAME
        self.logs = io.StringIO()
        self.enterContext(contextlib.redirect_stderr(self.logs))
        self.enterContext(
            patch("subprocess.run", side_effect=AssertionError("No Docker in send"))
        )
        self.calls = []

    def send(self, transport=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            return s.send(
                KEY, transport or ok_transport(self.calls, self.execution), self.sel
            )

    def status(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(s.status(self.sel), 0)
        return out.getvalue()

    def test_complete_run_ledgers_options_and_no_second_post(self):
        self.assertEqual(self.send(), 0)
        self.assertEqual([c[2] for c in self.calls], ["01-doc-a", "02-doc-c"])
        request, timeout, _, options = self.calls[0]
        self.assertEqual(request.full_url, "https://api.firecrawl.dev/v2/parse")
        self.assertEqual(timeout, 330)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + KEY)
        parser = options["parsers"][0]
        self.assertEqual(
            parser,
            {
                "type": "pdf",
                "mode": "ocr",
                "maxPages": 2,
                "pages": True,
                "blocks": True,
                "pageMarkers": True,
            },
        )
        self.assertEqual(options["timeout"], 300000)
        self.assertIn(b"%PDF-fragment-01-doc-a", request.data)
        self.assertNotIn(b"%PDF-source", request.data)
        attempt = json.loads((self.execution / "01-doc-a/attempt.json").read_text())
        self.assertEqual(attempt["mapping"], {"1": 2, "2": 4})
        self.assertEqual(attempt["state"], "reserved_may_have_been_sent")
        result = json.loads((self.execution / "01-doc-a/result.json").read_text())
        self.assertEqual((result["state"], result["credits_declared"]), ("complete", 2))
        self.assertTrue((self.execution / "02-doc-c/response.raw").is_file())
        self.assertNotIn(KEY, self.logs.getvalue())
        # Every lot complete: a new run skips them all and never posts.
        self.calls.clear()
        self.assertEqual(self.send(), 0)
        self.assertEqual(self.calls, [])
        report = self.status()
        self.assertIn("2 complete, 0 uncertain", report)
        self.assertIn("Declared credits (metadata.creditsUsed): 3", report)

    def test_altered_fragment_identity_is_refused_before_ledger(self):
        (self.sel / "lots/01-doc-a.pdf").write_bytes(b"%PDF-tampered")
        self.assertEqual(self.send(), 1)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.execution.exists())

    def test_altered_manifest_is_refused(self):
        path = self.sel / "manifest.json"
        path.write_bytes(path.read_bytes() + b" ")
        self.assertEqual(self.send(), 1)
        self.assertEqual(self.calls, [])

    def test_altered_original_is_refused(self):
        (self.repo / "docs/a.pdf").write_bytes(b"%PDF-other")
        self.assertEqual(self.send(), 1)
        self.assertEqual(self.calls, [])

    def test_existing_ledger_blocks_any_post(self):
        (self.execution / "01-doc-a").mkdir(parents=True)
        (self.execution / "01-doc-a/attempt.json").write_text("{}")
        self.assertEqual(self.send(), 1)
        self.assertEqual(self.calls, [])
        self.assertIn("uncertain", self.status())

    def test_uncertainty_stops_and_blocks_later_runs(self):
        def failing(request, timeout):
            self.calls.append(request)
            raise urllib.error.URLError(TimeoutError())

        self.assertEqual(self.send(failing), 1)
        self.assertEqual(len(self.calls), 1)  # doc-c never attempted
        self.assertFalse((self.execution / "02-doc-c").exists())
        result = json.loads((self.execution / "01-doc-a/result.json").read_text())
        self.assertEqual(
            (result["state"], result["category"]), ("uncertain_no_replay", "timeout")
        )
        self.assertFalse((self.execution / "01-doc-a/response.raw").exists())
        report = self.status()
        self.assertIn("consumo desconocido", report)
        self.assertIn("do NOT retry", report)
        self.calls.clear()
        self.assertEqual(self.send(), 1)
        self.assertEqual(self.calls, [])

    def test_http_error_saves_raw_and_stops(self):
        def server_error(request, timeout):
            self.calls.append(request)
            return FakeResponse(b'{"success": false}', status=500)

        self.assertEqual(self.send(server_error), 1)
        self.assertEqual(len(self.calls), 1)
        result = json.loads((self.execution / "01-doc-a/result.json").read_text())
        self.assertEqual(result["state"], "response_saved_no_replay")
        self.assertEqual(result["http_status"], 500)

    def test_incomplete_coverage_is_not_complete(self):
        def short(request, timeout):
            self.calls.append(request)
            return FakeResponse(response_body(1))  # 1 page for a 2-page lot

        self.assertEqual(self.send(short), 1)
        result = json.loads((self.execution / "01-doc-a/result.json").read_text())
        self.assertEqual(result["state"], "response_saved_no_replay")
        self.assertEqual(result["credits_declared"], 1)

    def test_missing_credits_is_reported_as_unknown(self):
        def no_credits(request, timeout):
            n = 2 if b"01-doc-a" in request.data else 1
            return FakeResponse(response_body(n, credits=None))

        self.assertEqual(self.send(no_credits), 0)
        self.assertIn("credits_declared=consumo desconocido", self.status())

    def test_empty_page_markdown_is_recorded_with_physical_page(self):
        def blank_second(request, timeout):
            n = 2 if b"01-doc-a" in request.data else 1
            return FakeResponse(
                response_body(n, markdown=lambda i: "" if i == 2 else "x")
            )

        self.assertEqual(self.send(blank_second), 0)
        result = json.loads((self.execution / "01-doc-a/result.json").read_text())
        self.assertEqual(result["empty_markdown_pages"], [4])


class CliTests(unittest.TestCase):
    """The paid command needs the exact confirmation flag."""

    def run_main(self, *argv):
        with (
            patch.object(sys, "argv", ["selective_send.py", *argv]),
            patch.object(
                s, "send", side_effect=AssertionError("must not send")
            ) as sender,
            patch.object(
                s, "load_key", side_effect=AssertionError("must not load key")
            ),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            s.main()
        sender.assert_not_called()

    def test_send_without_flag_does_not_send(self):
        self.run_main("send", "--env-file", ".env")

    def test_send_with_wrong_credit_count_does_not_send(self):
        self.run_main("send", "--confirm-credits", "105", "--env-file", ".env")
        self.run_main("send", "--confirm-credits", "107")


if __name__ == "__main__":
    unittest.main()

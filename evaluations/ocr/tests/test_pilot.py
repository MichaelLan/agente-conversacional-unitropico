"""Offline contract checks for the bounded Firecrawl pilot."""

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pilot


class PilotTests(unittest.TestCase):
    """Exercise public operations with fake credentials and isolated files."""

    def setUp(self) -> None:
        """Create an isolated fragment and provenance record."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pdf = b"%PDF-1.7\nfake two-page fixture"
        (self.root / "pages-13-14.pdf").write_bytes(self.pdf)
        self.manifest = {
            "page_count": 2,
            "mapping": {"1": 13, "2": 14},
            "fragment_sha256": hashlib.sha256(self.pdf).hexdigest(),
            "fragment_bytes": len(self.pdf),
            "original_sha256": "a" * 64,
            "original_sha256_after": "a" * 64,
            "original_page_count": 17,
            "original": str(pilot.ORIGINAL.relative_to(pilot.BASE.parents[1])),
        }
        (self.root / "manifest.json").write_text(json.dumps(self.manifest))
        self.key = "fc-FAKE_ONLY_DO_NOT_USE"
        for name, value in (
            ("FRAGMENT_SHA256", hashlib.sha256(self.pdf).hexdigest()),
            ("FRAGMENT_BYTES", len(self.pdf)),
            ("ORIGINAL_SHA256", "a" * 64),
        ):
            binding = patch.object(pilot, name, value, create=True)
            binding.start()
            self.addCleanup(binding.stop)
        self.verify = patch.object(pilot, "page_count", return_value=2)
        self.verify.start()
        self.addCleanup(self.verify.stop)

    def test_multipart_and_single_attempt(self) -> None:
        """Send just the mapped fragment, then refuse replay unchanged."""
        response = Mock()
        response.status = 200
        response.read.return_value = b'{"success":true,"data":{"pages":[],"blocks":[]}}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        transport = Mock(return_value=response)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.assertEqual(pilot.send(self.root, self.key, transport), 0)
            before = {p.name: p.read_bytes() for p in self.root.iterdir()}
            self.assertEqual(pilot.send(self.root, self.key, transport), 1)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
        req = transport.call_args.args[0]
        self.assertEqual(req.full_url, "https://api.firecrawl.dev/v2/parse")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(transport.call_args.kwargs, {"timeout": 330})
        self.assertEqual(req.get_header("Authorization"), "Bearer " + self.key)
        self.assertIn(b'name="file"; filename="pages-13-14.pdf"', req.data)
        self.assertIn(self.pdf, req.data)
        self.assertIn(b'name="options"\r\nContent-Type: application/json', req.data)
        options = json.loads(
            req.data.split(b"Content-Type: application/json\r\n\r\n")[1].split(
                b"\r\n--"
            )[0]
        )
        self.assertEqual(
            options,
            {
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
            },
        )
        attempt = json.loads((self.root / "attempt.json").read_text())
        self.assertEqual(attempt["mapping"], {"1": 13, "2": 14})
        self.assertEqual(attempt["estimated_credits"], 2)
        self.assertEqual(
            (self.root / "response.raw").read_bytes(), response.read.return_value
        )
        self.assertNotIn(self.key, output.getvalue())
        for name in ("attempt.json", "result.json"):
            self.assertNotIn(self.key, (self.root / name).read_text())

    def test_timeout_and_http_errors_never_retry(self) -> None:
        """Every uncertain or rejected request consumes the local attempt."""
        for error in (
            TimeoutError(self.key),
            urllib.error.URLError(self.key),
            urllib.error.HTTPError(
                "https://api.firecrawl.dev/v2/parse",
                429,
                self.key,
                {},
                io.BytesIO(b"limited"),
            ),
        ):
            with (
                self.subTest(error=type(error).__name__),
                tempfile.TemporaryDirectory() as td,
            ):
                root = Path(td)
                for name in ("pages-13-14.pdf", "manifest.json"):
                    (root / name).write_bytes((self.root / name).read_bytes())
                transport = Mock(side_effect=error)
                output = io.StringIO()
                with (
                    contextlib.redirect_stdout(output),
                    contextlib.redirect_stderr(output),
                ):
                    self.assertEqual(pilot.send(root, self.key, transport), 1)
                    before = {p.name: p.read_bytes() for p in root.iterdir()}
                    self.assertEqual(pilot.send(root, self.key, transport), 1)
                self.assertEqual(transport.call_count, 1)
                self.assertEqual(
                    before, {p.name: p.read_bytes() for p in root.iterdir()}
                )
                self.assertNotIn(self.key, output.getvalue())
                self.assertNotIn(self.key, (root / "result.json").read_text())
                self.assertTrue((root / "attempt.json").exists())

    def test_invalid_fragment_rejected_before_attempt(self) -> None:
        """Reject changed bytes, wrong mapping, count and size without HTTP."""
        for field, value in (
            ("mapping", {"1": 1, "2": 2}),
            ("page_count", 17),
            ("fragment_sha256", "b" * 64),
            ("fragment_bytes", 1),
        ):
            with self.subTest(field=field):
                manifest = dict(self.manifest, **{field: value})
                (self.root / "manifest.json").write_text(json.dumps(manifest))
                transport = Mock()
                before = {p.name: p.read_bytes() for p in self.root.iterdir()}
                self.assertEqual(pilot.send(self.root, self.key, transport), 1)
                transport.assert_not_called()
                self.assertEqual(
                    before, {p.name: p.read_bytes() for p in self.root.iterdir()}
                )
        (self.root / "manifest.json").write_text(json.dumps(self.manifest))
        with patch.object(pilot, "page_count", return_value=17):
            transport = Mock()
            self.assertEqual(pilot.send(self.root, self.key, transport), 1)
            transport.assert_not_called()
        self.assertFalse((self.root / "attempt.json").exists())

    def assert_refused_unchanged(self, root: Path) -> None:
        """Assert safe rejection, zero HTTP calls and byte-identical state."""
        before = {p.name: p.read_bytes() for p in root.iterdir()}
        transport = Mock(side_effect=TimeoutError(self.key))
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(pilot.send(root, self.key, transport), 1)
        self.assertEqual(transport.call_count, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Preflight refused; no POST.", stderr.getvalue())
        self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})
        self.assertFalse((root / "attempt.json").exists())

    def test_self_consistent_substitute_fragment_is_refused(self) -> None:
        """A replacement PDF cannot authorize itself by rewriting its manifest."""
        replacement = b"%PDF-1.7\nsubstituted two-page PDF"
        (self.root / "pages-13-14.pdf").write_bytes(replacement)
        manifest = dict(
            self.manifest,
            fragment_sha256=hashlib.sha256(replacement).hexdigest(),
            fragment_bytes=len(replacement),
        )
        (self.root / "manifest.json").write_text(json.dumps(manifest))
        self.assert_refused_unchanged(self.root)

    def test_corrupt_provenance_is_refused(self) -> None:
        """Validate each source identity field, not just fragment consistency."""
        corruptions = (
            {"original_sha256": "0" * 64},
            {"original_sha256_after": "1" * 64},
            {"original_page_count": 216},
            {
                "original_sha256": "0" * 64,
                "original_sha256_after": "1" * 64,
                "original_page_count": 216,
            },
            {"original_sha256": "0" * 64, "original_sha256_after": "0" * 64},
            {"original": "documents/substitute.pdf"},
        )
        for corrupted in corruptions:
            with self.subTest(corrupted=corrupted), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / "pages-13-14.pdf").write_bytes(self.pdf)
                (root / "manifest.json").write_text(
                    json.dumps(dict(self.manifest, **corrupted))
                )
                self.assert_refused_unchanged(root)

    def test_keyboard_interrupt_keeps_attempt_and_prevents_replay(self) -> None:
        """Cancellation propagates but the durable pre-HTTP reservation survives."""

        def interrupt(request: object, *, timeout: int) -> None:
            """Check reservation already exists at the transport boundary."""
            self.assertEqual(timeout, 330)
            self.assertTrue((self.root / "attempt.json").is_file())
            raise KeyboardInterrupt

        transport = Mock(side_effect=interrupt)
        with self.assertRaises(KeyboardInterrupt):
            pilot.send(self.root, self.key, transport)
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(pilot.send(self.root, self.key, transport), 1)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Preflight refused; no POST.", stderr.getvalue())
        self.assertNotIn(self.key, (self.root / "attempt.json").read_text())

    def test_corrupt_bytes_are_refused_safely(self) -> None:
        """Binary corruption returns only the generic error without state changes."""
        (self.root / "pages-13-14.pdf").write_bytes(b"\x00\xff" + self.key.encode())
        self.assert_refused_unchanged(self.root)

    def test_credentials_are_narrow_and_fail_closed(self) -> None:
        """Load only the requested variable; never evaluate dotenv content."""
        env = self.root / "explicit.env"
        with patch.dict("os.environ", {}, clear=True):
            for text in (
                'OTHER=ignore\nFIRECRAWL_API_KEY="fc-fake"\n',
                "FIRECRAWL_API_KEY='fc-fake'\n",
            ):
                env.write_text(text)
                self.assertEqual(pilot.load_key(env), "fc-fake")
            for text in (
                "FIRECRAWL_API_KEY=$(command)\n",
                "FIRECRAWL_API_KEY=x\nFIRECRAWL_API_KEY=y\n",
                'FIRECRAWL_API_KEY="multiline\nvalue"\n',
                "OTHER=ignore\n",
                "FIRECRAWL_API_KEY=x #comment\n",
            ):
                env.write_text(text)
                with self.assertRaises(ValueError):
                    pilot.load_key(env)
        with patch.dict("os.environ", {"FIRECRAWL_API_KEY": self.key}, clear=True):
            self.assertEqual(pilot.load_key(None), self.key)

    def test_prepare_keeps_source_and_exact_mapping(self) -> None:
        """Prepare only pages 13–14 using constrained Docker and checked hashes."""
        source = self.root / "original.pdf"
        source.write_bytes(b"%PDF-original-seventeen-pages")
        artifacts = self.root / "prepared"

        def extract(args: list[str], **kwargs: object) -> Mock:
            """Simulate only local extraction, never networking or real Docker."""
            self.assertEqual(kwargs["timeout"], 60)
            self.assertIn("--network", args)
            self.assertEqual(args[args.index("--network") + 1], "none")
            self.assertEqual(args[args.index("--cap-drop") + 1], "ALL")
            self.assertEqual(args[args.index("--pull") + 1], "never")
            self.assertIn("no-new-privileges", args)
            self.assertIn(f"type=bind,src={source},dst=/source.pdf,readonly", args)
            self.assertIn("pdfseparate -f 13 -l 14", args[-1])
            self.assertIn("pdfunite /out/page-13.pdf /out/page-14.pdf", args[-1])
            (artifacts / "pages-13-14.pdf").write_bytes(self.pdf)
            return Mock()

        with (
            patch.object(pilot, "ORIGINAL", source),
            patch.object(pilot, "ARTIFACTS", artifacts),
            patch.object(pilot, "BASE", self.root / "evaluations/ocr"),
            patch.object(pilot, "page_count", side_effect=[17, 2]),
            patch.object(pilot.subprocess, "run", side_effect=extract) as docker,
        ):
            self.assertEqual(pilot.prepare(), 0)
            docker.assert_called_once()
        manifest = json.loads((artifacts / "manifest.json").read_text())
        self.assertEqual(manifest["mapping"], {"1": 13, "2": 14})
        self.assertEqual(manifest["page_count"], 2)
        self.assertEqual(manifest["fragment_bytes"], len(self.pdf))
        self.assertEqual(
            manifest["fragment_sha256"], hashlib.sha256(self.pdf).hexdigest()
        )
        self.assertEqual(
            manifest["original_sha256"], hashlib.sha256(source.read_bytes()).hexdigest()
        )
        self.assertEqual(manifest["original_sha256"], manifest["original_sha256_after"])
        self.assertEqual(source.read_bytes(), b"%PDF-original-seventeen-pages")

    def test_safe_flushed_phase_logs_and_elapsed(self) -> None:
        """Expose completed local phases without leaking credentials or raw data."""
        response = Mock(status=200)
        response.read.return_value = b'{"success":true,"secret":"RAW_PRIVATE"}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        stderr = Mock()
        with patch.object(pilot.sys, "stderr", stderr):
            self.assertEqual(
                pilot.send(self.root, self.key, Mock(return_value=response)), 0
            )
        text = "".join(call.args[0] for call in stderr.write.call_args_list)
        phases = [
            "credential_validated",
            "pdf_validation_started",
            "pdf_validated",
            "ledger_reservation_started",
            "ledger_reserved",
            "http_started",
            "http_status",
            "raw_saved",
            "finished",
        ]
        positions = [text.index("phase=" + phase + " ") for phase in phases]
        self.assertEqual(positions, sorted(positions))
        self.assertRegex(text, r"\d{4}-\d{2}-\d{2}T[^ ]+\+00:00 phase=")
        self.assertIn("elapsed_s=", text)
        self.assertIn("status=200", text)
        self.assertNotIn(self.key, text)
        self.assertNotIn("RAW_PRIVATE", text)
        self.assertNotIn("Authorization", text)
        self.assertGreaterEqual(stderr.flush.call_count, len(phases))

    def test_safe_failure_categories_and_interruption_result(self) -> None:
        """Persist controlled failure categories, never exception strings; no replay."""
        errors = [
            (TimeoutError(self.key), "timeout"),
            (urllib.error.URLError(TimeoutError(self.key)), "timeout"),
            (urllib.error.URLError(self.key), "network_error"),
            (RuntimeError(self.key), "unexpected_error"),
            (KeyboardInterrupt(self.key), "interrupted"),
        ]
        for error, category in errors:
            with self.subTest(category=category), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                for name in ("pages-13-14.pdf", "manifest.json"):
                    (root / name).write_bytes((self.root / name).read_bytes())
                output = io.StringIO()
                transport = Mock(side_effect=error)
                with (
                    contextlib.redirect_stderr(output),
                    contextlib.redirect_stdout(output),
                ):
                    if isinstance(error, KeyboardInterrupt):
                        with self.assertRaises(KeyboardInterrupt):
                            pilot.send(root, self.key, transport)
                    else:
                        self.assertEqual(pilot.send(root, self.key, transport), 1)
                    result = json.loads((root / "result.json").read_text())
                    self.assertEqual(result["state"], "uncertain_no_replay")
                    self.assertEqual(result["error_category"], category)
                    self.assertIn("category=" + category, output.getvalue())
                    self.assertIn(
                        "Intento registrado; consumo desconocido", output.getvalue()
                    )
                    self.assertNotIn("Attempt consumed", output.getvalue())
                    self.assertNotIn(self.key, output.getvalue())
                    before = {p.name: p.read_bytes() for p in root.iterdir()}
                    self.assertEqual(pilot.send(root, self.key, transport), 1)
                self.assertEqual(transport.call_count, 1)
                self.assertEqual(
                    before, {p.name: p.read_bytes() for p in root.iterdir()}
                )

    def test_poppler_timeout_is_bounded_before_http(self) -> None:
        """Bound local subprocess waits and safely diagnose their timeouts."""
        self.verify.stop()
        with patch.object(
            pilot.subprocess, "run", return_value=Mock(stdout="Pages: 2\n")
        ) as run:
            self.assertEqual(pilot.page_count(self.root / "pages-13-14.pdf"), 2)
            self.assertEqual(run.call_args.kwargs["timeout"], 60)
        transport = Mock()
        output = io.StringIO()
        with (
            patch.object(
                pilot.subprocess,
                "run",
                side_effect=pilot.subprocess.TimeoutExpired("secret", 60),
            ),
            contextlib.redirect_stderr(output),
        ):
            self.assertEqual(pilot.send(self.root, self.key, transport), 1)
        self.assertIn("category=poppler_timeout", output.getvalue())
        self.assertNotIn("secret", output.getvalue())
        self.assertFalse((self.root / "attempt.json").exists())
        transport.assert_not_called()

    def test_credential_load_failure_is_logged_safely(self) -> None:
        """CLI credential errors are visible without exposing values or paths."""
        output = io.StringIO()
        with (
            patch.object(
                pilot.sys, "argv", ["pilot.py", "send", "--confirm-two-credits"]
            ),
            patch.object(pilot, "load_key", side_effect=ValueError(self.key)),
            patch.object(pilot, "send") as send,
            contextlib.redirect_stderr(output),
        ):
            self.assertEqual(pilot.main(), 1)
        send.assert_not_called()
        self.assertIn("phase=credential_load_started", output.getvalue())
        self.assertIn("category=invalid_input", output.getvalue())
        self.assertNotIn(self.key, output.getvalue())

    def test_response_and_result_failures_are_visible_without_replay(self) -> None:
        """Report HTTP, JSON and local persistence failures without leaking bodies."""
        cases = [
            (429, b'{"success":false,"private":"RAW_PRIVATE"}', False),
            (200, b"RAW_PRIVATE", False),
            (200, b'{"success":true}', True),
        ]
        for status, raw, fail_result in cases:
            with (
                self.subTest(status=status, fail_result=fail_result),
                tempfile.TemporaryDirectory() as td,
            ):
                root = Path(td)
                for name in ("pages-13-14.pdf", "manifest.json"):
                    (root / name).write_bytes((self.root / name).read_bytes())
                if fail_result:
                    (root / "result.json").write_text("preserve-existing-result")
                response = Mock(status=status)
                response.read.return_value = raw
                response.__enter__ = Mock(return_value=response)
                response.__exit__ = Mock(return_value=False)
                transport = Mock(return_value=response)
                output = io.StringIO()
                with contextlib.redirect_stderr(output):
                    self.assertEqual(pilot.send(root, self.key, transport), 1)
                    self.assertEqual(pilot.send(root, self.key, transport), 1)
                self.assertEqual(transport.call_count, 1)
                self.assertIn("status=" + str(status), output.getvalue())
                self.assertNotIn("RAW_PRIVATE", output.getvalue())
                self.assertEqual((root / "response.raw").read_bytes(), raw)
                if fail_result:
                    self.assertIn("phase=result_save_failed", output.getvalue())
                    self.assertEqual(
                        (root / "result.json").read_text(), "preserve-existing-result"
                    )

    def test_ledger_is_fsynced_before_transport(self) -> None:
        """HTTP must never begin before the exclusive reservation is durable."""
        transport = Mock(side_effect=TimeoutError("private"))
        with (
            patch.object(pilot.os, "fsync", side_effect=OSError("private")),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(pilot.send(self.root, self.key, transport), 1)
        transport.assert_not_called()
        self.assertTrue((self.root / "attempt.json").exists())

    def test_second_authorized_attempt_preserves_previous_evidence(self) -> None:
        """Allow just one isolated second send, without resetting the first ledger."""
        pilot.write_json(
            self.root / "attempt.json", {"state": "reserved_may_have_been_sent"}
        )
        pilot.write_json(
            self.root / "result.json",
            {"state": "uncertain_no_replay", "http_status": None},
        )
        original = {p.name: p.read_bytes() for p in self.root.iterdir()}
        transport = Mock(side_effect=TimeoutError(self.key))
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.assertEqual(
                pilot.send_second_attempt(self.root, self.key, transport), 1
            )
            second = self.root / "second-attempt"
            before = {p.name: p.read_bytes() for p in second.iterdir()}
            self.assertEqual(
                pilot.send_second_attempt(self.root, self.key, transport), 1
            )
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(
            original, {name: (self.root / name).read_bytes() for name in original}
        )
        self.assertEqual(before, {p.name: p.read_bytes() for p in second.iterdir()})
        self.assertEqual(
            json.loads((second / "result.json").read_text())["state"],
            "uncertain_no_replay",
        )
        self.assertEqual((second / "pages-13-14.pdf").read_bytes(), self.pdf)
        self.assertNotIn(self.key, output.getvalue())

    def test_second_attempt_requires_uncertain_first_attempt_and_valid_identity(
        self,
    ) -> None:
        """No new HTTP without prior uncertainty or with substituted source bytes."""
        transport = Mock()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                pilot.send_second_attempt(self.root, self.key, transport), 1
            )
            self.assertFalse((self.root / "second-attempt").exists())
            pilot.write_json(
                self.root / "attempt.json", {"state": "reserved_may_have_been_sent"}
            )
            pilot.write_json(
                self.root / "result.json",
                {"state": "response_saved_no_replay", "http_status": 200},
            )
            self.assertEqual(
                pilot.send_second_attempt(self.root, self.key, transport), 1
            )
            self.assertFalse((self.root / "second-attempt").exists())
            (self.root / "result.json").write_text(
                '{"state":"uncertain_no_replay","http_status":null}'
            )
            (self.root / "pages-13-14.pdf").write_bytes(b"%PDF-substituted")
            self.assertEqual(
                pilot.send_second_attempt(self.root, self.key, transport), 1
            )
        transport.assert_not_called()
        self.assertFalse((self.root / "second-attempt" / "attempt.json").exists())

    def test_second_attempt_flag_is_explicit_and_keeps_confirmation(self) -> None:
        """Route the extra authorization flag only with the existing cost confirmation."""
        args = [
            "pilot.py",
            "send",
            "--confirm-two-credits",
            "--second-authorized-attempt",
        ]
        with (
            patch.object(pilot.sys, "argv", args),
            patch.object(pilot, "load_key", return_value=self.key),
            patch.object(
                pilot, "send_second_attempt", return_value=0, create=True
            ) as second,
            patch.object(pilot, "send") as first,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(pilot.main(), 0)
        first.assert_not_called()
        second.assert_called_once_with(pilot.ARTIFACTS, self.key)
        with (
            patch.object(
                pilot.sys, "argv", ["pilot.py", "send", "--second-authorized-attempt"]
            ),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as stopped,
        ):
            pilot.main()
        self.assertEqual(stopped.exception.code, 2)

    def test_redirect_is_refused(self) -> None:
        """Never construct a redirected request carrying credentials."""
        handler = pilot.NoRedirect()
        self.assertIsNone(
            handler.redirect_request(
                Mock(), None, 307, "redirect", {}, "https://other.invalid/"
            )
        )


if __name__ == "__main__":
    unittest.main()

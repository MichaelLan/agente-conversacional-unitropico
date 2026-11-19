"""Offline public sender contract; fixtures never use real credentials or PDFs."""

import contextlib
import copy
import http.client
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import remaining_send as s


class SenderTests(unittest.TestCase):
    """Exercise fixed-input provenance and one-shot execution with isolated fixtures."""

    def setUp(self):
        """Build fake pinned PDFs; Docker and subprocess execution are unavailable."""
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        specs = []
        for name, _, _, _, _, mapping in s.SPECS:
            data = b"%PDF-" + name.encode()
            source = self.root / (name + "-source.pdf")
            source.write_bytes(data)
            (self.root / (name + ".pdf")).write_bytes(data)
            specs.append(
                (name, source, s.digest(data), s.digest(data), len(data), mapping)
            )
        for name, value in [("ROOT", self.root), ("SPECS", tuple(specs))]:
            self.enterContext(patch.object(s, name, value))
        self.plan = {
            "schema_version": 1,
            "state": "prepared_offline_not_sent",
            "image": s.IMAGE,
            "estimated_credits": 35,
            "spent_by_preparation": 0,
            "historical_ceiling": 100,
            "payloads": [s.entry(spec) for spec in specs],
        }
        self.save_plan()
        self.enterContext(
            patch(
                "subprocess.run", side_effect=FileNotFoundError(2, "absent", "docker")
            )
        )
        self.enterContext(
            patch("subprocess.Popen", side_effect=AssertionError("No subprocess"))
        )
        self.key = "FAKE_SECRET"
        self.logs = io.StringIO()
        self.enterContext(contextlib.redirect_stderr(self.logs))

    def save_plan(self):
        """Write only the mutable fixture plan, never production artifacts."""
        (self.root / "preparation.json").write_text(json.dumps(self.plan))

    def response(self, n=20, status=200, mutate=None):
        """Return a complete fake response with observed group status and metadata."""
        value = {
            "success": True,
            "data": {
                "pages": [
                    {"pageNumber": i, "markdown": "text"} for i in range(1, n + 1)
                ],
                "blocks": [
                    {
                        "pageNumber": i,
                        "items": [],
                        "status": ("ok", "completed", "success")[i % 3],
                    }
                    for i in range(1, n + 1)
                ],
                "metadata": {"creditsUsed": n, "numPages": n, "totalPages": n},
            },
        }
        if mutate:
            mutate(value)
        response = MagicMock(status=status, headers={})
        response.read.return_value = json.dumps(value).encode()
        response.__enter__.return_value = response
        return response

    def snapshot(self):
        """Capture fixture bytes to detect any overwrite during refused replay."""
        return {
            str(p.relative_to(self.root)): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def stopped(self, outcomes, credits=None):
        """Assert stop, durable result, exact attempt count, redaction and immutable replay."""
        with (
            tempfile.TemporaryDirectory() as td,
            patch.object(s, "EXECUTION_NAME", Path(td) / "execution"),
        ):
            transport = Mock(side_effect=outcomes)
            if outcomes and isinstance(outcomes[-1], KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    s.send(self.key, transport)
            else:
                self.assertEqual(s.send(self.key, transport), 1)
            result = json.loads((Path(td) / "execution/result.json").read_text())
            self.assertEqual(result["state"], "stopped_no_replay")
            if credits is not None:
                self.assertEqual(result["known_credits"], credits)
            self.assertNotIn(self.key, json.dumps(result) + self.logs.getvalue())
            before = (Path(td) / "execution/result.json").read_bytes()
            self.assertEqual(s.send(self.key, transport), 1)
            self.assertEqual(before, (Path(td) / "execution/result.json").read_bytes())
            self.assertEqual(transport.call_count, len(outcomes))
            return result

    def test_two_posts_order_options_durable_and_no_replay(self):
        """Preserve exact multipart options, ordering, raw bytes and pre-POST durability."""
        calls = []

        def post(req, timeout):
            """Check both durable reservations at each HTTP boundary."""
            name, n = [("res137", 20), ("conv2026", 15)][len(calls)]
            for path in ["batch.json", name + "/attempt.json"]:
                self.assertTrue((self.root / "execution" / path).is_file())
            self.assertGreaterEqual(sync.call_count, 6 if not calls else 13)
            self.assertEqual(
                (req.full_url, req.method, timeout), (s.ENDPOINT, "POST", 330)
            )
            self.assertIn(('filename="' + name + '.pdf"').encode(), req.data)
            options = json.loads(
                req.data.split(b"application/json\r\n\r\n")[1].split(b"\r\n--")[0]
            )
            self.assertEqual(options, s.entry(s.SPECS[len(calls)])["options"])
            self.assertEqual(options["parsers"][0]["maxPages"], n)
            response = self.response(n)
            calls.append(response)
            return response

        with patch.object(s.os, "fsync", wraps=s.os.fsync) as sync:
            self.assertEqual(s.send(self.key, post), 0)
        before = self.snapshot()
        self.assertEqual(s.send(self.key, post), 1)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(len(calls), 2)
        for name, response in zip(("res137", "conv2026"), calls, strict=False):
            self.assertEqual(
                (self.root / f"execution/{name}/response.raw").read_bytes(),
                response.read.return_value,
            )
        result = json.loads((self.root / "execution/result.json").read_text())
        self.assertEqual(result["known_credits"], 35)
        self.assertFalse(result["unknown_consumption"])
        self.assertNotIn(self.key, self.logs.getvalue())
        self.assertIn("phase=finished", self.logs.getvalue())

    def test_invalid_plan_payload_source_and_count_before_any_post(self):
        """Reject self-consistent one-byte changes, counts, duplicate keys and symlinks."""
        original = copy.deepcopy(self.plan)
        cases = [
            ("mapping", {"1": 99}),
            ("source", "/arbitrary.pdf"),
            ("sha256", "0" * 64),
            ("options", {}),
            ("page_count", True),
            ("source_page_count", 99),
            ("payload", None),
            ("source_bytes", None),
        ]
        for field, value in cases:
            self.plan = copy.deepcopy(original)
            target = self.root / (
                "conv2026-source.pdf" if field == "source_bytes" else "conv2026.pdf"
            )
            if field in ("payload", "source_bytes"):
                target.write_bytes(b"%PDF-conv2027")
                if field == "payload":
                    self.plan["payloads"][1].update(
                        sha256=s.digest(target.read_bytes()),
                        bytes=target.stat().st_size,
                    )
                else:
                    self.plan["payloads"][1].update(
                        dict.fromkeys(
                            ("source_sha256_before", "source_sha256_after"),
                            s.digest(target.read_bytes()),
                        )
                    )
            else:
                self.plan["payloads"][1][field] = value
            self.save_plan()
            self.refused()
            target.write_bytes(b"%PDF-conv2026")
        self.plan = original
        self.save_plan()
        (self.root / "preparation.json").write_text(
            json.dumps(self.plan).replace(
                '"schema_version": 1', '"schema_version": 1, "schema_version": 1'
            )
        )
        self.refused()
        self.save_plan()
        linked = self.root / "linked"
        linked.symlink_to(self.root, target_is_directory=True)
        with patch.object(s, "ROOT", linked):
            self.refused()

    def refused(self):
        """Assert preflight refusal has zero HTTP calls and no execution reservation."""
        transport = Mock()
        self.assertEqual(s.send(self.key, transport), 1)
        transport.assert_not_called()
        self.assertFalse((self.root / "execution").exists())

    def test_first_uncertainty_stops_second_and_redacts(self):
        """Reject every count contradiction and group failure with known credits retained."""
        mutations = [
            lambda v, k=k, x=x: v["data"]["metadata"].update({k: x})
            for k in ("pageCount", "numPages", "totalPages")
            for x in (19, True)
        ]
        mutations += [
            lambda v, f=f, d=d: v["data"][f][0].update(d)
            for f in ("pages", "blocks")
            for d in ({"status": "failed"}, {"error": "failed"}, {"success": False})
        ]
        mutations += [
            lambda v: v.update(success=False),
            lambda v: v["data"]["pages"].pop(),
            lambda v: v["data"]["pages"][0].update(pageNumber=2),
            lambda v: v["data"]["blocks"].pop(),
            lambda v: v["data"]["blocks"][0].update(items=[{"pageNumber": 2}]),
            lambda v: v["data"].update(status="processing"),
        ]
        for i, mutate in enumerate(mutations):
            with self.subTest(rule=i):
                self.stopped([self.response(mutate=mutate)], credits=20)
        for credit in [None, True, -1, 21, 1.5]:
            self.stopped(
                [
                    self.response(
                        mutate=lambda v: v["data"]["metadata"].update(
                            creditsUsed=credit
                        )
                    )
                ]
            )
        malformed, incomplete, short = self.response(), self.response(), self.response()
        malformed.read.return_value = b"FAKE_SECRET invalid JSON"
        incomplete.read.side_effect = http.client.IncompleteRead(b"FAKE_SECRET")
        short.headers = {"Content-Length": "999999"}
        for failure in [
            malformed,
            incomplete,
            short,
            self.response(status=202),
            urllib.error.HTTPError(
                "private", 429, self.key, {}, io.BytesIO(b"private")
            ),
            TimeoutError(self.key),
            urllib.error.URLError(self.key),
            KeyboardInterrupt(self.key),
        ]:
            self.stopped([failure])

    def test_zero_credits_and_second_unknown_report_no_invented_charge(self):
        """Retain declared zero credits while the second request remains unknown."""
        first = self.response(
            mutate=lambda v: v["data"]["metadata"].update(creditsUsed=0)
        )
        result = self.stopped([first, TimeoutError(self.key)], credits=0)
        self.assertTrue(result["unknown_consumption"])
        self.assertTrue(result["lots"][0]["coverage"])
        self.assertIsNone(result["lots"][1]["credits"])

    def test_durable_reservation_failures_prevent_network(self):
        """Refuse both batch fsync failure and lot ledger failure without replay."""
        writer = s.record

        def fail(path, value):
            """Fail only the per-lot ledger, after the batch reservation succeeds."""
            if path.name == "attempt.json":
                raise OSError(self.key)
            writer(path, value)

        for target, name, replacement in [
            (s, "record", fail),
            (s.os, "fsync", Mock(side_effect=OSError(self.key))),
        ]:
            with patch.object(target, name, replacement):
                self.stopped([])

    def test_check_cli_no_key_no_reservation_and_interrupt_exit(self):
        """Keep offline checks credential-free and CLI interruption traceback-free."""
        with (
            patch.object(sys, "argv", ["sender", "--check"]),
            patch.object(s, "load_key") as key,
        ):
            self.assertEqual(s.main(), 0)
            key.assert_not_called()
        self.assertFalse((self.root / "execution").exists())
        with (
            patch.object(
                sys,
                "argv",
                ["sender", "--confirm-thirty-five-credits", "--env-file", ".env"],
            ),
            patch.object(s, "load_key", side_effect=KeyboardInterrupt(self.key)),
        ):
            self.assertEqual(s.main(), 130)

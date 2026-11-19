"""Offline prerequisite diagnostics; no credentials, production state or HTTP."""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pilot
import remaining_send as sender


class PrerequisiteTests(unittest.TestCase):
    def test_exact_missing_docker_category_and_other_io_preserved(self):
        for error, expected in [
            (FileNotFoundError(2, "private", "docker"), "docker_unavailable"),
            (FileNotFoundError(2, "private", "other"), "local_io_error"),
            (FileNotFoundError("private"), "local_io_error"),
            (OSError(5, "private", "docker"), "local_io_error"),
        ]:
            with self.subTest(expected=expected, error_type=type(error).__name__):
                self.assertEqual(pilot.error_category(error), expected)

    def test_missing_docker_stops_sender_before_reservation_or_http(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stdout, stderr, transport = io.StringIO(), io.StringIO(), Mock()
            with (
                patch.object(sender, "ROOT", root),
                patch.object(
                    sender,
                    "preflight",
                    side_effect=FileNotFoundError(2, "FAKE_SECRET", "docker"),
                ) as preflight,
                patch.object(sender.time, "monotonic", return_value=0),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                self.assertEqual(sender.send("FAKE_SECRET", transport), 1)
            preflight.assert_called_once_with()
            transport.assert_not_called()
            self.assertEqual(list(root.iterdir()), [])
            self.assertFalse((root / "execution").exists())
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                [
                    line.split(" phase=", 1)[-1]
                    for line in stderr.getvalue().splitlines()
                ],
                [
                    "preflight_started elapsed_s=0.000 category=none status=None",
                    "stopped elapsed_s=0.000 category=docker_unavailable status=None",
                    "finished elapsed_s=0.000 category=none status=None",
                    "Cobertura/disponibilidad y créditos solo según payload; no calidad OCR. "
                    "Consumo incierto si no declarado; no reenviar. Ver registros locales.",
                ],
            )
            self.assertNotIn("FAKE_SECRET", stderr.getvalue())

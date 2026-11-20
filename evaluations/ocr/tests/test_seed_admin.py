"""seed_admin.py against a local fake admin API (http://127.0.0.1, no real network)."""

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))
import seed_admin as sa

TOKEN = "tok-secreto-de-prueba"
SHA_A, SHA_B = "aa" * 32, "bb" * 32


class FakeAdmin:
    """In-memory admin state shared with the handler."""

    def __init__(self):
        self.versions = {
            "ver_a": {
                "id": "ver_a",
                "status": "review_required",
                "source_sha256": SHA_A,
            },
            "ver_b": {
                "id": "ver_b",
                "status": "review_required",
                "source_r2_key": f"raw/{SHA_B}.pdf",
            },
        }
        self.corrections = {"ver_a": [], "ver_b": []}
        self.overrides = {"ver_a": [], "ver_b": []}
        self.log = []
        self.hide_from_get = False  # simulate a correction GET does not list
        self.fail_post_with = None
        self.rate_limit_once = False


def make_handler(state: FakeAdmin):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, body, headers=None):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def handle_any(self, method):
            state.log.append((method, self.path))
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                return self.reply(401, {"error": {"code": "unauthorized"}})
            if state.rate_limit_once:
                state.rate_limit_once = False
                return self.reply(
                    429, {"error": {"code": "rate_limited"}}, {"Retry-After": "2"}
                )
            parts = self.path.strip("/").split("/")
            body = {}
            if method == "POST":
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if parts == ["admin", "documents"]:
                return self.reply(
                    200,
                    {
                        "documents": [
                            {"id": "doc_a", "versions": [state.versions["ver_a"]]},
                            {"id": "doc_b", "versions": [state.versions["ver_b"]]},
                        ]
                    },
                )
            vid, kind = parts[2], parts[3]
            if kind == "corrections":
                items = state.corrections[vid]
                if method == "GET":
                    shown = [] if state.hide_from_get else items
                    return self.reply(200, {"version_id": vid, "corrections": shown})
                if state.fail_post_with:
                    return self.reply(state.fail_post_with, {"error": {"code": "boom"}})
                if any(
                    c["active"]
                    and (c["page_number"], c["find_text"])
                    == (body["page_number"], body["find_text"])
                    for c in items
                ):
                    return self.reply(409, {"error": {"code": "duplicate_correction"}})
                item = dict(body, id=f"cor_{len(items)}", active=True)
                items.append(item)
                return self.reply(201, {"correction": item})
            if kind == "page-overrides":
                items = state.overrides[vid]
                if method == "GET":
                    return self.reply(200, {"version_id": vid, "overrides": items})
                item = dict(body, id=f"pov_{len(items)}", active=True)
                items.append(item)
                return self.reply(201, {"override": item})
            return self.reply(404, {"error": {"code": "not_found"}})

        def do_GET(self):
            self.handle_any("GET")

        def do_POST(self):
            self.handle_any("POST")

    return Handler


def seeds():
    corrections = {
        "documents": [
            {
                "document": "doc-a",
                "source_sha256": SHA_A,
                "corrections": [
                    {
                        "seq": 1,
                        "page_number": 1,
                        "find_text": "uno",
                        "replace_text": "UNO",
                        "reason": "r",
                    },
                    {
                        "seq": 2,
                        "page_number": 2,
                        "find_text": "dos",
                        "replace_text": "DOS",
                        "reason": "r",
                    },
                    {
                        "seq": 3,
                        "page_number": 3,
                        "find_text": "argo plaza",
                        "replace_text": "largo plazo",
                        "reason": "r",
                        "only_without_override": True,
                    },
                ],
            },
            {
                "document": "doc-b",
                "source_sha256": SHA_B,
                "corrections": [
                    {
                        "seq": 1,
                        "page_number": 1,
                        "find_text": "tres",
                        "replace_text": "TRES",
                        "reason": "r",
                    }
                ],
            },
        ]
    }
    overrides = {
        "overrides": [
            {
                "source_sha256": SHA_A,
                "page_number": 3,
                "text": "Página completa.",
                "reason": "r",
            }
        ]
    }
    return corrections, overrides


class SeedAdminTests(unittest.TestCase):
    def setUp(self):
        self.state = FakeAdmin()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.state))
        threading.Thread(
            target=self.server.serve_forever, args=(0.05,), daemon=True
        ).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.sleeps = []
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def client(self):
        return sa.Client(self.url, TOKEN, pause=0.5, sleep=self.sleeps.append)

    def run_seed(self, apply, use_overrides=True, ledger=None):
        corrections, overrides = seeds()
        return sa.run(
            self.client(),
            corrections,
            overrides,
            apply=apply,
            use_overrides=use_overrides,
            ledger=sa.Ledger(ledger),
        )

    def posts(self):
        return [p for m, p in self.state.log if m == "POST"]

    def test_dry_run_writes_nothing(self):
        summaries = self.run_seed(apply=False, use_overrides=None)
        self.assertEqual(self.posts(), [])
        a = summaries[0]
        self.assertTrue(a["override_decision_required"])
        self.assertEqual((a["corrections_planned"], a["overrides_planned"]), (2, 1))

    def test_apply_requires_override_decision(self):
        with self.assertRaisesRegex(sa.Stop, "override-verified"):
            self.run_seed(apply=True, use_overrides=None)
        self.assertEqual(self.posts(), [])

    def test_apply_then_idempotent_and_ledger(self):
        ledger = Path(self.tmp.name) / "ledger.jsonl"
        first = self.run_seed(apply=True, ledger=ledger)
        self.assertEqual(first[0]["overrides_created"], 1)
        self.assertEqual(first[0]["corrections_created"], 2)  # integrated one skipped
        self.assertEqual(first[1]["corrections_created"], 1)
        self.assertEqual(self.posts()[0], "/admin/versions/ver_a/page-overrides")
        second = self.run_seed(apply=True, ledger=ledger)
        self.assertEqual(
            [s["corrections_created"] + s["overrides_created"] for s in second], [0, 0]
        )
        self.assertEqual(second[0]["corrections_existing"], 2)
        self.assertEqual(second[0]["overrides_existing"], 1)
        lines = ledger.read_text().splitlines()
        self.assertEqual(len(lines), 4)
        self.assertNotIn(TOKEN, ledger.read_text())
        self.assertNotIn("uno", ledger.read_text())  # no page text in the ledger
        self.assertTrue(all(s == 0.5 for s in self.sleeps))  # paced

    def test_no_overrides_registers_integrated_correction(self):
        summaries = self.run_seed(apply=True, use_overrides=False)
        self.assertEqual(summaries[0]["corrections_created"], 3)
        self.assertEqual(self.state.overrides["ver_a"], [])

    def test_duplicate_409_counts_as_registered(self):
        self.run_seed(apply=True)
        self.state.hide_from_get = True
        summaries = self.run_seed(apply=True)
        self.assertEqual(summaries[0]["corrections_created"], 0)
        self.assertEqual(summaries[0]["corrections_existing"], 2)

    def test_resolution_by_sha_and_r2_key(self):
        summaries = self.run_seed(apply=False)
        self.assertEqual([s["version_id"] for s in summaries], ["ver_a", "ver_b"])
        del self.state.versions["ver_b"]["source_r2_key"]
        with self.assertRaisesRegex(sa.Stop, "0 versions"):
            self.run_seed(apply=False)

    def test_unexpected_status_stops(self):
        self.state.fail_post_with = 500
        with self.assertRaisesRegex(sa.Stop, "500"):
            self.run_seed(apply=True, use_overrides=False)

    def test_different_active_override_stops(self):
        self.state.overrides["ver_a"].append(
            {"page_number": 3, "text": "Otro texto", "active": True, "id": "pov_x"}
        )
        with self.assertRaisesRegex(sa.Stop, "different override"):
            self.run_seed(apply=True)

    def test_busy_or_published_version_stops(self):
        self.state.versions["ver_a"]["status"] = "processing"
        with self.assertRaisesRegex(sa.Stop, "wait for review_required"):
            self.run_seed(apply=False)
        self.state.versions["ver_a"]["status"] = "published"
        with self.assertRaisesRegex(sa.Stop, "published"):
            self.run_seed(apply=False)

    def test_429_retried_once_after_retry_after(self):
        self.state.rate_limit_once = True
        self.run_seed(apply=False)
        self.assertIn(2.0, self.sleeps)

    def test_https_required_except_localhost(self):
        with self.assertRaises(sa.Stop):
            sa.Client("http://example.org", TOKEN)
        self.assertNotIn(TOKEN, repr(self.client()))

    def test_cli_dry_run_never_prints_token(self):
        seed_dir = Path(self.tmp.name)
        corrections, overrides = seeds()
        (seed_dir / "corrections.json").write_text(json.dumps(corrections))
        (seed_dir / "page_overrides.json").write_text(json.dumps(overrides))
        env_file = seed_dir / "admin.env"
        env_file.write_text(f"export ADMIN_TOKEN='{TOKEN}'\n")
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, {}, clear=False),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            os.environ.pop("ADMIN_TOKEN", None)
            code = sa.main(
                [
                    "--base-url",
                    self.url,
                    "--env-file",
                    str(env_file),
                    "--seed-dir",
                    str(seed_dir),
                    "--pause",
                    "0",
                ]
            )
        self.assertEqual(code, 0)
        self.assertNotIn(TOKEN, out.getvalue() + err.getvalue())
        self.assertIn("Dry-run", out.getvalue())
        self.assertEqual(self.posts(), [])
        self.assertFalse((seed_dir / "ledger.jsonl").exists())

    def test_cli_missing_token_stops(self):
        err = io.StringIO()
        with (
            patch.dict(os.environ, {"ADMIN_TOKEN": ""}),
            contextlib.redirect_stderr(err),
        ):
            code = sa.main(["--base-url", self.url, "--seed-dir", self.tmp.name])
        self.assertEqual(code, 1)
        self.assertIn("ADMIN_TOKEN", err.getvalue())


if __name__ == "__main__":
    unittest.main()

"""Alternative OCR pilot: guards, synthetic parsing, fake-transport polling, export.

No network, no Docker, no credentials: every fixture is synthetic and built
from the documented response shapes (no institutional text).
"""

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
import alt_ocr as a

KEY = "k" * 32
ENDPOINT = "https://pilot-resource.cognitiveservices.azure.com"
OPERATION = "3b31320d-8bab-4f88-b19c-2322a7f11034"
LOCATION = (
    f"{ENDPOINT}/documentintelligence/documentModels/prebuilt-layout/"
    f"analyzeResults/{OPERATION}?api-version=2024-11-30"
)
SETTINGS = {"key": KEY, "endpoint": ENDPOINT}
CONTENT = "# Titulo de prueba\n\n<table><tr><th>A</th></tr><tr><td>1</td></tr></table>"


def azure_body(content=CONTENT, status="succeeded", fmt="markdown", spans=True):
    """Synthetic analyzeResult following the documented 2024-11-30 shape."""
    page = {
        "pageNumber": 1,
        "words": [
            {
                "content": "Titulo",
                "confidence": 0.99,
                "span": {"offset": 2, "length": 6},
            },
            {
                "content": "prueba",
                "confidence": 0.42,
                "span": {"offset": 12, "length": 6},
            },
        ],
    }
    if spans:
        page["spans"] = [{"offset": 0, "length": len(content)}]
    return {
        "status": status,
        "createdDateTime": "2026-01-01T00:00:00Z",
        "analyzeResult": {
            "apiVersion": "2024-11-30",
            "modelId": "prebuilt-layout",
            "stringIndexType": "unicodeCodePoint",
            "contentFormat": fmt,
            "content": content,
            "pages": [page],
        },
    }


def mistral_body(markdown="Texto de prueba\n\n[tbl-0.html](tbl-0.html)"):
    """Synthetic OCR response with table_format=html and word confidence."""
    return {
        "model": "mistral-ocr-2512",
        "pages": [
            {
                "index": 0,
                "markdown": markdown,
                "tables": [
                    {
                        "id": "tbl-0.html",
                        "content": "<table><tr><td>x</td></tr></table>",
                        "format": "html",
                        "word_confidence_scores": [
                            {"text": "x", "confidence": 0.7, "start_index": 0}
                        ],
                    }
                ],
                "confidence_scores": {
                    "average_page_confidence_score": 0.9,
                    "minimum_page_confidence_score": 0.5,
                    "word_confidence_scores": [
                        {"text": "Texto", "confidence": 0.95, "start_index": 0},
                        {"text": "prueba", "confidence": 0.5, "start_index": 9},
                    ],
                },
            }
        ],
        "usage_info": {"pages_processed": 1, "doc_size_bytes": 10},
    }


class FakeResponse:
    """Minimal urllib-like response."""

    def __init__(self, status, body=b"", headers=None):
        self.status = status
        self.body = body
        self.headers = headers or {}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def js(value):
    return json.dumps(value).encode()


class FakeTransport:
    """Records requests and replays scripted replies (or exceptions)."""

    def __init__(self, replies, on_call=None):
        self.replies = list(replies)
        self.requests = []
        self.on_call = on_call

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.on_call:
            self.on_call(request)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


class Clock:
    """Deterministic clock advanced by the fake sleep."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Base(unittest.TestCase):
    """Isolated manifest, fragments and sources with patched pins."""

    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root, self.repo = tmp / "root", tmp / "repo"
        (self.root / "pages").mkdir(parents=True)
        (self.repo / "documents").mkdir(parents=True)
        pages, pins, sources = [], [], {}
        for number, slug in ((1, "doc-a"), (2, "doc-b")):
            source = f"documents/{slug}.pdf"
            source_data = b"%PDF-source-" + slug.encode()
            (self.repo / source).write_bytes(source_data)
            sources[slug] = (source, a.digest(source_data))
            page_id = f"0{number}-{slug}-p0{number}"
            data = b"%PDF-fragment-" + page_id.encode()
            (self.root / "pages" / f"{page_id}.pdf").write_bytes(data)
            pages.append(
                {
                    "page_id": page_id,
                    "slug": slug,
                    "source_path": source,
                    "source_sha256": a.digest(source_data),
                    "physical_page": number,
                    "file": f"pages/{page_id}.pdf",
                    "sha256": a.digest(data),
                    "bytes": len(data),
                }
            )
            pins.append((page_id, a.digest(data), len(data), number))
        manifest = {
            "state": "prepared_offline_not_sent",
            "image": a.IMAGE,
            "total_pages": 2,
            "pages": pages,
        }
        (self.root / "manifest.json").write_text(json.dumps(manifest))
        self.pages = pages
        for name, value in (
            ("MANIFEST_SHA256", a.digest((self.root / "manifest.json").read_bytes())),
            ("PAGE_PINS", tuple(pins)),
            ("SOURCES", sources),
            ("TOTAL_PAGES", 2),
        ):
            self.enterContext(patch.object(a, name, value))
        self.clock = Clock()

    def send(self, provider, transport, **kwargs):
        settings = SETTINGS if provider == "azure" else {"key": KEY}
        kwargs.setdefault("confirm_pages", 2)
        with contextlib.redirect_stderr(io.StringIO()):
            return a.send(
                provider,
                settings,
                transport=transport,
                root=self.root,
                repo=self.repo,
                clock=self.clock,
                sleep=self.clock.sleep,
                **kwargs,
            )

    def folder(self, provider, index=0):
        return self.root / "execution" / provider / self.pages[index]["page_id"]

    def result(self, provider, index=0):
        return json.loads((self.folder(provider, index) / "result.json").read_text())


def azure_ok_replies(pages=2):
    replies = []
    for _ in range(pages):
        replies += [
            FakeResponse(
                202, b"", {"Operation-Location": LOCATION, "Retry-After": "2"}
            ),
            FakeResponse(200, js(azure_body(status="running")), {"Retry-After": "2"}),
            FakeResponse(200, js(azure_body())),
        ]
    return replies


class GuardTests(Base):
    def test_send_without_confirmation_flag_is_refused(self):
        transport = FakeTransport([])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            a.main(["send", "--provider", "mistral"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            a.main(["send", "--provider", "mistral", "--confirm-pages", "9"])
        self.assertEqual(1, self.send("mistral", transport, confirm_pages=1))
        self.assertEqual([], transport.requests)
        self.assertFalse((self.root / "execution").exists())

    def test_disallowed_endpoints_are_rejected(self):
        for bad in (
            "http://x.cognitiveservices.azure.com",
            "https://x.cognitiveservices.azure.com:8443",
            "https://x.cognitiveservices.azure.com/extra",
            "https://x.cognitiveservices.azure.com.evil.net",
            "https://user@x.cognitiveservices.azure.com",
            "https://x.openai.azure.com",
            "https://evil.net/.cognitiveservices.azure.com",
            "https://-x.cognitiveservices.azure.com",
            "https://X.cognitiveservices.azure.com",
            "",
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                a.validate_endpoint(bad)
        self.assertEqual(ENDPOINT, a.validate_endpoint(ENDPOINT + "/"))
        transport = FakeTransport([])
        with contextlib.redirect_stderr(io.StringIO()):
            code = a.send(
                "azure",
                {"key": KEY, "endpoint": "https://evil.net"},
                confirm_pages=2,
                transport=transport,
                root=self.root,
                repo=self.repo,
            )
        self.assertEqual(1, code)
        self.assertEqual([], transport.requests)

    def test_tampered_fragment_manifest_or_source_stops_before_http(self):
        fragment = self.root / self.pages[0]["file"]
        original = fragment.read_bytes()
        for label, change in (
            ("fragment", lambda: fragment.write_bytes(original + b"x")),
            (
                "manifest",
                lambda: (self.root / "manifest.json").write_text("{}"),
            ),
            (
                "source",
                lambda: (self.repo / self.pages[0]["source_path"]).write_bytes(b"x"),
            ),
        ):
            with self.subTest(label):
                transport = FakeTransport([])
                change()
                self.assertEqual(1, self.send("mistral", transport))
                self.assertEqual([], transport.requests)
                self.assertFalse((self.root / "execution").exists())

    def test_existing_ledger_folder_blocks_everything(self):
        self.folder("mistral").mkdir(parents=True)
        transport = FakeTransport([])
        self.assertEqual(1, self.send("mistral", transport))
        self.assertEqual([], transport.requests)

    def test_ledger_is_durable_before_every_http_call(self):
        seen = []

        def check(request):
            seen.append(
                (self.folder("mistral") / "attempt.json").exists()
                or (self.folder("mistral", 1) / "attempt.json").exists()
            )

        transport = FakeTransport(
            [FakeResponse(200, js(mistral_body())) for _ in range(2)], on_call=check
        )
        self.assertEqual(0, self.send("mistral", transport))
        self.assertEqual([True, True], seen)

    def test_uncertainty_stops_and_blocks_replay(self):
        transport = FakeTransport([urllib.error.URLError(TimeoutError())])
        self.assertEqual(1, self.send("mistral", transport))
        self.assertEqual(1, len(transport.requests))
        self.assertEqual("uncertain_no_replay", self.result("mistral")["state"])
        self.assertFalse(self.folder("mistral", 1).exists())
        again = FakeTransport([])
        self.assertEqual(1, self.send("mistral", again))
        self.assertEqual([], again.requests)

    def test_http_error_status_is_never_complete(self):
        transport = FakeTransport(
            [FakeResponse(429, b'{"message":"rate"}', {"Retry-After": "1"})]
        )
        self.assertEqual(1, self.send("mistral", transport))
        result = self.result("mistral")
        self.assertEqual(429, result["http_status"])
        self.assertEqual("http_error", result["category"])
        self.assertEqual(1, len(transport.requests))

    def test_credentials_never_written_to_the_ledger(self):
        transport = FakeTransport(azure_ok_replies())
        self.assertEqual(0, self.send("azure", transport))
        for path in self.root.rglob("*"):
            if path.is_file():
                blob = path.read_bytes()
                self.assertNotIn(KEY.encode(), blob)
                self.assertNotIn(b"pilot-resource", blob)

    def test_f0_refuses_fragments_over_the_limit(self):
        with patch.object(a, "F0_MAX_FRAGMENT_BYTES", 5):
            transport = FakeTransport([])
            self.assertEqual(1, self.send("azure", transport))
            self.assertEqual([], transport.requests)

    def test_second_run_skips_complete_pages(self):
        first = FakeTransport([FakeResponse(200, js(mistral_body())) for _ in range(2)])
        self.assertEqual(0, self.send("mistral", first))
        second = FakeTransport([])
        self.assertEqual(0, self.send("mistral", second))
        self.assertEqual([], second.requests)


class AzureFlowTests(Base):
    def test_requests_follow_the_documented_contract(self):
        transport = FakeTransport(azure_ok_replies(1) + azure_ok_replies(1))
        self.assertEqual(0, self.send("azure", transport))
        post, get = transport.requests[0], transport.requests[1]
        self.assertEqual("POST", post.get_method())
        self.assertTrue(
            post.full_url.startswith(
                f"{ENDPOINT}/documentintelligence/documentModels/prebuilt-layout:analyze?"
            )
        )
        for part in (
            "api-version=2024-11-30",
            "outputContentFormat=markdown",
            "stringIndexType=unicodeCodePoint",
        ):
            self.assertIn(part, post.full_url)
        self.assertEqual(KEY, post.get_header("Ocp-apim-subscription-key"))
        self.assertEqual(["base64Source"], list(json.loads(post.data)))
        self.assertEqual("GET", get.get_method())
        self.assertEqual(
            f"{ENDPOINT}/documentintelligence/documentModels/prebuilt-layout/"
            f"analyzeResults/{OPERATION}?api-version=2024-11-30",
            get.full_url,
        )
        self.assertEqual("complete", self.result("azure")["state"])
        self.assertEqual(
            "markdown",
            self.result("azure")["content_format"],
        )
        self.assertTrue((self.folder("azure") / "operation.json").exists())
        self.assertTrue((self.folder("azure") / "response.raw").exists())

    def test_polling_honors_retry_after_via_sleep(self):
        transport = FakeTransport(azure_ok_replies(2))
        self.assertEqual(0, self.send("azure", transport))
        self.assertGreaterEqual(self.clock.now, 2 * 2 * 2)

    def test_failed_operation_is_recorded_and_stops(self):
        transport = FakeTransport(
            [
                FakeResponse(202, b"", {"Operation-Location": LOCATION}),
                FakeResponse(200, js(azure_body(status="failed"))),
            ]
        )
        self.assertEqual(1, self.send("azure", transport))
        self.assertEqual("operation_failed", self.result("azure")["category"])
        self.assertEqual(2, len(transport.requests))
        self.assertFalse(self.folder("azure", 1).exists())

    def test_polling_timeout_is_uncertain_without_replay(self):
        running = FakeResponse(
            200, js(azure_body(status="running")), {"Retry-After": "5"}
        )
        transport = FakeTransport(
            [FakeResponse(202, b"", {"Operation-Location": LOCATION})] + [running] * 200
        )
        self.assertEqual(1, self.send("azure", transport))
        result = self.result("azure")
        self.assertEqual("timeout", result["category"])
        self.assertNotEqual("complete", result["state"])
        posts = [r for r in transport.requests if r.get_method() == "POST"]
        self.assertEqual(1, len(posts))
        again = FakeTransport([])
        self.assertEqual(1, self.send("azure", again))
        self.assertEqual([], again.requests)

    def test_operation_location_on_another_host_is_never_followed(self):
        for bad in (
            LOCATION.replace("pilot-resource", "evil"),
            LOCATION.replace("https://", "http://"),
            LOCATION.replace("prebuilt-layout/analyzeResults", "x/analyzeResults"),
            LOCATION + "&extra=1",
            None,
        ):
            with self.subTest(bad=bad), self.assertRaises(a.Stop):
                a.operation_id(bad, ENDPOINT)
        transport = FakeTransport(
            [FakeResponse(202, b"", {"Operation-Location": "https://evil.net/x"})]
        )
        self.assertEqual(1, self.send("azure", transport))
        self.assertEqual(1, len(transport.requests))

    def test_post_status_other_than_202_stops(self):
        transport = FakeTransport([FakeResponse(401, b'{"error":{"code":"401"}}')])
        self.assertEqual(1, self.send("azure", transport))
        self.assertEqual("http_error", self.result("azure")["category"])
        self.assertEqual(1, len(transport.requests))

    def test_polling_error_status_is_uncertain(self):
        transport = FakeTransport(
            [
                FakeResponse(202, b"", {"Operation-Location": LOCATION}),
                FakeResponse(429, b"{}"),
            ]
        )
        self.assertEqual(1, self.send("azure", transport))
        self.assertEqual("http_error", self.result("azure")["category"])

    def test_invalid_succeeded_payload_is_not_complete(self):
        bad = azure_body()
        bad["analyzeResult"]["pages"] = []
        transport = FakeTransport(
            [
                FakeResponse(202, b"", {"Operation-Location": LOCATION}),
                FakeResponse(200, js(bad)),
            ]
        )
        self.assertEqual(1, self.send("azure", transport))
        self.assertEqual("invalid_result", self.result("azure")["category"])
        self.assertTrue((self.folder("azure") / "response.raw").exists())


class ParsingTests(unittest.TestCase):
    def test_azure_page_returns_markdown_with_html_table_and_confidence(self):
        markdown, confidence = a.azure_page(azure_body())
        self.assertEqual(CONTENT, markdown)
        self.assertIn("<table>", markdown)
        self.assertEqual(0.42, confidence["min"])
        self.assertEqual(2, len(confidence["words"]))
        self.assertAlmostEqual(0.705, confidence["mean"])

    def test_azure_page_cuts_content_by_spans(self):
        body = azure_body(content="uno\n\ndos")
        body["analyzeResult"]["pages"][0]["spans"] = [{"offset": 5, "length": 3}]
        self.assertEqual("dos", a.azure_page(body)[0])
        body["analyzeResult"]["pages"][0]["spans"] = [{"offset": 5, "length": 9}]
        with self.assertRaises(ValueError):
            a.azure_page(body)

    def test_azure_page_rejects_plain_text_format(self):
        with self.assertRaises(ValueError):
            a.azure_page(azure_body(fmt="text"))

    def test_mistral_page_replaces_table_placeholders_and_keeps_confidence(self):
        markdown, confidence = a.mistral_page(mistral_body())
        self.assertIn("<table><tr><td>x</td></tr></table>", markdown)
        self.assertNotIn("tbl-0.html", markdown)
        self.assertEqual(0.5, confidence["minimum_page_confidence_score"])
        self.assertEqual(2, len(confidence["words"]))
        self.assertIn("tbl-0.html", confidence["table_words"])

    def test_mistral_page_requires_exactly_one_page(self):
        body = mistral_body()
        body["pages"].append(body["pages"][0])
        with self.assertRaises(a.Stop):
            a.mistral_page(body)


class ExportTests(Base):
    def complete(self, provider, body):
        self.assertEqual(
            0,
            self.send(
                provider,
                FakeTransport(
                    azure_ok_replies(2)
                    if provider == "azure"
                    else [FakeResponse(200, js(body)) for _ in range(2)]
                ),
            ),
        )

    def parser(self):
        sys.path.insert(0, str(a.REPO / "workers/ingestion/src"))
        self.addCleanup(sys.path.remove, str(a.REPO / "workers/ingestion/src"))
        from unitropico_ingestion.services.ocr_parser import parse_ocr_response

        return parse_ocr_response

    def test_export_is_accepted_by_parse_ocr_response_per_document(self):
        parse = self.parser()
        for provider, body in (("azure", None), ("mistral", mistral_body())):
            with self.subTest(provider):
                self.complete(provider, body)
                summary = a.export(provider, self.root)
                out = self.root / "export" / provider
                self.assertEqual(2, len(summary["pages"]))
                for page in self.pages:
                    raw = (out / f"{page['slug']}.json").read_bytes()
                    result = parse(raw)
                    self.assertEqual(
                        [page["physical_page"]], [p.page_number for p in result.pages]
                    )
                    self.assertTrue(result.pages[0].markdown.strip())
                combined = json.loads((out / "pages.json").read_text())
                self.assertEqual("alt-ocr-pilot-export/v1", combined["schema"])
                self.assertEqual({1, 2}, {p["pageNumber"] for p in combined["pages"]})
                self.assertIn("confidence", combined["pages"][0])

    def test_export_refuses_incomplete_pilot_and_never_overwrites(self):
        with self.assertRaises(ValueError):
            a.export("azure", self.root)
        self.assertFalse((self.root / "export").exists())
        self.complete("mistral", mistral_body())
        a.export("mistral", self.root)
        with self.assertRaises(FileExistsError):
            a.export("mistral", self.root)

    def test_status_is_offline_and_reports_cost(self):
        self.complete("mistral", mistral_body())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(0, a.status(self.root))
        text = out.getvalue()
        self.assertIn("mistral: 2 complete, 0 uncertain", text)
        self.assertIn("0.008 USD", text)
        self.assertIn("azure: 0 complete", text)


class CredentialTests(unittest.TestCase):
    def test_environment_and_env_file(self):
        env = {"AZURE_DOCINT_ENDPOINT": ENDPOINT, "AZURE_DOCINT_KEY": KEY}
        self.assertEqual(
            {"key": KEY, "endpoint": ENDPOINT}, a.load_settings("azure", environ=env)
        )
        with self.assertRaises(ValueError):
            a.load_settings("mistral", environ={})
        with self.assertRaises(ValueError):
            a.load_settings(
                "azure", environ=dict(env, AZURE_DOCINT_ENDPOINT="https://evil.net")
            )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "env"
            path.write_text(
                f'export AZURE_DOCINT_ENDPOINT="{ENDPOINT}"\nAZURE_DOCINT_KEY={KEY}\n'
                "OTHER=$(rm -rf /)\n"
            )
            self.assertEqual(
                {"key": KEY, "endpoint": ENDPOINT}, a.load_settings("azure", path)
            )
            for bad in (
                f"MISTRAL_API_KEY=$(echo {KEY})\n",
                f"MISTRAL_API_KEY={KEY}\nMISTRAL_API_KEY={KEY}\n",
                "MISTRAL_API_KEY=short\n",
                "OTHER=1\n",
            ):
                path.write_text(bad)
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    a.load_settings("mistral", path)


class ManifestPinTests(unittest.TestCase):
    @unittest.skipUnless((a.ROOT / "manifest.json").exists(), "not prepared")
    def test_real_manifest_matches_the_pins(self):
        self.assertEqual(10, len(a.preflight()))

    def test_pins_cover_the_ten_pilot_pages(self):
        self.assertEqual(10, len(a.PAGE_PINS))
        self.assertEqual(
            [3, 12, 13, 14, 16, 18, 20, 22, 54, 38], [p[3] for p in a.PAGE_PINS]
        )
        self.assertEqual(len(a.PAGE_PINS), len({p[0] for p in a.PAGE_PINS}))


if __name__ == "__main__":
    unittest.main()

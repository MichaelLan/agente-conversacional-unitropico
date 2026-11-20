"""Azure OCR v2: manifest pins, guards, exact confirmation, export metrics.

No network, no Docker, no credentials; fixtures are synthetic.
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
from test_alt_ocr import (
    ENDPOINT,
    KEY,
    LOCATION,
    SETTINGS,
    Clock,
    FakeResponse,
    FakeTransport,
    js,
)

import alt_ocr as a
import alt_ocr_v2 as v
import prepare_alt_v2 as prep

CONTENT = "Alfa beta gamma delta"


def words(confidences):
    """Words of CONTENT, one per confidence value, with their spans."""
    out, offset = [], 0
    for text, confidence in zip(CONTENT.split(), confidences, strict=False):
        offset = CONTENT.index(text, offset)
        out.append(
            {
                "content": text,
                "confidence": confidence,
                "span": {"offset": offset, "length": len(text)},
            }
        )
    return out


def body(confidences=(0.99, 0.99, 0.99, 0.99), status="succeeded"):
    return {
        "status": status,
        "analyzeResult": {
            "contentFormat": "markdown",
            "content": CONTENT,
            "pages": [
                {
                    "pageNumber": 1,
                    "spans": [{"offset": 0, "length": len(CONTENT)}],
                    "words": words(confidences),
                }
            ],
        },
    }


def ok_replies(pages, bodies=None):
    replies = []
    for i in range(pages):
        replies += [
            FakeResponse(
                202, b"", {"Operation-Location": LOCATION, "Retry-After": "2"}
            ),
            FakeResponse(200, js(bodies[i] if bodies else body())),
        ]
    return replies


class Base(unittest.TestCase):
    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root, self.repo = tmp / "root", tmp / "repo"
        (self.root / "pages").mkdir(parents=True)
        (self.repo / "documents").mkdir(parents=True)
        pages, pins, sources = [], [], {}
        for number, slug, physical in (
            (5, "doc-a", 4),
            (6, "doc-a", 9),
            (7, "doc-b", 2),
        ):
            source = f"documents/{slug}.pdf"
            source_data = b"%PDF-source-" + slug.encode()
            (self.repo / source).write_bytes(source_data)
            sources[slug] = (source, a.digest(source_data))
            page_id = f"0{number}-{slug}-p0{physical}"
            data = b"%PDF-fragment-" + page_id.encode()
            (self.root / "pages" / f"{page_id}.pdf").write_bytes(data)
            pages.append(
                {
                    "page_id": page_id,
                    "slug": slug,
                    "source_path": source,
                    "source_sha256": a.digest(source_data),
                    "physical_page": physical,
                    "file": f"pages/{page_id}.pdf",
                    "sha256": a.digest(data),
                    "bytes": len(data),
                }
            )
            pins.append((page_id, a.digest(data), len(data), physical))
        manifest = {
            "campaign": "alt-ocr-v2",
            "state": "prepared_offline_not_sent",
            "image": a.IMAGE,
            "total_pages": 3,
            "pages": pages,
        }
        (self.root / "manifest.json").write_text(json.dumps(manifest))
        self.pages = pages
        plan = [{"page_id": p["page_id"]} for p in pages]
        for target, name, value in (
            (
                v,
                "MANIFEST_SHA256",
                a.digest((self.root / "manifest.json").read_bytes()),
            ),
            (v, "PAGE_PINS", tuple(pins)),
            (v, "SOURCES", sources),
            (v, "TOTAL_PAGES", 3),
            (v, "plan_pages", lambda: plan),
        ):
            self.enterContext(patch.object(target, name, value))
        self.clock = Clock()

    def send(self, transport, **kwargs):
        kwargs.setdefault("confirm_pages", 3)
        with contextlib.redirect_stderr(io.StringIO()):
            return v.send(
                SETTINGS,
                transport=transport,
                root=self.root,
                repo=self.repo,
                clock=self.clock,
                sleep=self.clock.sleep,
                **kwargs,
            )

    def folder(self, index=0):
        return self.root / "execution" / "azure" / self.pages[index]["page_id"]

    def complete_all(self, bodies=None):
        self.assertEqual(0, self.send(FakeTransport(ok_replies(3, bodies))))


class PlanTests(unittest.TestCase):
    def test_plan_is_69_distinct_pages_outside_the_pilot(self):
        pages = prep.plan_pages()
        self.assertEqual(69, len(pages))
        self.assertEqual(69, len({p["page_id"] for p in pages}))
        pilot = {(slug, n) for _, slug, ns in prep.PILOT for n in ns}
        self.assertFalse({(p["slug"], p["physical_page"]) for p in pages} & pilot)
        counts = {}
        for page in pages:
            counts[page["slug"]] = counts.get(page["slug"], 0) + 1
        self.assertEqual(
            {
                "documento-tecnico": 6,
                "manual-m-ip-36": 14,
                "res-00137": 12,
                "convocatoria-2026-1": 17,
                "politica-bienestar": 20,
            },
            counts,
        )

    def test_pinned_constants_cover_the_plan(self):
        self.assertEqual(
            [p["page_id"] for p in prep.plan_pages()], [pin[0] for pin in v.PAGE_PINS]
        )
        self.assertEqual(64, len(v.MANIFEST_SHA256))
        self.assertTrue(all(pin[2] <= a.F0_MAX_FRAGMENT_BYTES for pin in v.PAGE_PINS))


class GuardTests(Base):
    def test_confirm_pages_must_equal_manifest_count(self):
        for wrong in (1, 2, 4, 69):
            transport = FakeTransport([])
            self.assertEqual(1, self.send(transport, confirm_pages=wrong))
            self.assertEqual([], transport.requests)
        self.assertFalse((self.root / "execution").exists())
        for argv in (["send"], ["send", "--confirm-pages", "2"]):
            with (
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                v.main(argv)

    def test_tampered_manifest_fragment_or_source_stops_before_http(self):
        fragment = self.root / self.pages[0]["file"]
        original = fragment.read_bytes()
        for label, change in (
            ("fragment", lambda: fragment.write_bytes(original + b"x")),
            ("manifest", lambda: (self.root / "manifest.json").write_text("{}")),
            (
                "source",
                lambda: (self.repo / self.pages[0]["source_path"]).write_bytes(b"x"),
            ),
        ):
            with self.subTest(label):
                transport = FakeTransport([])
                change()
                self.assertEqual(1, self.send(transport))
                self.assertEqual([], transport.requests)
                self.assertFalse((self.root / "execution").exists())

    def test_wrong_campaign_state_or_page_order_is_rejected(self):
        manifest = json.loads((self.root / "manifest.json").read_text())
        for key, value in (("campaign", "alt-ocr-pilot"), ("total_pages", 4)):
            with self.subTest(key):
                (self.root / "manifest.json").write_text(
                    json.dumps(dict(manifest, **{key: value}))
                )
                with (
                    patch.object(
                        v,
                        "MANIFEST_SHA256",
                        a.digest((self.root / "manifest.json").read_bytes()),
                    ),
                    self.assertRaises(ValueError),
                ):
                    v.load_manifest(self.root)

    def test_bad_endpoint_stops_before_ledger(self):
        transport = FakeTransport([])
        with contextlib.redirect_stderr(io.StringIO()):
            code = v.send(
                {"key": KEY, "endpoint": "https://evil.net"},
                confirm_pages=3,
                transport=transport,
                root=self.root,
                repo=self.repo,
            )
        self.assertEqual(1, code)
        self.assertEqual([], transport.requests)
        self.assertFalse((self.root / "execution").exists())

    def test_ledger_is_durable_before_http_and_records_v2_sha(self):
        seen = []

        def check(request):
            seen.append(
                any((self.folder(i) / "attempt.json").exists() for i in range(3))
            )

        self.assertEqual(0, self.send(FakeTransport(ok_replies(3), on_call=check)))
        self.assertTrue(all(seen) and len(seen) == 6)
        attempt = json.loads((self.folder(0) / "attempt.json").read_text())
        self.assertEqual(v.MANIFEST_SHA256, attempt["manifest_sha256"])
        self.assertEqual("azure", attempt["provider"])

    def test_uncertainty_blocks_replay_and_resume_polls_only(self):
        transport = FakeTransport(
            [
                FakeResponse(
                    202, b"", {"Operation-Location": LOCATION, "Retry-After": "2"}
                ),
                urllib.error.URLError(TimeoutError()),
            ]
        )
        self.assertEqual(1, self.send(transport))
        self.assertEqual(2, len(transport.requests))
        again = FakeTransport([])
        self.assertEqual(1, self.send(again))
        self.assertEqual([], again.requests)
        resume = FakeTransport([FakeResponse(200, js(body()))])
        with contextlib.redirect_stderr(io.StringIO()):
            code = v.resume(
                SETTINGS,
                transport=resume,
                root=self.root,
                clock=self.clock,
                sleep=self.clock.sleep,
            )
        self.assertEqual(0, code)
        self.assertEqual(["GET"], [r.get_method() for r in resume.requests])
        rest = FakeTransport(ok_replies(2))
        self.assertEqual(0, self.send(rest))
        self.assertEqual(2, sum(r.get_method() == "POST" for r in rest.requests))

    def test_f0_pacing_and_429_on_polling_with_retry_after(self):
        times = []
        replies = [
            FakeResponse(
                202, b"", {"Operation-Location": LOCATION, "Retry-After": "2"}
            ),
            FakeResponse(429, b"{}", {"Retry-After": "7"}),
            FakeResponse(200, js(body())),
        ] + ok_replies(2)
        transport = FakeTransport(
            replies, on_call=lambda r: times.append(self.clock.now)
        )
        self.assertEqual(0, self.send(transport))
        self.assertTrue(
            all(b - a_ >= 3.5 for a_, b in zip(times, times[1:], strict=False))
        )
        self.assertGreaterEqual(times[2] - times[1], 7)
        self.assertEqual(3, sum(r.get_method() == "POST" for r in transport.requests))

    def test_post_429_is_uncertain_and_stops(self):
        transport = FakeTransport([FakeResponse(429, b"{}", {"Retry-After": "1"})])
        self.assertEqual(1, self.send(transport))
        self.assertEqual(1, len(transport.requests))
        result = json.loads((self.folder(0) / "result.json").read_text())
        self.assertNotEqual("complete", result["state"])
        self.assertFalse(self.folder(1).exists())

    def test_status_is_offline_and_azure_only(self):
        self.complete_all()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(0, v.status(self.root))
        text = out.getvalue()
        self.assertIn("azure: 3 complete, 0 uncertain, 0 not started", text)
        self.assertNotIn("mistral", text)


class MetricsTests(unittest.TestCase):
    def metrics(self, confidences):
        return v.page_metrics(body(confidences)["analyzeResult"]["pages"][0])[0]

    def test_review_rule_boundaries(self):
        self.assertFalse(self.metrics((0.99, 0.98, 0.97, 0.96))["needs_full_review"])
        low_mean = self.metrics((0.92, 0.92, 0.92, 0.92))
        self.assertAlmostEqual(0.92, low_mean["mean"])
        self.assertTrue(low_mean["needs_full_review"])
        # 1 of 4 words below 0.8 is 25 % > 10 %, even with a high mean.
        many_low = self.metrics((1.0, 1.0, 1.0, 0.79))
        self.assertGreater(many_low["mean"], 0.93)
        self.assertEqual(25.0, many_low["pct_below_0_8"])
        self.assertTrue(many_low["needs_full_review"])
        # Exactly 10 % is not "more than 10 %".
        ten = v.page_metrics(
            {
                "words": [
                    {"content": "w", "confidence": 0.5 if i == 0 else 1.0}
                    for i in range(10)
                ]
            }
        )[0]
        self.assertEqual(10.0, ten["pct_below_0_8"])
        self.assertFalse(ten["needs_full_review"])
        self.assertTrue(v.page_metrics({"words": []})[0]["needs_full_review"])

    def test_low_confidence_words_have_text_and_offset_only(self):
        metrics = self.metrics((0.99, 0.85, 0.5, 0.9))
        self.assertEqual(50.0, metrics["pct_below_0_9"])
        self.assertEqual(
            [
                {"text": "beta", "confidence": 0.85, "offset": 5},
                {"text": "gamma", "confidence": 0.5, "offset": 10},
            ],
            metrics["low_confidence_words"],
        )

    def test_offsets_are_relative_to_the_page_markdown(self):
        page = {
            "spans": [{"offset": 100, "length": 10}],
            "words": [
                {
                    "content": "x",
                    "confidence": 0.1,
                    "span": {"offset": 104, "length": 1},
                },
                {"content": "y", "confidence": 0.1, "span": {"offset": 5, "length": 1}},
            ],
        }
        listed = v.page_metrics(page)[1]
        self.assertEqual([4, None], [w["offset"] for w in listed])


class ExportTests(Base):
    def parser(self):
        sys.path.insert(0, str(a.REPO / "workers/ingestion/src"))
        self.addCleanup(sys.path.remove, str(a.REPO / "workers/ingestion/src"))
        from unitropico_ingestion.services.ocr_parser import parse_ocr_response

        return parse_ocr_response

    def test_export_per_document_with_metrics_flag_and_parser_compat(self):
        parse = self.parser()
        bodies = [body(), body((1.0, 1.0, 1.0, 0.5)), body((0.99, 0.95, 0.9, 0.99))]
        self.complete_all(bodies)
        summary = v.export(self.root)
        out = self.root / "export"
        self.assertEqual([self.pages[1]["page_id"]], summary["flagged_pages"])
        self.assertEqual("alt-ocr-v2-export/v1", summary["schema"])
        by_doc = {"doc-a": [4, 9], "doc-b": [2]}
        for slug, numbers in by_doc.items():
            raw = (out / f"{slug}.json").read_bytes()
            result = parse(raw)
            self.assertEqual(numbers, [p.page_number for p in result.pages])
            self.assertTrue(all(p.markdown.strip() for p in result.pages))
            data = json.loads(raw)["data"]
            self.assertEqual({"azure_layout_v1"}, {p["source"] for p in data["pages"]})
            self.assertEqual(len(numbers), data["metadata"]["numPages"])
        doc_a = json.loads((out / "doc-a.json").read_text())["data"]["pages"]
        self.assertEqual([False, True], [p["needs_full_review"] for p in doc_a])
        self.assertEqual(
            ["delta"],
            [w["text"] for w in doc_a[1]["confidence"]["low_confidence_words"]],
        )
        flagged = doc_a[1]["confidence"]
        self.assertEqual(25.0, flagged["pct_below_0_8"])
        self.assertIn("pct_below_0_9", flagged)
        self.assertIn("mean", flagged)

    def test_export_requires_all_pages_complete_and_never_overwrites(self):
        with self.assertRaises(ValueError):
            v.export(self.root)
        self.assertFalse((self.root / "export").exists())
        self.complete_all()
        v.export(self.root)
        with self.assertRaises(FileExistsError):
            v.export(self.root)


class RealPathTests(Base):
    """main() with NO injected root: defaults must resolve to the v2 root."""

    def run_main(self, argv, transport, err=None):
        defaults = {"repo": self.repo, "sleep": self.clock.sleep, "clock": self.clock}
        resume_defaults = {"sleep": self.clock.sleep, "clock": self.clock}
        with (
            patch.object(v, "ROOT", self.root),
            patch.object(a, "open_transport", lambda: transport),
            patch.object(
                a, "load_settings", lambda provider, env_file=None: dict(SETTINGS)
            ),
            patch.dict(a.send.__kwdefaults__, defaults),
            patch.dict(a.resume.__kwdefaults__, resume_defaults),
            contextlib.redirect_stderr(err or io.StringIO()),
        ):
            return v.main(argv)

    def test_send_and_resume_use_v2_root_by_default(self):
        transport = FakeTransport(ok_replies(3))
        code = self.run_main(
            ["send", "--confirm-pages", "3", "--azure-tier", "s0"], transport
        )
        self.assertEqual(0, code)
        self.assertEqual(6, len(transport.requests))
        for index in range(3):
            self.assertTrue((self.folder(index) / "response.raw").exists())
        self.assertFalse(
            (a.ROOT / "execution" / "azure" / self.pages[0]["page_id"]).exists()
        )
        # all complete: resume reads the v2 ledger (not v1) and does no HTTP
        again = FakeTransport([])
        err = io.StringIO()
        self.assertEqual(1, self.run_main(["resume", "--azure-tier", "s0"], again, err))
        self.assertIn("No uncertain pages to resume", err.getvalue())
        self.assertEqual([], again.requests)

    def test_wrappers_default_root_is_v2(self):
        with patch.object(a, "send", return_value=0) as send_core:
            v.send(SETTINGS, confirm_pages=3)
        self.assertEqual(v.ROOT, send_core.call_args.kwargs["root"])
        with patch.object(a, "resume", return_value=0) as resume_core:
            v.resume(SETTINGS)
        self.assertEqual(v.ROOT, resume_core.call_args.kwargs["root"])


class CliTests(unittest.TestCase):
    def test_endpoint_constant_is_the_validated_azure_shape(self):
        self.assertEqual(ENDPOINT, a.validate_endpoint(ENDPOINT))


if __name__ == "__main__":
    unittest.main()

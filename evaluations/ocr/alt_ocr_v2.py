"""Azure OCR batch v2 (69 pages): sibling of ``alt_ocr.py`` reusing its core.

Bound to ``.artifacts/alt-ocr-v2/manifest.json`` through its own pinned SHA-256
and per-page fragment pins. The HTTP machinery, guards and ledger of
``alt_ocr.send``/``resume`` are reused unchanged (exclusive ledger folder per
(provider, page) with ``attempt.json`` fsynced BEFORE any HTTP, no replay,
validated endpoint, polling resume, F0 pacing of 3.5 s, 429 on polling honoring
Retry-After); only the manifest identity, the page count and the export differ.
Azure only (``execution/azure/<page_id>/``). check/status/export are offline.
"""

import argparse
import re
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
import alt_ocr
from alt_ocr import PartialResponse, Stop  # noqa: F401 - re-exported for callers
from pilot import ARTIFACTS, IMAGE, digest, log_phase, write_json
from prepare_alt_v2 import TOTAL_PAGES, plan_pages
from prepare_selective import ORIGINALS
from remaining_send import decode, safe

ROOT = ARTIFACTS / "alt-ocr-v2"
PROVIDER = "azure"
SOURCE = alt_ocr.SOURCE_LABEL[PROVIDER]
# Fixed after the real offline preparation (see README "Azure v2").
MANIFEST_SHA256 = "dcf7c7ad69976185f908349a782c52b76e4c2a2f7ded5fee04afd2f340503685"
PAGE_PINS = (  # (page_id, fragment sha256, bytes, physical page)
    (
        "02-documento-tecnico-p17",
        "e9eda1614f5e364b51c3ce5ccdeef5a640bd7fb39da159dbd088471cb872066d",
        261133,
        17,
    ),
    (
        "02-documento-tecnico-p21",
        "edf79341baca781f5d92373d3eb4b02c6288b538ebea8efece0f32a1840b2693",
        207549,
        21,
    ),
    (
        "02-documento-tecnico-p30",
        "ff49f35c9efc7da87cbe1f85b4a1fe8c953e59a183c0c9dc3a6fcfa1661078e8",
        192376,
        30,
    ),
    (
        "02-documento-tecnico-p32",
        "39d1689aaf64b18762ec83e7482e628cd93081e5e6abfa860e5926b9bffcae79",
        241493,
        32,
    ),
    (
        "02-documento-tecnico-p44",
        "68bb48e1c769de50eed20cef9d790ed2c9a1afce78d7ea001a92776871cb06d5",
        273895,
        44,
    ),
    (
        "02-documento-tecnico-p46",
        "538d4ebf4259bf4496c1e64efb89e887e2171fb673dae78b0e5fb9af6ac0d210",
        250217,
        46,
    ),
    (
        "03-manual-m-ip-36-p27",
        "7bc0e6cf82cc7306bb5998ab08d137290ac4d272c5b3e328f910afc12da5c9e5",
        165439,
        27,
    ),
    (
        "03-manual-m-ip-36-p29",
        "ad306c313a31a71fe9bcf6691bbc1a30dd3e5e82b7bf1aaef5de018e65d194b6",
        107960,
        29,
    ),
    (
        "03-manual-m-ip-36-p41",
        "0ce37cd79526dd6db7b901f13b3743283c8a1ebb45fd886561aea8bb59052193",
        66589,
        41,
    ),
    (
        "03-manual-m-ip-36-p47",
        "e5e9a082a14a87da560174ccd69939b4813a34901ee75ddc92165c293c66dc6e",
        62856,
        47,
    ),
    (
        "03-manual-m-ip-36-p52",
        "c5eedb0c102e2f858962064b17fed6155adf489d03f139520ad32c6d57bbf910",
        96256,
        52,
    ),
    (
        "03-manual-m-ip-36-p61",
        "9e952227f64a690de1232b4f0ceb80cd824666a84a6efce4f70b5070dde1b34d",
        114166,
        61,
    ),
    (
        "03-manual-m-ip-36-p62",
        "c9b0933919e7e268753d98985e8fb464690a7aaf7e3cd1794110a6bb42f7f4de",
        62884,
        62,
    ),
    (
        "03-manual-m-ip-36-p63",
        "99f6130a3f59237c63726cd204e2a1a0e38550034e09dc18ea1237a4ed47b6e3",
        64737,
        63,
    ),
    (
        "03-manual-m-ip-36-p64",
        "b6f3ccc1f89dcba97c5ba13769d9f4ad56470f4da4341a53ba8a872c7ed42653",
        64512,
        64,
    ),
    (
        "03-manual-m-ip-36-p65",
        "67bf746e91cdd7724dc6317660515b7999f823f69d5f23c634c843a0c031b42f",
        62300,
        65,
    ),
    (
        "03-manual-m-ip-36-p67",
        "4ee70e069ca593fb1685c33c880ddb7ef8c1a768dd41618d2eeba30dcc17a7c8",
        63015,
        67,
    ),
    (
        "03-manual-m-ip-36-p68",
        "1dcb45f97be0f2699fe523eb14db4d28b042b0396153b3c3f871f59bb7801f4a",
        70780,
        68,
    ),
    (
        "03-manual-m-ip-36-p74",
        "29d84ac575af085b540b5c50a0d893da9f8d7dba18ee7a496d805d4e4904921d",
        63598,
        74,
    ),
    (
        "03-manual-m-ip-36-p75",
        "74535b3020ee404b825ba0c7f55e328df606b3cf175dafadb0bff97b4d4346b8",
        61163,
        75,
    ),
    (
        "05-res-00137-p01",
        "c0dbb2a92db59a89008d00df34f128b1f65b1bbab767b41da8b6a2ace0de4e1a",
        1025219,
        1,
    ),
    (
        "05-res-00137-p02",
        "1bec33ea1e47ba829d3f0eee01e3c238be02fa6503dde57a019cb6afa3cb2e28",
        1034815,
        2,
    ),
    (
        "05-res-00137-p03",
        "d234adc31d1b4e956f7674741ddca1464da283fce9b5297f16cf4daf3d5f0ffd",
        1008074,
        3,
    ),
    (
        "05-res-00137-p04",
        "f57804a22544b644154c6d9dd9dc4c17b5a75aaeac2f2288c00aaf30936ef137",
        941441,
        4,
    ),
    (
        "05-res-00137-p05",
        "4b3c803fca0ebe7724e0bed5c11c6b19f0cb09a47bd493fdd30d1e7e72be41bb",
        994456,
        5,
    ),
    (
        "05-res-00137-p09",
        "17d238f8067dc6cf636bf4c7bf105a4c4ba054208310bc1857117f42d00f8c68",
        952582,
        9,
    ),
    (
        "05-res-00137-p11",
        "1c5cdc13f6e4d90b43b9964d104c6d2a2c1683c77d49ec6c5d80be14e85f7890",
        979196,
        11,
    ),
    (
        "05-res-00137-p13",
        "b62432961ab04571a37b52461d53f700c6341cc924a4a8e5f1fc9e3bf9222253",
        968177,
        13,
    ),
    (
        "05-res-00137-p14",
        "4dd8b351aa8ec50165f89d6fece2bcdf49359e25cef72b4b2e46b49a1ac4a5b5",
        1020113,
        14,
    ),
    (
        "05-res-00137-p17",
        "707cfccd3caf63f7a00ba4105d14c72e87751dc87b318aec9de9ace153c9d6b4",
        1123844,
        17,
    ),
    (
        "05-res-00137-p18",
        "b46ef163d665a456b907c6689eba2c833e16ce96079a8ffdfa05f1a9aca8cbb8",
        947523,
        18,
    ),
    (
        "05-res-00137-p19",
        "2a382e6b544efdb7a047ceb39315194660a7ef4ee6da263dfa027df6ca3ed490",
        1058982,
        19,
    ),
    (
        "06-convocatoria-2026-1-p01",
        "badb1f539f67eb41458d0680c58571c262a1e89d69097edd09661e39ed7420ac",
        503187,
        1,
    ),
    (
        "06-convocatoria-2026-1-p02",
        "1447fc433d29fb6aafaa2c5dd012026243be466ded61d4ad7fe6aa19b055e543",
        548183,
        2,
    ),
    (
        "06-convocatoria-2026-1-p03",
        "d688c625c2d8e3d99b3d8299dd2c477625d835b56a202609b5c8963f64496758",
        510154,
        3,
    ),
    (
        "06-convocatoria-2026-1-p04",
        "5fb4c952b5e2da1a1c607ca35cc8debe13a4f8c9b3866a341597a9f1a33d71e9",
        468445,
        4,
    ),
    (
        "06-convocatoria-2026-1-p05",
        "4fcc2a218cb089e70d2984a1585164d95f3a8faa52f4e4534f4f4af39728156e",
        516184,
        5,
    ),
    (
        "06-convocatoria-2026-1-p06",
        "bb721d841e0fbb64c99b56184a5731fc17a7224f2b1dc7a3abec4ebe0229a36f",
        436044,
        6,
    ),
    (
        "06-convocatoria-2026-1-p07",
        "ba9805fd4287b7169f55bfc8f660fa694efdc4e345f6e1f0ca0c1697e90e47ad",
        527232,
        7,
    ),
    (
        "06-convocatoria-2026-1-p08",
        "407450431c8afa6351cb9f957e7eab72e9504aff2453ef96a067117a0e9ff1ff",
        473008,
        8,
    ),
    (
        "06-convocatoria-2026-1-p09",
        "efc1cedebc2bc90c5df13ff14a385d42096ea6d159a3756b9de3faebc8c66b6e",
        491760,
        9,
    ),
    (
        "06-convocatoria-2026-1-p10",
        "ee4c386fe7acc19496ba2d1aafbf31101ef0c11d075aa336e6be4195c983ecf0",
        467254,
        10,
    ),
    (
        "06-convocatoria-2026-1-p11",
        "dae7ab95fa7275f3e06c27f962a523f268f5ebd8eeb93bf0376fbc85222a705f",
        464950,
        11,
    ),
    (
        "06-convocatoria-2026-1-p12",
        "f9078aeee70711a33f14227c742ee081ac9cba354b7f0576f0ec7baff3851b4b",
        496872,
        12,
    ),
    (
        "06-convocatoria-2026-1-p13",
        "b501569fa55fcf83ff41abbf9912e00383a03c4b21cbc29e22a7c3e1005cab5f",
        353699,
        13,
    ),
    (
        "06-convocatoria-2026-1-p14",
        "80459260c9d7b1da3e281b533a8333abd2954468e1a7bfa058984ac82b773eb0",
        306799,
        14,
    ),
    (
        "06-convocatoria-2026-1-p15",
        "e5d82409e8c5a3fd4f508e2c4fd6ccdcf04649359709898b4c40450214f6e753",
        344221,
        15,
    ),
    (
        "06-convocatoria-2026-1-p16",
        "2668e23650e071a7317b971bd17a02eeb7c6cac1799b03ba5122ec4caed56f5b",
        526802,
        16,
    ),
    (
        "06-convocatoria-2026-1-p17",
        "532604f9135c909a6ef5790a3274de6fe63ba4c5532e2860e3e2c5b1beddb54c",
        277569,
        17,
    ),
    (
        "07-politica-bienestar-p01",
        "9a57cea426b5143ca8e856a79ccfc59cb18b1ed0adf85cc751ae55e785b255f1",
        450000,
        1,
    ),
    (
        "07-politica-bienestar-p02",
        "1b50c5ed3b126fb5ed48406aa812e26921e1845f806f0a390eb2201234f86c6f",
        414866,
        2,
    ),
    (
        "07-politica-bienestar-p03",
        "edba34815b45117e6140343b0dafe33d9b853fa19a5d7acbcd7b8645cdb06d28",
        359960,
        3,
    ),
    (
        "07-politica-bienestar-p04",
        "05f877ace51595d17b6a88d0a7997dfe8e5e1f1e6a5c534c59006d9efaef5441",
        384898,
        4,
    ),
    (
        "07-politica-bienestar-p07",
        "5f6145c9674115db6c0737a08e22630d5532037fa67a3300e3260c41f551cb33",
        400184,
        7,
    ),
    (
        "07-politica-bienestar-p08",
        "d0de7e3ccc7a6607432abd80e8b5c7173a60c6f815d2c2bc3f78a9089e2b48aa",
        445969,
        8,
    ),
    (
        "07-politica-bienestar-p09",
        "ded42310fc82f63bce9c984cee907642092a8e752c9fa1e5dfba73136dc94e4c",
        398827,
        9,
    ),
    (
        "07-politica-bienestar-p10",
        "d8fa762041bf182021bf68cfd8d554f29ede9d9ce50b65470f6f281c6d359469",
        375141,
        10,
    ),
    (
        "07-politica-bienestar-p11",
        "ba697efb4dff9c3e16dcf620c84fc440fe7ba87b611b0e2bfbf986321496014b",
        390689,
        11,
    ),
    (
        "07-politica-bienestar-p12",
        "197dbd26d9a71db0ae14b1645f05b1dc8f84277b37f1ea220386d6b7558ea9b4",
        408275,
        12,
    ),
    (
        "07-politica-bienestar-p13",
        "3d5df6a88fc570e441ed33ddaef51d02c2fa8a8f2e447153d82e05d190a2453f",
        365605,
        13,
    ),
    (
        "07-politica-bienestar-p14",
        "d53a05de67225d1843640685632be565d7178e26346d21b52224a29e4f43cd58",
        321134,
        14,
    ),
    (
        "07-politica-bienestar-p15",
        "2a3776da66a16739bef100d594d8abc078acca3b710feb6685718cfbb4dd6163",
        282077,
        15,
    ),
    (
        "07-politica-bienestar-p16",
        "7b106195a758f8f77a7c206718feb74dab42a5056b4d79e43b186e838c0118bf",
        245683,
        16,
    ),
    (
        "07-politica-bienestar-p17",
        "6be1bef77053f5813c94174209c4934d4f87f9e9b9221e8f7775fe4e663f8d6a",
        338006,
        17,
    ),
    (
        "07-politica-bienestar-p18",
        "4588b56dd185ddc80286aa13ca455b8ebf4d1ef7d9687150b7229cee85a69b14",
        375817,
        18,
    ),
    (
        "07-politica-bienestar-p20",
        "d5baec96cc1010b5587ee1dfb8f2bacff4217e1645f13de9f0d2262c6645916e",
        374925,
        20,
    ),
    (
        "07-politica-bienestar-p22",
        "58f351de94f31c314b0cf4112513aa0600c56a3dfc50ae5d37316937b807c1d3",
        281592,
        22,
    ),
    (
        "07-politica-bienestar-p23",
        "f1ca0fb9976733ec06660a22a67e468dac2ea839e37a7619af21c1ef608f5df6",
        339561,
        23,
    ),
    (
        "07-politica-bienestar-p24",
        "199fa732fec051adfe95d2a3f64a2da88c54b70019968fc211a6fab55346530a",
        89371,
        24,
    ),
)
SOURCES = {slug: (path, sha) for path, (slug, sha, _) in ORIGINALS.items()}
# Review rule (docs: alt-ocr-pilot-v1-comparison): mean < 0.93 or > 10 % of
# words below 0.8 requires reviewing the whole page.
REVIEW_MEAN = 0.93
REVIEW_PCT_BELOW_08 = 10.0
WORD_LIST_BELOW = 0.9


def load_manifest(root: Path = ROOT) -> dict:
    """Return the v2 manifest only if its bytes and every page match the pins."""
    raw = safe(root / "manifest.json").read_bytes()
    if digest(raw) != MANIFEST_SHA256:
        raise ValueError("Manifest identity mismatch")
    manifest = decode(raw)
    pages = manifest["pages"]
    pins = [(p["page_id"], p["sha256"], p["bytes"], p["physical_page"]) for p in pages]
    plan = [p["page_id"] for p in plan_pages()]
    if (
        manifest["state"] != "prepared_offline_not_sent"
        or manifest.get("campaign") != "alt-ocr-v2"
        or manifest["image"] != IMAGE
        or manifest["total_pages"] != TOTAL_PAGES
        or pins != [tuple(pin) for pin in PAGE_PINS]
        or [p["page_id"] for p in pages] != plan
        or len(pages) != TOTAL_PAGES
    ):
        raise ValueError("Manifest content mismatch")
    for page in pages:
        path, sha = SOURCES[page["slug"]]
        if (
            not re.fullmatch(r"[0-9]{2}-[a-z0-9-]+-p[0-9]{2}", page["page_id"])
            or page["file"] != f"pages/{page['page_id']}.pdf"
            or page["source_path"] != path
            or page["source_sha256"] != sha
        ):
            raise ValueError("Page content mismatch")
    return manifest


def send(settings: dict, *, confirm_pages: int, **kwargs) -> int:
    """v1 ``send`` bound to the v2 manifest; confirm-pages must equal 69."""
    kwargs.setdefault("root", ROOT)
    return alt_ocr.send(
        PROVIDER,
        settings,
        confirm_pages=confirm_pages,
        loader=load_manifest,
        manifest_sha=MANIFEST_SHA256,
        total=TOTAL_PAGES,
        **kwargs,
    )


def resume(settings: dict, **kwargs) -> int:
    """v1 ``resume`` (polling only, no new POST) bound to the v2 manifest."""
    kwargs.setdefault("root", ROOT)
    return alt_ocr.resume(PROVIDER, settings, loader=load_manifest, **kwargs)


def status(root: Path = ROOT) -> int:
    """Offline per-page state of the Azure ledger; no key or network."""
    return alt_ocr.status(root, loader=load_manifest, providers=(PROVIDER,))


# --------------------------------------------------------------------- export
def span_offsets(spans: object) -> list[tuple[int, int, int]]:
    """(content offset, length, markdown offset) per span of the page."""
    if not isinstance(spans, list) or not spans:
        return []
    result, cumulative = [], 0
    for span in spans:
        result.append((span["offset"], span["length"], cumulative))
        cumulative += span["length"]
    return result


def page_metrics(page: dict) -> tuple[dict, list[dict]]:
    """Confidence metrics, review flag and low-confidence words of one page.

    Words below 0.9 are listed with text, confidence and offset in the page
    Markdown (``None`` when the word lies outside the page spans); no context.
    """
    mapping = span_offsets(page.get("spans"))
    words = [
        w
        for w in page.get("words") or []
        if isinstance(w, dict)
        and isinstance(w.get("confidence"), (int, float))
        and isinstance(w.get("content"), str)
    ]
    scores = [w["confidence"] for w in words]
    total = len(scores)
    metrics: dict = {"granularity": "word", "words_total": total}
    if total:
        metrics["mean"] = sum(scores) / total
        metrics["min"] = min(scores)
        metrics["pct_below_0_8"] = 100.0 * sum(s < 0.8 for s in scores) / total
        metrics["pct_below_0_9"] = 100.0 * sum(s < 0.9 for s in scores) / total
    flag = (
        total == 0
        or metrics["mean"] < REVIEW_MEAN
        or metrics["pct_below_0_8"] > REVIEW_PCT_BELOW_08
    )
    metrics["needs_full_review"] = flag
    listed = []
    for word in words:
        if word["confidence"] >= WORD_LIST_BELOW:
            continue
        offset = None
        span = word.get("span")
        if isinstance(span, dict) and type(span.get("offset")) is int:
            for start, length, base in mapping:
                if start <= span["offset"] < start + length:
                    offset = base + span["offset"] - start
                    break
            else:
                offset = None if mapping else span["offset"]
        listed.append(
            {
                "text": word["content"],
                "confidence": word["confidence"],
                "offset": offset,
            }
        )
    metrics["low_confidence_words"] = listed
    return metrics, listed


def export(root: Path = ROOT) -> dict:
    """Write per-document Firecrawl-Parse-shaped JSON plus ``pages.json``.

    All 69 pages must be complete. ``export/<slug>.json`` is accepted by
    ``parse_ocr_response`` (one entry per physical page, unique per document;
    ``source: azure_layout_v1``); each page carries confidence metrics and the
    ``needs_full_review`` flag. Exclusive output: an existing export is kept.
    """
    manifest = load_manifest(root)
    execution = root / alt_ocr.EXECUTION_NAME / PROVIDER
    pages = []
    for page in manifest["pages"]:
        state, result = alt_ocr.page_state(execution / page["page_id"])
        if state != "complete":
            raise ValueError("All pages must be complete before export")
        raw_file = (result or {}).get("raw_file", "response.raw")
        if raw_file != "response.raw" and not re.fullmatch(
            r"resume-[0-9]+\.raw", raw_file
        ):
            raise ValueError("Invalid raw file name")
        body = decode(safe(execution / page["page_id"] / raw_file).read_bytes())
        markdown, _ = alt_ocr.azure_page(body)
        metrics, _ = page_metrics(alt_ocr.check_azure_result(body)["pages"][0])
        pages.append(
            {
                "page_id": page["page_id"],
                "document": page["slug"],
                "pageNumber": page["physical_page"],
                "markdown": markdown,
                "source": SOURCE,
                "needs_full_review": metrics["needs_full_review"],
                "confidence": metrics,
            }
        )
    out = root / "export"
    out.mkdir()  # Exclusive: an existing export is never replaced.
    by_document: dict[str, list[dict]] = {}
    for page in pages:
        by_document.setdefault(page["document"], []).append(page)
    provenance = {
        "provider": PROVIDER,
        "source": SOURCE,
        "manifest_sha256": MANIFEST_SHA256,
        "requires_review": True,
        "review_rule": f"mean < {REVIEW_MEAN} or > {REVIEW_PCT_BELOW_08:g} % words < 0.8",
    }
    for slug, items in by_document.items():
        write_json(
            out / f"{slug}.json",
            {
                "success": True,
                "data": {
                    "pages": [
                        {
                            k: p[k]
                            for k in (
                                "pageNumber",
                                "markdown",
                                "source",
                                "needs_full_review",
                                "confidence",
                            )
                        }
                        for p in sorted(items, key=lambda p: p["pageNumber"])
                    ],
                    "metadata": {"numPages": len(items), "totalPages": len(items)},
                    "provenance": dict(provenance, document=slug),
                },
            },
        )
    flagged = [p["page_id"] for p in pages if p["needs_full_review"]]
    summary = {
        "schema": "alt-ocr-v2-export/v1",
        "provenance": provenance,
        "flagged_pages": flagged,
        "pages": pages,
    }
    write_json(out / "pages.json", summary)
    return summary


# ------------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    """check/status/export are offline; send needs --confirm-pages 69."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Offline pinned identities; no key or HTTP")
    commands.add_parser("status", help="Offline page states")
    commands.add_parser("export", help="Offline: raw -> per-document JSON + metrics")
    send_parser = commands.add_parser(
        "send", help="Azure F0/S0 HTTP: 69 pages, one per request, no retries"
    )
    send_parser.add_argument(
        "--confirm-pages",
        type=int,
        required=True,
        help=f"Must be exactly {TOTAL_PAGES} (pages authorized)",
    )
    resume_parser = commands.add_parser(
        "resume", help="Resume only the polling of uncertain pages (no POST)"
    )
    resume_parser.add_argument("--page", help="page_id (default: every uncertain one)")
    for sub in (send_parser, resume_parser):
        sub.add_argument("--env-file", type=Path)
        sub.add_argument("--azure-tier", choices=("f0", "s0"), default="f0")
        sub.add_argument(
            "--min-interval",
            type=float,
            help="Seconds between HTTP calls (default: 3.5 with f0, 0 with s0)",
        )
    args = parser.parse_args(argv)
    if args.command == "send" and args.confirm_pages != TOTAL_PAGES:
        parser.error(f"--confirm-pages must be exactly {TOTAL_PAGES}")
    started = time.monotonic()
    try:
        if args.command == "check":
            alt_ocr.preflight(ROOT, alt_ocr.REPO, load_manifest)
            log_phase("offline_check_validated", started)
            return 0
        if args.command == "status":
            return status()
        if args.command == "export":
            summary = export()
            log_phase("export_written", started)
            print(
                f"Export written; pages needing full review: {summary['flagged_pages']}"
            )
            return 0
        log_phase("credential_load_started", started)
        settings = alt_ocr.load_settings(PROVIDER, args.env_file)
        log_phase("credential_loaded", started)
        if args.command == "resume":
            return resume(
                settings,
                page_id=args.page,
                azure_tier=args.azure_tier,
                min_interval=args.min_interval,
            )
        return send(
            settings,
            confirm_pages=args.confirm_pages,
            azure_tier=args.azure_tier,
            min_interval=args.min_interval,
        )
    except KeyboardInterrupt:
        log_phase("interrupted", started, category="interrupted")
        return 130
    except Exception as error:  # noqa: BLE001 - guarda: toda falla se reporta por categoría, sin reintento
        log_phase("stopped", started, category=alt_ocr.category_of(error))
        return 1


if __name__ == "__main__":
    sys.exit(main())

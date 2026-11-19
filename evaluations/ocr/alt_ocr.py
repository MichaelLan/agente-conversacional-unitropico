"""Alternative OCR pilot v1 (Azure Document Intelligence / Mistral OCR), 10 pages.

Bound to ``.artifacts/alt-ocr-pilot-v1/manifest.json`` through its pinned
SHA-256 and per-page fragment pins (one single-page PDF per pilot page). Every
(provider, page) gets an exclusive ledger folder with ``attempt.json`` fsynced
BEFORE any HTTP; a folder without a complete result stops every later run
(no replay, no retries). Endpoints are fixed (Mistral) or validated against
``https://<name>.cognitiveservices.azure.com`` (Azure); redirects and ambient
proxies are disabled. Credentials are never logged. check/status/export are
offline.
"""

import argparse
import base64
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

sys.dont_write_bytecode = True
from pilot import (
    ARTIFACTS,
    BASE,
    IMAGE,
    NoRedirect,
    digest,
    error_category,
    log_phase,
    write_json,
)
from prepare_alt_pilot import TOTAL_PAGES
from prepare_selective import ORIGINALS
from remaining_send import decode, record, safe, sync_dir

REPO = BASE.parents[1]
ROOT = ARTIFACTS / "alt-ocr-pilot-v1"
EXECUTION_NAME = "execution"
# Fixed after the real offline preparation (see README "Piloto OCR alternativo v1").
MANIFEST_SHA256 = "0fc698abb01be2fe81df33f5cecb4a06193066770f4de7264c662b702b2e6327"
PAGE_PINS = (  # (page_id, fragment sha256, bytes, physical page)
    (
        "01-res-cbu-002-2025-p03",
        "1d8765abc642aac1839f294f646d6e30731d0642bdfe0379818b24dd358728df",
        60257,
        3,
    ),
    (
        "01-res-cbu-002-2025-p12",
        "238c409098ebb2063eb7050a219f8ef1279ef8215622b2a1168aad3a4a8f54b0",
        33986,
        12,
    ),
    (
        "01-res-cbu-002-2025-p13",
        "718a63522f024ab20b9c9eb4f4f8a7b66a74d76fec9f0f27f39a931455848aaa",
        37173,
        13,
    ),
    (
        "01-res-cbu-002-2025-p14",
        "492d62e2264cbd2664477f3351baf691cb936aece6250149ce71b863ef0e7c9e",
        32892,
        14,
    ),
    (
        "02-documento-tecnico-p16",
        "552702d890f352fce756eb3305574142befbbc55101b14949cfae2209f4bc744",
        357076,
        16,
    ),
    (
        "02-documento-tecnico-p18",
        "9b0e760f0f978c0157c48ff2bbc687c63b7a14c488f130330df4f5e72461fddd",
        216778,
        18,
    ),
    (
        "02-documento-tecnico-p20",
        "dd1ebd99c605f48968e56f0225f0c0d666e2c3ea3b87da6e334c5b62a5f7daa5",
        162962,
        20,
    ),
    (
        "02-documento-tecnico-p22",
        "49dff703a8d4895e0f42e5cde309f6025935b6b87e2b9a506460d87d96b4fe92",
        198215,
        22,
    ),
    (
        "02-documento-tecnico-p54",
        "5abb4b7506d1820542c16f3f2e12655bfb96b509a4270952de8cb004d57237a0",
        556142,
        54,
    ),
    (
        "03-manual-m-ip-36-p38",
        "fcb1a1e6838dce0ab40ad578386443fdd1c758cd0e740583627373579be75be1",
        62041,
        38,
    ),
)
SOURCES = {slug: (path, sha) for path, (slug, sha, _) in ORIGINALS.items()}

# Official contracts (verified 2026-11-19, see README for URLs).
AZURE_API_VERSION = "2024-11-30"
AZURE_MODEL = "prebuilt-layout"
AZURE_ENDPOINT_RE = re.compile(
    r"https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cognitiveservices\.azure\.com/?"
)
AZURE_OPERATION_PATH = re.compile(
    rf"/documentintelligence/documentModels/{AZURE_MODEL}/analyzeResults/"
    r"([0-9A-Za-z-]{8,64})"
)
MISTRAL_ENDPOINT = "https://api.mistral.ai/v1/ocr"
MISTRAL_MODEL = "mistral-ocr-latest"
F0_MAX_FRAGMENT_BYTES = 3_000_000  # base64 body stays below the 4 MB F0 limit
POST_TIMEOUT = 120
GET_TIMEOUT = 60
POLL_DEADLINE = 300.0
POLL_MIN, POLL_MAX, POLL_DEFAULT = 2.0, 30.0, 3.0
# USD per page (docs/research/ocr-alternatives.md §3, retail prices checked 2026-11-19).
COST_PER_PAGE = {"azure-f0": 0.0, "azure-s0": 0.01, "mistral": 0.004}
SOURCE_LABEL = {"azure": "azure_layout_v1", "mistral": "mistral_ocr_v1"}
UNKNOWN = "consumo desconocido"
SECRET_VALUE = r'(?:"([A-Za-z0-9_./:-]+)"|\'([A-Za-z0-9_./:-]+)\'|([A-Za-z0-9_./:-]+))'
KEY_RE = re.compile(r"[A-Za-z0-9_-]{16,256}")
ENV_NAMES = {
    "azure": ("AZURE_DOCINT_ENDPOINT", "AZURE_DOCINT_KEY"),
    "mistral": ("MISTRAL_API_KEY",),
}


class Stop(Exception):  # noqa: N818 - controlled stop with a fixed category label
    """Stop the page with a controlled category (never response text)."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


class PartialResponse(Exception):  # noqa: N818
    """The HTTP body was truncated; keep what arrived for the ledger."""

    def __init__(self, status: int | None, partial: bytes) -> None:
        super().__init__("incomplete_response")
        self.status = status
        self.partial = partial


class Reply(NamedTuple):
    """HTTP reply reduced to what the pilot needs."""

    status: int | None
    headers: object
    raw: bytes


def category_of(error: BaseException) -> str:
    """Controlled category for any failure; never renders exception text."""
    if isinstance(error, (Stop, PartialResponse)):
        return (
            "incomplete_response"
            if isinstance(error, PartialResponse)
            else (error.category)
        )
    return error_category(error)


# ---------------------------------------------------------------- credentials
def validate_endpoint(value: str) -> str:
    """Only ``https://<name>.cognitiveservices.azure.com`` (no port/path/userinfo)."""
    if not isinstance(value, str) or not AZURE_ENDPOINT_RE.fullmatch(value):
        raise ValueError("Endpoint not allowed")
    return value.rstrip("/")


def _env_file_values(env_file: Path, names: tuple[str, ...]) -> dict[str, str]:
    """Parse only the wanted NAME=value lines; no shell, expansion or eval."""
    found: dict[str, list[str]] = {name: [] for name in names}
    with env_file.open(encoding="utf-8") as stream:
        for line in stream:
            for name in names:
                if re.match(rf"^\s*(?:export\s+)?{name}\b", line):
                    match = re.fullmatch(
                        rf"\s*(?:export\s+)?{name}\s*=\s*{SECRET_VALUE}\s*", line
                    )
                    if not match:
                        raise ValueError("Ambiguous credential syntax")
                    found[name].append(next(v for v in match.groups() if v))
    if any(len(v) != 1 for v in found.values()):
        raise ValueError("Missing or duplicated credential")
    return {name: values[0] for name, values in found.items()}


def load_settings(
    provider: str, env_file: Path | None = None, environ: dict | None = None
) -> dict:
    """Return validated credentials for the provider from env or an env-file."""
    names = ENV_NAMES[provider]
    if env_file is None:
        source = os.environ if environ is None else environ
        values = {name: source.get(name, "") for name in names}
    else:
        values = _env_file_values(env_file, names)
    key = values[names[-1]]
    if not KEY_RE.fullmatch(key):
        raise ValueError("Missing or invalid credential")
    settings = {"key": key}
    if provider == "azure":
        settings["endpoint"] = validate_endpoint(values[names[0]])
    return settings


# ------------------------------------------------------------------- manifest
def load_manifest(root: Path = ROOT) -> dict:
    """Return the manifest only if its bytes and every page match the pins."""
    raw = safe(root / "manifest.json").read_bytes()
    if digest(raw) != MANIFEST_SHA256:
        raise ValueError("Manifest identity mismatch")
    manifest = decode(raw)
    pages = manifest["pages"]
    pins = [(p["page_id"], p["sha256"], p["bytes"], p["physical_page"]) for p in pages]
    if (
        manifest["state"] != "prepared_offline_not_sent"
        or manifest["image"] != IMAGE
        or manifest["total_pages"] != TOTAL_PAGES
        or pins != [tuple(pin) for pin in PAGE_PINS]
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


def preflight(root: Path = ROOT, repo: Path = REPO) -> list[tuple[dict, bytes]]:
    """Verify manifest, fragment bytes and original identities; no key, no network."""
    manifest = load_manifest(root)
    payloads = []
    for page in manifest["pages"]:
        source = safe(repo / page["source_path"])
        if digest(source.read_bytes()) != page["source_sha256"]:
            raise ValueError("Source mismatch")
        data = safe(root / page["file"]).read_bytes()
        if (
            digest(data) != page["sha256"]
            or len(data) != page["bytes"]
            or not data.startswith(b"%PDF-")
        ):
            raise ValueError("Fragment mismatch")
        payloads.append((page, data))
    return payloads


# --------------------------------------------------------------------- ledger
def page_state(folder: Path) -> tuple[str, dict | None]:
    """not_started | complete | uncertain (folder without a complete result)."""
    if not folder.exists():
        return "not_started", None
    result_path = folder / "result.json"
    result = decode(safe(result_path).read_bytes()) if result_path.exists() else None
    if result is not None and result.get("state") == "complete":
        return "complete", result
    return "uncertain", result


def save_raw(path: Path, raw: bytes) -> None:
    """Exclusive, fsynced raw body (a second write would mean a replay)."""
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync_dir(path.parent)


def open_transport() -> Callable:
    """No ambient proxies, no redirects, no retry mechanism."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect()
    ).open


def fetch(transport: Callable, request: urllib.request.Request, timeout: int) -> Reply:
    """One HTTP exchange; HTTP errors are replies, truncation is PartialResponse."""
    try:
        response = transport(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        status = response.status if type(response.status) is int else None
        try:
            raw = response.read()
        except http.client.IncompleteRead as error:
            raise PartialResponse(status, error.partial) from error
        length = response.headers.get("Content-Length")
        if length is not None and int(length) != len(raw):
            raise PartialResponse(status, raw)
        return Reply(status, response.headers, raw)


# ------------------------------------------------------------- Azure requests
def azure_post_request(endpoint: str, key: str, data: bytes) -> urllib.request.Request:
    """analyze: JSON ``base64Source``; markdown output; code-point offsets (Python)."""
    query = urllib.parse.urlencode(
        {
            "api-version": AZURE_API_VERSION,
            "outputContentFormat": "markdown",
            "stringIndexType": "unicodeCodePoint",
        }
    )
    url = (
        f"{endpoint}/documentintelligence/documentModels/{AZURE_MODEL}:analyze?{query}"
    )
    body = json.dumps({"base64Source": base64.b64encode(data).decode("ascii")})
    return urllib.request.Request(
        url,
        method="POST",
        data=body.encode(),
        headers={
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/json",
        },
    )


def operation_id(location: object, endpoint: str) -> str:
    """Accept only the same host and the exact analyzeResults path; return the id.

    The key header is only ever sent to a URL rebuilt from the validated
    endpoint plus this id, never to the header value itself.
    """
    if not isinstance(location, str):
        raise Stop("bad_operation_location")
    parts = urllib.parse.urlsplit(location)
    match = AZURE_OPERATION_PATH.fullmatch(parts.path)
    if (
        parts.scheme != "https"
        or parts.netloc != urllib.parse.urlsplit(endpoint).netloc
        or parts.username
        or parts.fragment
        or urllib.parse.parse_qs(parts.query) != {"api-version": [AZURE_API_VERSION]}
        or not match
    ):
        raise Stop("bad_operation_location")
    return match.group(1)


def azure_poll_request(endpoint: str, key: str, op: str) -> urllib.request.Request:
    """GET the analyze result of an operation id."""
    url = (
        f"{endpoint}/documentintelligence/documentModels/{AZURE_MODEL}/"
        f"analyzeResults/{op}?api-version={AZURE_API_VERSION}"
    )
    return urllib.request.Request(
        url, method="GET", headers={"Ocp-Apim-Subscription-Key": key}
    )


def retry_after(headers: object, default: float) -> float:
    """Honor an integer Retry-After within sane bounds."""
    value = headers.get("Retry-After") if headers is not None else None
    try:
        seconds = float(int(value)) if value is not None else default
    except ValueError:
        seconds = default
    return min(max(seconds, POLL_MIN), POLL_MAX)


def check_azure_result(body: dict) -> dict:
    """Structural check of a succeeded analyzeResult for one single-page PDF."""
    result = body.get("analyzeResult")
    pages = result.get("pages") if isinstance(result, dict) else None
    if (
        not isinstance(pages, list)
        or len(pages) != 1
        or not isinstance(pages[0], dict)
        or pages[0].get("pageNumber") != 1
        or not isinstance(result.get("content"), str)
    ):
        raise Stop("invalid_result")
    return result


# ----------------------------------------------------------- Mistral requests
def mistral_request(key: str, data: bytes) -> urllib.request.Request:
    """OCR of one PDF as a base64 data URI; HTML tables and word confidence."""
    uri = "data:application/pdf;base64," + base64.b64encode(data).decode("ascii")
    body = {
        "model": MISTRAL_MODEL,
        "document": {"type": "document_url", "document_url": uri},
        "table_format": "html",
        "confidence_scores_granularity": "word",
        "include_image_base64": False,
    }
    return urllib.request.Request(
        MISTRAL_ENDPOINT,
        method="POST",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        },
    )


def check_mistral_result(body: dict) -> dict:
    """One page with Markdown; the page index base is not relied upon."""
    pages = body.get("pages")
    if (
        not isinstance(pages, list)
        or len(pages) != 1
        or not isinstance(pages[0], dict)
        or not isinstance(pages[0].get("markdown"), str)
    ):
        raise Stop("invalid_result")
    return pages[0]


# --------------------------------------------------------------- page sending
def new_result(page: dict, provider: str) -> dict:
    """Result record, uncertain until proven complete."""
    return {
        "page_id": page["page_id"],
        "provider": provider,
        "http_status": None,
        "category": "unknown",
        "empty_markdown": None,
        "state": "uncertain_no_replay",
    }


def run_azure(
    folder: Path,
    data: bytes,
    settings: dict,
    transport: Callable,
    started: float,
    result: dict,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    """analyze POST (202 + Operation-Location), then poll GET until a final status."""
    endpoint, key = settings["endpoint"], settings["key"]
    log_phase("http_started", started)
    try:
        reply = fetch(transport, azure_post_request(endpoint, key, data), POST_TIMEOUT)
    except PartialResponse as error:
        result["http_status"] = error.status
        save_raw(folder / "post.raw", error.partial)
        raise
    result["http_status"] = reply.status
    save_raw(folder / "post.raw", reply.raw)
    result["state"] = "response_saved_no_replay"
    log_phase("post_saved", started, status=reply.status)
    if reply.status != 202:
        raise Stop("http_error")
    op = operation_id(reply.headers.get("Operation-Location"), endpoint)
    interval = retry_after(reply.headers, POLL_DEFAULT)
    record(folder / "operation.json", {"operation_id": op, "retry_after": interval})
    result["state"] = "operation_started_no_replay"
    log_phase("operation_started", started)
    deadline = clock() + POLL_DEADLINE
    while True:
        if clock() + interval > deadline:
            raise Stop("timeout")
        sleep(interval)
        try:
            reply = fetch(transport, azure_poll_request(endpoint, key, op), GET_TIMEOUT)
        except PartialResponse as error:
            result["http_status"] = error.status
            raise
        result["http_status"] = reply.status
        log_phase("poll", started, status=reply.status)
        if reply.status != 200:
            save_raw(folder / "response.raw", reply.raw)
            raise Stop("http_error")
        try:
            body = decode(reply.raw)
        except ValueError:
            save_raw(folder / "response.raw", reply.raw)
            raise Stop("invalid_response") from None
        status = body.get("status")
        if status in ("notStarted", "running"):
            interval = retry_after(reply.headers, interval)
            continue
        save_raw(folder / "response.raw", reply.raw)
        log_phase("raw_saved", started, status=reply.status)
        if status != "succeeded":
            raise Stop("operation_failed" if status == "failed" else "unknown_status")
        analyzed = check_azure_result(body)
        result["content_format"] = analyzed.get("contentFormat")
        result["empty_markdown"] = not analyzed["content"].strip()
        return


def run_mistral(
    folder: Path,
    data: bytes,
    settings: dict,
    transport: Callable,
    started: float,
    result: dict,
) -> None:
    """One synchronous POST to the fixed endpoint."""
    log_phase("http_started", started)
    try:
        reply = fetch(transport, mistral_request(settings["key"], data), POST_TIMEOUT)
    except PartialResponse as error:
        result["http_status"] = error.status
        save_raw(folder / "response.raw", error.partial)
        raise
    result["http_status"] = reply.status
    save_raw(folder / "response.raw", reply.raw)
    result["state"] = "response_saved_no_replay"
    log_phase("raw_saved", started, status=reply.status)
    if reply.status != 200:
        raise Stop("http_error")
    try:
        body = decode(reply.raw)
    except ValueError:
        raise Stop("invalid_response") from None
    page = check_mistral_result(body)
    result["model"] = body.get("model") if isinstance(body.get("model"), str) else None
    result["empty_markdown"] = not page["markdown"].strip()


def send(
    provider: str,
    settings: dict,
    *,
    confirm_pages: int,
    transport: Callable | None = None,
    root: Path = ROOT,
    repo: Path = REPO,
    azure_tier: str = "f0",
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Process pages in order; skip complete ones; stop at the first non-complete."""
    started = time.monotonic()
    log_phase("preflight_started", started)
    try:
        if confirm_pages != TOTAL_PAGES:
            raise ValueError("Confirmation must equal the page count")
        if provider not in SOURCE_LABEL or "key" not in settings:
            raise ValueError("Invalid provider or credential")
        if provider == "azure":
            validate_endpoint(settings["endpoint"])
        payloads = preflight(root, repo)
        if provider == "azure" and azure_tier == "f0":
            if any(len(data) > F0_MAX_FRAGMENT_BYTES for _, data in payloads):
                raise ValueError("Fragment too large for F0")
        execution = safe(root / EXECUTION_NAME) / provider
        states = {
            page["page_id"]: page_state(execution / page["page_id"])[0]
            for page, _ in payloads
        }
        if any(state == "uncertain" for state in states.values()):
            raise FileExistsError("Uncertain page; no replay")
        log_phase("preflight_validated", started)
    except (Exception, KeyboardInterrupt) as error:
        log_phase("preflight_refused", started, category=category_of(error))
        print(
            "Preflight rechazado; ningún HTTP. Si hay una página incierta, no "
            "reintentar: revisar `status` y el portal del proveedor.",
            file=sys.stderr,
            flush=True,
        )
        if isinstance(error, KeyboardInterrupt):
            raise
        return 1
    if transport is None:
        transport = open_transport()
    code = 0
    try:
        execution.mkdir(parents=True, exist_ok=True)
        sync_dir(execution)
        sync_dir(execution.parent)
        for page, data in payloads:
            if states[page["page_id"]] == "complete":
                log_phase("page_skipped_complete", started)
                continue
            folder = execution / page["page_id"]
            folder.mkdir()  # Exclusive; an existing folder means a prior attempt.
            sync_dir(execution)
            record(
                folder / "attempt.json",
                {
                    "created_utc": datetime.now(UTC).isoformat(),
                    "provider": provider,
                    "endpoint": MISTRAL_ENDPOINT
                    if provider == "mistral"
                    else "azure-resource (host not recorded)",
                    "manifest_sha256": MANIFEST_SHA256,
                    "page_id": page["page_id"],
                    "physical_page": page["physical_page"],
                    "source_sha256": page["source_sha256"],
                    "fragment_sha256": digest(data),
                    "fragment_bytes": len(data),
                    "state": "reserved_may_have_been_sent",
                },
            )
            log_phase("ledger_reserved", started)
            result = new_result(page, provider)
            try:
                if provider == "azure":
                    run_azure(
                        folder, data, settings, transport, started, result, clock, sleep
                    )
                else:
                    run_mistral(folder, data, settings, transport, started, result)
                result.update(category="none", state="complete")
            except (Exception, KeyboardInterrupt) as error:
                result["category"] = category_of(error)
                if result["state"] == "response_saved_no_replay" and (
                    result["category"] == "invalid_input"
                ):
                    result["category"] = "invalid_response"
                log_phase(
                    "page_stopped",
                    started,
                    category=result["category"],
                    status=result["http_status"],
                )
                if isinstance(error, KeyboardInterrupt):
                    _save_result(folder, result, started)
                    raise
            _save_result(folder, result, started)
            if result["state"] != "complete":
                code = 1
                break
    except (Exception, KeyboardInterrupt) as error:
        code = 1
        log_phase("stopped", started, category=category_of(error))
        if isinstance(error, KeyboardInterrupt):
            raise
    finally:
        log_phase("finished", started)
        print(
            f"Consumo solo confirmable en el portal del proveedor ({UNKNOWN} si "
            "no hay respuesta). No reenviar automáticamente; ver `status`.",
            file=sys.stderr,
            flush=True,
        )
    return code


def _save_result(folder: Path, result: dict, started: float) -> None:
    try:
        record(folder / "result.json", result)
        log_phase("result_saved", started, status=result["http_status"])
    except Exception as error:  # noqa: BLE001 - guarda: el ledger ya bloquea el reenvío
        log_phase("result_save_failed", started, category=error_category(error))


# ---------------------------------------------------------------------- status
def status(root: Path = ROOT) -> int:
    """Offline per-page, per-provider state and estimated cost; no key or network."""
    manifest = load_manifest(root)
    execution = root / EXECUTION_NAME
    for provider in ("azure", "mistral"):
        attempted = complete = uncertain = 0
        for page in manifest["pages"]:
            state, result = page_state(execution / provider / page["page_id"])
            if state != "not_started":
                attempted += 1
            complete += state == "complete"
            uncertain += state == "uncertain"
            http = result.get("http_status") if result else None
            category = result.get("category") if result else None
            shown = state if state != "uncertain" else f"uncertain ({UNKNOWN})"
            print(
                f"{provider} {page['page_id']}: state={shown} http={http} "
                f"category={category}"
            )
        if provider == "azure":
            cost = (
                f"F0 {attempted * COST_PER_PAGE['azure-f0']:.2f} USD / "
                f"S0 {attempted * COST_PER_PAGE['azure-s0']:.2f} USD"
            )
        else:
            cost = f"{attempted * COST_PER_PAGE['mistral']:.3f} USD"
        print(
            f"{provider}: {complete} complete, {uncertain} uncertain, "
            f"{TOTAL_PAGES - attempted} not started; estimated cost of attempted "
            f"pages (upper bound): {cost}."
        )
        if uncertain:
            print(f"{provider}: uncertain page present: do NOT retry; notify.")
    return 0


# ---------------------------------------------------------------------- export
def azure_page(body: dict) -> tuple[str, dict]:
    """Markdown of the single page (cut by spans) plus per-word confidence."""
    result = check_azure_result(body)
    if result.get("contentFormat") != "markdown":
        raise ValueError("Result is not markdown")
    page = result["pages"][0]
    content = result["content"]
    spans = page.get("spans")
    if not isinstance(spans, list) or not spans:
        markdown = content
    else:
        pieces = []
        for span in spans:
            offset, length = span["offset"], span["length"]
            if type(offset) is not int or type(length) is not int:
                raise ValueError("Invalid span")
            if offset < 0 or length < 0 or offset + length > len(content):
                raise ValueError("Span outside content")
            pieces.append(content[offset : offset + length])
        markdown = "".join(pieces)
    words = [
        {"text": w["content"], "confidence": w["confidence"]}
        for w in page.get("words") or []
        if isinstance(w, dict) and isinstance(w.get("confidence"), (int, float))
    ]
    confidence = {"granularity": "word", "words": words}
    scores = [w["confidence"] for w in words]
    if scores:
        confidence["min"] = min(scores)
        confidence["mean"] = sum(scores) / len(scores)
    lines = [
        {"text": line["content"], "confidence": line["confidence"]}
        for line in page.get("lines") or []
        if isinstance(line, dict) and isinstance(line.get("confidence"), (int, float))
    ]
    if lines:
        confidence["lines"] = lines  # Present only if the service provides it.
    return markdown, confidence


def mistral_page(body: dict) -> tuple[str, dict]:
    """Markdown with table placeholders replaced by their HTML, plus confidence."""
    page = check_mistral_result(body)
    markdown = page["markdown"]
    tables = {}
    for table in page.get("tables") or []:
        if not isinstance(table, dict):
            continue
        ident, content = table.get("id"), table.get("content")
        if isinstance(ident, str) and isinstance(content, str):
            markdown = markdown.replace(f"[{ident}]({ident})", content)
            if isinstance(table.get("word_confidence_scores"), list):
                tables[ident] = table["word_confidence_scores"]
    scores = page.get("confidence_scores")
    confidence: dict = {"granularity": "word"}
    if isinstance(scores, dict):
        for name in ("average_page_confidence_score", "minimum_page_confidence_score"):
            if isinstance(scores.get(name), (int, float)):
                confidence[name] = scores[name]
        words = scores.get("word_confidence_scores")
        if isinstance(words, list):
            confidence["words"] = [w for w in words if isinstance(w, dict)]
    if tables:
        confidence["table_words"] = tables
    return markdown, confidence


def export(provider: str, root: Path = ROOT) -> dict:
    """Write comparable JSON from the saved raw responses (all 10 pages required).

    ``export/<provider>/<slug>.json`` is Firecrawl-Parse shaped (accepted by
    ``parse_ocr_response``: one entry per physical page, unique within the
    document); ``export/<provider>/pages.json`` lists all pages with confidence.
    """
    manifest = load_manifest(root)
    parse = azure_page if provider == "azure" else mistral_page
    execution = root / EXECUTION_NAME / provider
    pages = []
    for page in manifest["pages"]:
        state, _ = page_state(execution / page["page_id"])
        if state != "complete":
            raise ValueError("All pages must be complete before export")
        raw = safe(execution / page["page_id"] / "response.raw").read_bytes()
        markdown, confidence = parse(decode(raw))
        pages.append(
            {
                "page_id": page["page_id"],
                "document": page["slug"],
                "pageNumber": page["physical_page"],
                "markdown": markdown,
                "source": SOURCE_LABEL[provider],
                "confidence": confidence,
            }
        )
    out = root / "export" / provider
    out.mkdir(parents=True)  # Exclusive: an existing export is never replaced.
    by_document: dict[str, list[dict]] = {}
    for page in pages:
        by_document.setdefault(page["document"], []).append(page)
    provenance = {
        "provider": provider,
        "source": SOURCE_LABEL[provider],
        "manifest_sha256": MANIFEST_SHA256,
        "requires_review": True,
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
                            for k in ("pageNumber", "markdown", "source", "confidence")
                        }
                        for p in items
                    ],
                    "metadata": {"numPages": len(items), "totalPages": len(items)},
                    "provenance": dict(provenance, document=slug),
                },
            },
        )
    summary = {
        "schema": "alt-ocr-pilot-export/v1",
        "provenance": provenance,
        "pages": pages,
    }
    write_json(out / "pages.json", summary)
    return summary


# ------------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    """check/status/export are offline; send requires --confirm-pages 10."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Offline pinned identities; no key or HTTP")
    commands.add_parser("status", help="Offline page states and estimated cost")
    export_parser = commands.add_parser(
        "export", help="Offline: raw -> comparable JSON"
    )
    export_parser.add_argument(
        "--provider", choices=("azure", "mistral"), required=True
    )
    send_parser = commands.add_parser(
        "send", help="Paid/quota HTTP: up to 10 pages, one per request, no retries"
    )
    send_parser.add_argument("--provider", choices=("azure", "mistral"), required=True)
    send_parser.add_argument(
        "--confirm-pages",
        type=int,
        required=True,
        help="Must be exactly 10 (pages authorized)",
    )
    send_parser.add_argument(
        "--azure-tier",
        choices=("f0", "s0"),
        default="f0",
        help="f0 enforces the 4 MB document limit locally (default)",
    )
    send_parser.add_argument(
        "--env-file",
        type=Path,
        help="Explicit dotenv (only the provider variables, no shell expansion)",
    )
    args = parser.parse_args(argv)
    if args.command == "send" and args.confirm_pages != TOTAL_PAGES:
        parser.error(f"--confirm-pages must be exactly {TOTAL_PAGES}")
    started = time.monotonic()
    try:
        if args.command == "check":
            preflight()
            log_phase("offline_check_validated", started)
            return 0
        if args.command == "status":
            return status()
        if args.command == "export":
            export(args.provider)
            log_phase("export_written", started)
            return 0
        log_phase("credential_load_started", started)
        settings = load_settings(args.provider, args.env_file)
        log_phase("credential_loaded", started)
        return send(
            args.provider,
            settings,
            confirm_pages=args.confirm_pages,
            azure_tier=args.azure_tier,
        )
    except KeyboardInterrupt:
        log_phase("interrupted", started, category="interrupted")
        return 130
    except Exception as error:  # noqa: BLE001 - guarda: toda falla se reporta por categoría, sin reintento
        log_phase("stopped", started, category=category_of(error))
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""Bounded two-page Firecrawl evaluation; prepare is offline, send is explicit."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
ARTIFACTS = BASE / ".artifacts"
ORIGINAL = (
    BASE.parents[1]
    / "documents/documentos_bienestar/Resolución CBU No. 001 de 2026 - Convocatoria Pública 2026-1 (1).pdf"
)
IMAGE = "unitropico-pdf-audit:poppler-25.03.0-deb13u4"
ENDPOINT = "https://api.firecrawl.dev/v2/parse"
POPPLER_TIMEOUT = 60
MAPPING = {"1": 13, "2": 14}
# Approved evidence for this single pilot, never derived from the mutable manifest.
FRAGMENT_SHA256 = "836713cefe6bf36f69832e3c88ac56288e4e238e61343f300ba6fe34814ff71a"
FRAGMENT_BYTES = 660929
ORIGINAL_SHA256 = "3ebfd85b26199382c759240321622a1009b791e16dd895f4f7ea34428da2aa26"


def log_phase(
    phase: str, started: float, *, category: str = "none", status: int | None = None
) -> None:
    """Emit only caller-controlled labels, timestamps and numeric metadata."""
    stamp = datetime.now(UTC).isoformat()
    elapsed = time.monotonic() - started
    print(
        f"{stamp} phase={phase} elapsed_s={elapsed:.3f} "
        f"category={category} status={status}",
        file=sys.stderr,
        flush=True,
    )


def error_category(error: BaseException) -> str:
    """Classify failures without rendering exception text, URLs or headers."""
    if isinstance(error, KeyboardInterrupt):
        return "interrupted"
    if isinstance(error, subprocess.TimeoutExpired):
        return "poppler_timeout"
    if isinstance(error, subprocess.CalledProcessError):
        return "poppler_error"
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, urllib.error.URLError):
        return "timeout" if isinstance(error.reason, TimeoutError) else "network_error"
    if isinstance(error, FileExistsError):
        return "existing_artifact"
    if isinstance(error, (ValueError, KeyError, TypeError)):
        return "invalid_input"
    if isinstance(error, FileNotFoundError) and error.filename == "docker":
        return "docker_unavailable"
    if isinstance(error, OSError):
        return "local_io_error"
    return "unexpected_error"


def docker_args() -> list[str]:
    """Constrain existing local Poppler image without pulls or network."""
    return [
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
    ]


def page_count(pdf: Path) -> int:
    """Count actual pages with Poppler mounting just the PDF read-only."""
    result = subprocess.run(
        docker_args()
        + [
            "--mount",
            f"type=bind,src={pdf.resolve()},dst=/source.pdf,readonly",
            IMAGE,
            "pdfinfo",
            "/source.pdf",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=POPPLER_TIMEOUT,
    )
    match = re.search(r"^Pages:\s+(\d+)\s*$", result.stdout, re.MULTILINE)
    if not match:
        raise ValueError("No page count")
    return int(match.group(1))


def digest(data: bytes) -> str:
    """Return provenance SHA-256 without logging source contents."""
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: dict) -> None:
    """Create a record exclusively, never replacing prior pilot state."""
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def prepare() -> int:
    """Extract only original pages 13–14; preserve and hash the original."""
    before = digest(ORIGINAL.read_bytes())
    if page_count(ORIGINAL) != 17:
        raise ValueError("Unexpected source page count")
    ARTIFACTS.mkdir(exist_ok=True)
    for name in (
        "page-13.pdf",
        "page-14.pdf",
        "pages-13-14.pdf",
        "manifest.json",
        "attempt.json",
    ):
        if (ARTIFACTS / name).exists():
            raise ValueError("Preparation already exists; do not overwrite")
    subprocess.run(
        docker_args()
        + [
            "--mount",
            f"type=bind,src={ORIGINAL},dst=/source.pdf,readonly",
            "--mount",
            f"type=bind,src={ARTIFACTS},dst=/out",
            IMAGE,
            "sh",
            "-c",
            "pdfseparate -f 13 -l 14 /source.pdf /out/page-%d.pdf && "
            "pdfunite /out/page-13.pdf /out/page-14.pdf /out/pages-13-14.pdf",
        ],
        check=True,
        capture_output=True,
        timeout=POPPLER_TIMEOUT,
    )
    fragment = ARTIFACTS / "pages-13-14.pdf"
    count = page_count(fragment)
    after = digest(ORIGINAL.read_bytes())
    if count != 2 or before != after:
        raise ValueError("Preparation integrity failure")
    data = fragment.read_bytes()
    write_json(
        ARTIFACTS / "manifest.json",
        {
            "original": str(ORIGINAL.relative_to(BASE.parents[1])),
            "original_sha256": before,
            "original_sha256_after": after,
            "original_page_count": 17,
            "fragment_sha256": digest(data),
            "fragment_bytes": len(data),
            "page_count": count,
            "mapping": MAPPING,
            "image": IMAGE,
        },
    )
    print("Prepared: 2 pages; fragment 1 → original 13, fragment 2 → original 14.")
    return 0


def load_key(env_file: Path | None) -> str:
    """Read only FIRECRAWL_API_KEY; reject ambiguous dotenv syntax."""
    if env_file is None:
        value = os.environ.get("FIRECRAWL_API_KEY", "")
    else:
        values = []
        with env_file.open(encoding="utf-8") as stream:
            for line in stream:
                if re.match(r"^\s*(?:export\s+)?FIRECRAWL_API_KEY\b", line):
                    match = re.fullmatch(
                        r"\s*(?:export\s+)?FIRECRAWL_API_KEY\s*=\s*"
                        r'(?:"([A-Za-z0-9_-]+)"|\'([A-Za-z0-9_-]+)\'|([A-Za-z0-9_-]+))\s*',
                        line,
                    )
                    if not match:
                        raise ValueError("Ambiguous credential syntax")
                    values.append(next(v for v in match.groups() if v is not None))
        if len(values) != 1:
            raise ValueError("Missing or duplicated credential")
        value = values[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Missing or invalid credential")
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse all redirects, including those that could forward authorization."""

    def redirect_request(
        self, req: object, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        """Never construct a second request."""
        return None


def send(artifacts: Path, key: str, transport: Callable | None = None) -> int:
    """Make at most one POST after exclusive durable attempt creation."""
    started = time.monotonic()
    log_phase("preflight_started", started)
    try:
        if (artifacts / "attempt.json").exists():
            raise FileExistsError("Attempt exists")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
            raise ValueError("Invalid credential")
        log_phase("credential_validated", started)
        log_phase("pdf_validation_started", started)
        manifest = json.loads((artifacts / "manifest.json").read_text())
        pdf = artifacts / "pages-13-14.pdf"
        data = pdf.read_bytes()
        if (
            manifest["mapping"] != MAPPING
            or manifest["page_count"] != 2
            or manifest["original"] != str(ORIGINAL.relative_to(BASE.parents[1]))
            or manifest["original_sha256"] != ORIGINAL_SHA256
            or manifest["original_sha256_after"] != ORIGINAL_SHA256
            or manifest["original_page_count"] != 17
            or manifest["fragment_sha256"] != FRAGMENT_SHA256
            or digest(data) != FRAGMENT_SHA256
            or manifest["fragment_bytes"] != FRAGMENT_BYTES
            or len(data) != FRAGMENT_BYTES
            or len(data) > 50_000_000
            or not data.startswith(b"%PDF-")
            or page_count(pdf) != 2
        ):
            raise ValueError("Invalid pilot identity or provenance")
        log_phase("pdf_validated", started)
        options = {
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
        }
        boundary = "pilot-" + uuid.uuid4().hex
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="options"\r\n'
            "Content-Type: application/json\r\n\r\n"
            + json.dumps(options)
            + f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            'filename="pages-13-14.pdf"\r\nContent-Type: application/pdf\r\n\r\n'
        ).encode()
        body += data + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(
            ENDPOINT,
            data=body,
            method="POST",
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "multipart/form-data; boundary=" + boundary,
            },
        )
        if transport is None:
            # Disable ambient proxies and default redirects; no retry mechanism.
            transport = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), NoRedirect()
            ).open
        log_phase("ledger_reservation_started", started)
        write_json(
            artifacts / "attempt.json",
            {
                "created_utc": datetime.now(UTC).isoformat(),
                "endpoint": ENDPOINT,
                "mapping": MAPPING,
                "estimated_credits": 2,
                "fragment_sha256": digest(data),
                "fragment_bytes": len(data),
                "options": options,
                "state": "reserved_may_have_been_sent",
            },
        )
        log_phase("ledger_reserved", started)
    except (Exception, KeyboardInterrupt) as error:
        log_phase("preflight_refused", started, category=error_category(error))
        print("Preflight refused; no POST.", file=sys.stderr, flush=True)
        if isinstance(error, KeyboardInterrupt):
            raise
        return 1

    result = {"state": "uncertain_no_replay", "http_status": None}
    exit_code = 1
    try:
        log_phase("http_started", started)
        try:
            response = transport(request, timeout=330)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            result["http_status"] = response.status
            log_phase("http_status", started, status=response.status)
            raw = response.read()
        with (artifacts / "response.raw").open("xb") as stream:
            stream.write(raw)
        result["state"] = "response_saved_no_replay"
        log_phase("raw_saved", started, status=result["http_status"])
        # A 200 alone is not proof of successful parsing.
        parsed = json.loads(raw)
        if result["http_status"] == 200 and parsed.get("success") is True:
            exit_code = 0
    except (Exception, KeyboardInterrupt) as error:
        # Persist controlled labels only; interruption still propagates after finally.
        result["error_category"] = error_category(error)
        log_phase(
            "request_failed",
            started,
            category=result["error_category"],
            status=result["http_status"],
        )
        if isinstance(error, KeyboardInterrupt):
            raise
    finally:
        try:
            write_json(artifacts / "result.json", result)
            log_phase("result_saved", started, status=result["http_status"])
        except Exception as error:  # noqa: BLE001 - guarda: toda falla se reporta por categoría, sin reintento
            exit_code = 1
            log_phase("result_save_failed", started, category=error_category(error))
        log_phase(
            "finished",
            started,
            category=result.get("error_category", "none"),
            status=result["http_status"],
        )
        print(
            "Intento registrado; consumo desconocido. Revisar artefactos locales; "
            "no reenviar automáticamente.",
            file=sys.stderr,
            flush=True,
        )

    return exit_code


def send_second_attempt(
    artifacts: Path, key: str, transport: Callable | None = None
) -> int:
    """Use the explicitly authorized second slot without altering prior evidence."""
    started = time.monotonic()
    second = artifacts / "second-attempt"
    log_phase("second_attempt_setup_started", started)
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
            raise ValueError("Invalid credential")
        if not (artifacts / "attempt.json").is_file():
            raise ValueError("First attempt required")
        previous = json.loads((artifacts / "result.json").read_text())
        if previous != {"state": "uncertain_no_replay", "http_status": None}:
            raise ValueError("Only the historical uncertain attempt is eligible")
        # Exclusive directory creation reserves this one extra slot, even if setup fails.
        # No arbitrary output path, ledger deletion, new extraction or automatic retries.
        second.mkdir()
        for name in ("pages-13-14.pdf", "manifest.json"):
            with (second / name).open("xb") as stream:
                stream.write((artifacts / name).read_bytes())
        log_phase("second_attempt_setup_finished", started)
    except (Exception, KeyboardInterrupt) as error:
        log_phase(
            "second_attempt_setup_refused", started, category=error_category(error)
        )
        print(
            "Segundo intento no iniciado; no POST. No borrar ni reutilizar registros.",
            file=sys.stderr,
            flush=True,
        )
        if isinstance(error, KeyboardInterrupt):
            raise
        return 1
    # All fixed provenance, PDF, exclusive-ledger and transport guards still apply.
    return send(second, key, transport)


def main() -> int:
    """Expose offline preparation and a deliberately explicit one-shot send."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "prepare", help="Offline Poppler extraction of original pages 13–14"
    )
    send_parser = commands.add_parser(
        "send", help="ONE paid POST; attempt blocks every later invocation"
    )
    send_parser.add_argument(
        "--confirm-two-credits",
        action="store_true",
        required=True,
        help="Acknowledge 2 pages × 1 credit, no retries",
    )
    send_parser.add_argument(
        "--second-authorized-attempt",
        action="store_true",
        help="Explicitly approved second 2-page POST; separate fixed one-shot ledger",
    )
    send_parser.add_argument(
        "--env-file",
        type=Path,
        help="Explicit dotenv file; otherwise only FIRECRAWL_API_KEY from environment",
    )
    args = parser.parse_args()
    if args.command == "prepare":
        started = time.monotonic()
        try:
            log_phase("prepare_started", started)
            result = prepare()
            log_phase("prepare_finished", started)
            return result
        except Exception as error:  # noqa: BLE001 - guarda: toda falla se reporta por categoría, sin reintento
            log_phase("prepare_failed", started, category=error_category(error))
            return 1
    started = time.monotonic()
    try:
        log_phase("credential_load_started", started)
        key = load_key(args.env_file)
        log_phase("credential_loaded", started)
    except (Exception, KeyboardInterrupt) as error:
        log_phase("credential_load_failed", started, category=error_category(error))
        print(
            "Stopped before POST; check local prerequisites (details suppressed).",
            file=sys.stderr,
            flush=True,
        )
        if isinstance(error, KeyboardInterrupt):
            raise
        return 1
    if args.second_authorized_attempt:
        return send_second_attempt(ARTIFACTS, key)
    return send(ARTIFACTS, key)


if __name__ == "__main__":
    sys.exit(main())

"""Selective OCR v1 sender: 106 pinned pages, lot by lot, no retries or replay.

Bound to ``.artifacts/selective-ocr-v1/manifest.json`` through its pinned
SHA-256 and per-lot fragment pins. Each lot gets ``attempt.json`` (exclusive,
fsynced) BEFORE its single POST; any lot with an attempt but no complete
result stops every later run until a human decides. ``status`` is offline.
"""

import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.dont_write_bytecode = True
from pilot import (
    ARTIFACTS,
    BASE,
    ENDPOINT,
    IMAGE,
    NoRedirect,
    digest,
    error_category,
    load_key,
    log_phase,
)
from prepare_selective import MAX_LOT_BYTES, MAX_LOT_PAGES, TOTAL_PAGES, lot_options
from remaining_send import coverage, decode, record, safe, sync_dir

REPO = BASE.parents[1]
ROOT = ARTIFACTS / "selective-ocr-v1"
EXECUTION_NAME = "execution"
# Fixed after the real offline preparation (see README "OCR selectivo v1").
MANIFEST_SHA256 = "5868748c1b2ec6e5bb40959ca8e6ee0aeec41762db580426923f8843fe433161"
LOT_PINS = (  # (lot_id, fragment sha256, bytes, pages)
    (
        "01-res-cbu-002-2025",
        "ecc6bb47f87707248d19fa8ccd26e27c16c54714c86b99e5beddc1ff64ec536a",
        816479,
        16,
    ),
    (
        "02-documento-tecnico",
        "9207bc79a00908a3d8ad676517478236fb7fbab627ca85c3601739140effc4ae",
        4457243,
        17,
    ),
    (
        "03-manual-m-ip-36-1of2",
        "35e307ab592a411736d84c51420544ef5beee000ff058e639468bd3b3ae0d872",
        942768,
        12,
    ),
    (
        "04-manual-m-ip-36-2of2",
        "4fb4cda7a733779845de35fefb35fe1fe8f04e1d76a737c703297bdbd264b43b",
        927282,
        12,
    ),
    (
        "05-res-00137",
        "4fc6c946c62ce5886059ed94f665d974b6fcd7ad7e081aef76bdc81b93a6ec87",
        12052896,
        12,
    ),
    (
        "06-convocatoria-2026-1",
        "9498437d14958d079f654962c45d7e4e93f20edf249e4a9710299e1d595b7d30",
        7709394,
        17,
    ),
    (
        "07-politica-bienestar",
        "4271c2b8745856e48a5bc5ab3d9078aa41494dcdc5d0c75189fdd08ca8f534fd",
        7222973,
        20,
    ),
)
UNKNOWN = "consumo desconocido"


def load_manifest(root: Path = ROOT) -> dict:
    """Return the manifest only if its bytes and every lot match the pins."""
    raw = safe(root / "manifest.json").read_bytes()
    if digest(raw) != MANIFEST_SHA256:
        raise ValueError("Manifest identity mismatch")
    manifest = decode(raw)
    lots = manifest["lots"]
    pins = [
        (lot["lot_id"], lot["sha256"], lot["bytes"], lot["page_count"]) for lot in lots
    ]
    if (
        manifest["state"] != "prepared_offline_not_sent"
        or manifest["image"] != IMAGE
        or manifest["total_pages"] != TOTAL_PAGES
        or pins != [tuple(pin) for pin in LOT_PINS]
        or sum(lot["page_count"] for lot in lots) != TOTAL_PAGES
    ):
        raise ValueError("Manifest content mismatch")
    for lot in lots:
        n = lot["page_count"]
        if (
            not 0 < n <= MAX_LOT_PAGES
            or not re.fullmatch(r"[0-9]{2}-[a-z0-9-]+", lot["lot_id"])
            or lot["file"] != f"lots/{lot['lot_id']}.pdf"
            or lot["mapping"]
            != {str(i): p for i, p in enumerate(lot["physical_pages"], 1)}
            or len(lot["physical_pages"]) != n
            or json.dumps(lot["options"], sort_keys=True)
            != json.dumps(lot_options(n), sort_keys=True)
        ):
            raise ValueError("Lot content mismatch")
    return manifest


def preflight(root: Path = ROOT) -> list[tuple[dict, bytes]]:
    """Verify manifest, fragment bytes and original identities; no Docker or key."""
    manifest = load_manifest(root)
    payloads = []
    for lot in manifest["lots"]:
        source = safe(REPO / lot["source_path"])
        if digest(source.read_bytes()) != lot["source_sha256"]:
            raise ValueError("Source mismatch")
        data = safe(root / lot["file"]).read_bytes()
        if (
            digest(data) != lot["sha256"]
            or len(data) != lot["bytes"]
            or len(data) > MAX_LOT_BYTES
            or not data.startswith(b"%PDF-")
        ):
            raise ValueError("Fragment mismatch")
        payloads.append((lot, data))
    return payloads


def lot_state(folder: Path) -> tuple[str, dict | None]:
    """not_started | complete | uncertain (attempt or folder without complete result)."""
    if not folder.exists():
        return "not_started", None
    result_path = folder / "result.json"
    result = decode(safe(result_path).read_bytes()) if result_path.exists() else None
    if result is not None and result.get("state") == "complete":
        return "complete", result
    return "uncertain", result


def declared_credits(folder: Path, result: dict | None) -> int | None:
    """Credits only as declared by metadata.creditsUsed; otherwise unknown."""
    if result is not None and type(result.get("credits_declared")) is int:
        return result["credits_declared"]
    raw = folder / "response.raw"
    if raw.is_file():
        try:
            credits = decode(raw.read_bytes())["data"]["metadata"]["creditsUsed"]
        except Exception:  # noqa: BLE001 - un cuerpo ilegible es consumo desconocido
            return None
        return credits if type(credits) is int else None
    return None


def build_request(lot: dict, data: bytes, key: str) -> urllib.request.Request:
    """Multipart body with the pinned options and the fragment, never the original."""
    boundary = "selective-" + uuid.uuid4().hex
    name = lot["lot_id"] + ".pdf"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="options"\r\n'
        "Content-Type: application/json\r\n\r\n"
        + json.dumps(lot["options"])
        + f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="{name}"\r\nContent-Type: application/pdf\r\n\r\n'
    ).encode()
    return urllib.request.Request(
        ENDPOINT,
        method="POST",
        data=body + data + f"\r\n--{boundary}--\r\n".encode(),
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "multipart/form-data; boundary=" + boundary,
        },
    )


def post_lot(folder: Path, lot: dict, request, transport, started: float) -> dict:
    """One POST after the durable ledger; always records result.json."""
    result = {
        "lot_id": lot["lot_id"],
        "page_count": lot["page_count"],
        "http_status": None,
        "coverage": False,
        "credits_declared": None,
        "empty_markdown_pages": None,
        "category": "unknown",
        "state": "uncertain_no_replay",
    }
    try:
        log_phase("http_started", started)
        try:
            response = transport(request, timeout=330)
        except urllib.error.HTTPError as error:
            response = error
        with response, (folder / "response.raw").open("xb") as stream:
            status = response.status
            result["http_status"] = status if type(status) is int else None
            log_phase("http_status", started, status=result["http_status"])
            try:
                raw = response.read()
                stream.write(raw)
            except http.client.IncompleteRead as error:
                stream.write(error.partial)
                raise
            finally:
                stream.flush()
                os.fsync(stream.fileno())
            length = response.headers.get("Content-Length")
            if length is not None and int(length) != len(raw):
                raise ValueError("Incomplete response")
        sync_dir(folder)
        result["state"] = "response_saved_no_replay"
        log_phase("raw_saved", started, status=result["http_status"])
        value = decode(raw)
        credits = value["data"]["metadata"].get("creditsUsed")
        if type(credits) is int:
            result["credits_declared"] = credits
        if (
            result["http_status"] != 200
            or value["success"] is not True
            or (
                credits is not None
                and (type(credits) is not int or not 0 <= credits <= lot["page_count"])
            )
        ):
            raise ValueError("Unsuccessful response")
        coverage(value, lot["page_count"])
        result["empty_markdown_pages"] = sorted(
            int(lot["mapping"][str(p["pageNumber"])])
            for p in value["data"]["pages"]
            if not p["markdown"].strip()
        )
        result.update(coverage=True, category="none", state="complete")
    except (Exception, KeyboardInterrupt) as error:
        result["category"] = error_category(error)
        log_phase(
            "lot_stopped",
            started,
            category=result["category"],
            status=result["http_status"],
        )
        if isinstance(error, KeyboardInterrupt):
            _save_result(folder, result, started)
            raise
    _save_result(folder, result, started)
    return result


def _save_result(folder: Path, result: dict, started: float) -> None:
    try:
        record(folder / "result.json", result)
        log_phase("result_saved", started, status=result["http_status"])
    except Exception as error:  # noqa: BLE001 - guarda: el ledger ya bloquea el reenvío
        log_phase("result_save_failed", started, category=error_category(error))


def send(key: str, transport=None, root: Path = ROOT) -> int:
    """Process lots in order; skip complete ones; stop at the first non-complete."""
    started = time.monotonic()
    log_phase("preflight_started", started)
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
            raise ValueError("Invalid credential")
        payloads = preflight(root)
        execution = safe(root / EXECUTION_NAME)
        states = {
            lot["lot_id"]: lot_state(execution / lot["lot_id"])[0]
            for lot, _ in payloads
        }
        if any(state == "uncertain" for state in states.values()):
            raise FileExistsError("Uncertain lot; no replay")
        log_phase("preflight_validated", started)
    except (Exception, KeyboardInterrupt) as error:
        log_phase("preflight_refused", started, category=error_category(error))
        print(
            "Preflight rechazado; ningún POST. Si hay un lote incierto, no reintentar: "
            "revisar `status` y el Dashboard.",
            file=sys.stderr,
            flush=True,
        )
        if isinstance(error, KeyboardInterrupt):
            raise
        return 1
    if transport is None:
        # No ambient proxies, no redirects, no retry mechanism.
        transport = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect()
        ).open
    code = 0
    try:
        execution.mkdir(exist_ok=True)  # Per-lot folders and ledgers are exclusive.
        sync_dir(execution.parent)
        for lot, data in payloads:
            if states[lot["lot_id"]] == "complete":
                log_phase("lot_skipped_complete", started)
                continue
            folder = execution / lot["lot_id"]
            request = build_request(lot, data, key)
            folder.mkdir()  # Exclusive; an existing folder means a prior attempt.
            sync_dir(execution)
            log_phase("ledger_started", started)
            record(
                folder / "attempt.json",
                {
                    "created_utc": datetime.now(UTC).isoformat(),
                    "endpoint": ENDPOINT,
                    "manifest_sha256": MANIFEST_SHA256,
                    "lot_id": lot["lot_id"],
                    "source_path": lot["source_path"],
                    "source_sha256": lot["source_sha256"],
                    "fragment_sha256": digest(data),
                    "fragment_bytes": len(data),
                    "page_count": lot["page_count"],
                    "mapping": lot["mapping"],
                    "estimated_credits": lot["page_count"],
                    "options": lot["options"],
                    "state": "reserved_may_have_been_sent",
                },
            )
            log_phase("ledger_reserved", started)
            result = post_lot(folder, lot, request, transport, started)
            if result["state"] != "complete":
                code = 1
                break
    except (Exception, KeyboardInterrupt) as error:
        code = 1
        log_phase("stopped", started, category=error_category(error))
        if isinstance(error, KeyboardInterrupt):
            raise
    finally:
        log_phase("finished", started)
        print(
            "Créditos solo según metadata.creditsUsed; un lote sin respuesta tiene "
            f"{UNKNOWN}. No reenviar automáticamente; ver `status`.",
            file=sys.stderr,
            flush=True,
        )
    return code


def status(root: Path = ROOT) -> int:
    """Offline summary of lot states and declared credits; no key or network."""
    manifest = load_manifest(root)
    execution = root / EXECUTION_NAME
    totals = {"complete": 0, "uncertain": 0, "not_started": 0}
    pages_done = credits_known = 0
    unknown = []
    for lot in manifest["lots"]:
        folder = execution / lot["lot_id"]
        state, result = lot_state(folder)
        totals[state] += 1
        credits = declared_credits(folder, result) if state != "not_started" else None
        if state == "complete":
            pages_done += lot["page_count"]
        if credits is not None:
            credits_known += credits
        elif state != "not_started":
            unknown.append(lot["lot_id"])
        http = result.get("http_status") if result else None
        empty = result.get("empty_markdown_pages") if result else None
        shown = (
            credits
            if credits is not None
            else (UNKNOWN if state != "not_started" else "-")
        )
        print(
            f"{lot['lot_id']}: pages={lot['page_count']} state={state} http={http} "
            f"credits_declared={shown} empty_markdown_pages={empty}"
        )
    print(
        f"Lots: {totals['complete']} complete, {totals['uncertain']} uncertain, "
        f"{totals['not_started']} not started. Pages complete: {pages_done}/{TOTAL_PAGES}. "
        f"Declared credits (metadata.creditsUsed): {credits_known}."
    )
    if unknown:
        print(
            f"Lots with {UNKNOWN}: {', '.join(unknown)}. Check the Firecrawl Dashboard."
        )
    if totals["uncertain"]:
        print("Uncertain lot present: do NOT retry; notify before any further send.")
    return 0


def main() -> int:
    """check/status are offline; send requires --confirm-credits 106."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "check", help="Offline pinned identities; no key, ledger or POST"
    )
    commands.add_parser("status", help="Offline lot states and declared credits")
    send_parser = commands.add_parser(
        "send", help="Paid: up to 106 pages (1 credit/page), lot by lot, no retries"
    )
    send_parser.add_argument(
        "--confirm-credits",
        type=int,
        required=True,
        help="Must be exactly 106 (pages authorized)",
    )
    send_parser.add_argument(
        "--env-file",
        type=Path,
        help="Explicit dotenv read by pilot.load_key (only FIRECRAWL_API_KEY, no shell)",
    )
    args = parser.parse_args()
    if args.command == "send" and args.confirm_credits != TOTAL_PAGES:
        parser.error(f"--confirm-credits must be exactly {TOTAL_PAGES}")
    started = time.monotonic()
    try:
        if args.command == "check":
            preflight()
            log_phase("offline_check_validated", started)
            return 0
        if args.command == "status":
            return status()
        log_phase("credential_load_started", started)
        key = load_key(args.env_file)
        log_phase("credential_loaded", started)
        return send(key)
    except KeyboardInterrupt:
        log_phase("interrupted", started, category="interrupted")
        return 130
    except Exception as error:  # noqa: BLE001 - guarda: toda falla se reporta por categoría, sin reintento
        log_phase("stopped", started, category=error_category(error))
        return 1


if __name__ == "__main__":
    sys.exit(main())

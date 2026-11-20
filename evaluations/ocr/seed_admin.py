"""Register the corrections seed and page overrides through the admin API (stdlib).

Run AFTER deploying and uploading the PDFs, once every version is in
``review_required`` (see README, "Caché final y semillas"):

    uv run python seed_admin.py --base-url https://<api>            # dry-run
    uv run python seed_admin.py --base-url https://<api> --apply    # registers

* Token: env ``ADMIN_TOKEN`` or ``--env-file`` (``ADMIN_TOKEN=...`` line); it
  is only sent as ``Authorization: Bearer`` and never printed or logged.
* Each document's version is resolved by the SHA-256 of its PDF in
  ``GET /admin/documents`` (``source_sha256`` or ``source_r2_key ==
  raw/{sha}.pdf``); exactly one version must match.
* Idempotent: corrections already active with the same (page, find_text) are
  skipped; ``409 duplicate_correction`` counts as already registered. An
  active override with the same text is skipped; a different active override
  stops the run (never deleted automatically).
* ``--dry-run`` is the default (GET requests only); ``--apply`` is explicit.
* Overrides marked ``requires_human_verification`` need an explicit decision:
  ``--override-verified`` (after checking the text against the page PNG)
  registers them and skips the corrections they integrate
  (``only_without_override``); ``--no-overrides`` does the reverse. Without
  either, the dry-run reports ``override_decision_required`` and ``--apply``
  stops before writing anything for that document.
* Pause between requests (``--pause``, default 1 s); a 429 is retried once
  after ``Retry-After`` (max 120 s). Any other unexpected status stops the run.
* Ledger: one JSON line per write (``--ledger``), without token or page text.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

BASE = Path(__file__).resolve().parent
SEED_DIR = BASE / ".artifacts/corrections-seed-v1"
BUSY_STATES = {"uploaded", "processing", "extracted"}
WRITABLE_STATES = {"review_required", "ready", "unpublished", "failed"}
MAX_RETRY_AFTER = 120.0
USER_AGENT = "unitropico-seed-admin/1"


class Stop(Exception):
    """Controlled stop: message without token or document text."""


class Client:
    """Minimal JSON client for /admin with pacing and one 429 retry."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        pause: float = 1.0,
        opener: Callable | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """``opener``/``sleep`` are injectable for tests."""
        parsed = urllib.parse.urlsplit(base_url)
        local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
            raise Stop("base-url must be https (http only for localhost)")
        self.base = base_url.rstrip("/")
        self._token = token
        self.pause = pause
        self.opener = opener or urllib.request.urlopen
        self.sleep = sleep
        self._first = True
        self.requests = 0

    def __repr__(self) -> str:
        """Never show the token."""
        return f"Client({self.base!r})"

    def call(self, method: str, path: str, body: dict | None = None):
        """Return (status, json) ; raise Stop on transport errors."""
        for attempt in range(2):
            if not self._first:
                self.sleep(self.pause)
            self._first = False
            data = None if body is None else json.dumps(body).encode("utf-8")
            request = urllib.request.Request(self.base + path, data=data, method=method)
            request.add_header("Authorization", f"Bearer {self._token}")
            request.add_header("Accept", "application/json")
            request.add_header("User-Agent", USER_AGENT)
            if data is not None:
                request.add_header("Content-Type", "application/json")
            self.requests += 1
            try:
                with self.opener(request, timeout=30) as response:
                    status, raw = response.status, response.read()
                    headers = response.headers
            except urllib.error.HTTPError as error:
                status, raw, headers = error.code, error.read(), error.headers
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                raise Stop(
                    f"transport error on {method} {path}: {type(error).__name__}"
                ) from None
            try:
                payload = json.loads(raw) if raw else {}
            except ValueError:
                payload = {}
            if status == 429 and attempt == 0:
                try:
                    wait = float(headers.get("Retry-After", "60"))
                except TypeError, ValueError:
                    wait = 60.0
                self.sleep(min(max(wait, 0.0), MAX_RETRY_AFTER))
                self._first = True
                continue
            return status, payload
        return status, payload


def error_code(payload: object) -> str:
    """``error.code`` of an admin error envelope, or ''."""
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        return str(payload["error"].get("code", ""))
    return ""


def load_token(env_file: Path | None) -> str:
    """ADMIN_TOKEN from the environment, else from ``--env-file``."""
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    if not token and env_file is not None:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            if line.startswith("ADMIN_TOKEN="):
                token = line.split("=", 1)[1].strip().strip("'\"")
    if not token:
        raise Stop("ADMIN_TOKEN not found (env or --env-file)")
    return token


def resolve_versions(client: Client, seeds: list[dict]) -> dict[str, dict]:
    """sha256 -> version summary, from GET /admin/documents."""
    status, payload = client.call("GET", "/admin/documents")
    if status != 200 or not isinstance(payload.get("documents"), list):
        raise Stop(f"GET /admin/documents -> {status} {error_code(payload)}")
    found: dict[str, list[dict]] = {}
    for document in payload["documents"]:
        for version in document.get("versions") or []:
            sha = version.get("source_sha256")
            key = version.get("source_r2_key") or ""
            if not sha and key.startswith("raw/") and key.endswith(".pdf"):
                sha = key[4:-4]
            if sha:
                found.setdefault(sha, []).append(version)
    result = {}
    for seed in seeds:
        matches = found.get(seed["source_sha256"], [])
        if len(matches) != 1:
            raise Stop(
                f"{seed['document']}: {len(matches)} versions with its sha256 "
                "(upload the PDF through the panel first)"
            )
        result[seed["source_sha256"]] = matches[0]
    return result


class Ledger:
    """Append-only JSON lines (no token, no page text)."""

    def __init__(self, path: Path | None) -> None:
        """``None`` disables the ledger (dry-run)."""
        self.path = path

    def write(self, **record) -> None:
        """Append one fsynced record."""
        if self.path is None:
            return
        record["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def seed_document(
    client: Client,
    doc: dict,
    version: dict,
    overrides: list[dict],
    *,
    apply: bool,
    use_overrides: bool | None,
    allow_busy: bool,
    ledger: Ledger,
) -> dict:
    """Plan (and with ``apply`` register) one document's overrides and corrections."""
    vid = version["id"]
    state = version.get("status")
    if state == "published":
        raise Stop(f"{doc['document']}: version {vid} is published (unpublish first)")
    if state in BUSY_STATES and not allow_busy:
        raise Stop(
            f"{doc['document']}: version {vid} is {state}; wait for review_required"
        )
    summary = {
        "document": doc["document"],
        "version_id": vid,
        "status": state,
        "corrections_planned": 0,
        "corrections_existing": 0,
        "corrections_created": 0,
        "overrides_planned": 0,
        "overrides_existing": 0,
        "overrides_created": 0,
    }
    # Overrides first: the corrections of an overridden page match its text.
    mine = [o for o in overrides if o["source_sha256"] == doc["source_sha256"]]
    if mine and use_overrides is None:
        summary["override_decision_required"] = True
        if apply:
            raise Stop(
                f"{doc['document']}: choose --override-verified or --no-overrides"
            )
        use_overrides = True  # dry-run: show the default plan
    if use_overrides and mine:
        status, payload = client.call("GET", f"/admin/versions/{vid}/page-overrides")
        if status != 200:
            raise Stop(f"GET page-overrides {vid} -> {status} {error_code(payload)}")
        active = {
            o["page_number"]: o for o in payload.get("overrides", []) if o.get("active")
        }
        for override in mine:
            page = override["page_number"]
            current = active.get(page)
            if current is not None:
                if current.get("text", "").strip() == override["text"].strip():
                    summary["overrides_existing"] += 1
                    continue
                raise Stop(f"{doc['document']} p{page}: a different override is active")
            summary["overrides_planned"] += 1
            if not apply:
                continue
            body = {
                "page_number": page,
                "text": override["text"],
                "reason": override["reason"],
            }
            status, payload = client.call(
                "POST", f"/admin/versions/{vid}/page-overrides", body
            )
            if status == 201:
                summary["overrides_created"] += 1
                ledger.write(
                    action="override_created",
                    document=doc["document"],
                    version_id=vid,
                    page_number=page,
                    override_id=(payload.get("override") or {}).get("id"),
                )
            else:
                raise Stop(
                    f"POST page-override p{page} -> {status} {error_code(payload)}"
                )
    status, payload = client.call("GET", f"/admin/versions/{vid}/corrections")
    if status != 200:
        raise Stop(f"GET corrections {vid} -> {status} {error_code(payload)}")
    existing = {
        (c["page_number"], c["find_text"])
        for c in payload.get("corrections", [])
        if c.get("active")
    }
    wanted = [
        c
        for c in doc["corrections"]
        if not (use_overrides and c.get("only_without_override") and mine)
    ]
    todo = [c for c in wanted if (c["page_number"], c["find_text"]) not in existing]
    summary["corrections_existing"] = len(wanted) - len(todo)
    summary["corrections_planned"] = len(todo)
    if len(existing) + len(todo) > 100:
        raise Stop(f"{doc['document']}: more than 100 active corrections")
    if apply:
        for c in todo:
            body = {
                "page_number": c["page_number"],
                "find_text": c["find_text"],
                "replace_text": c["replace_text"],
                "reason": c["reason"],
            }
            status, payload = client.call(
                "POST", f"/admin/versions/{vid}/corrections", body
            )
            if status == 201:
                summary["corrections_created"] += 1
                outcome = "correction_created"
                cid = (payload.get("correction") or {}).get("id")
            elif status == 409 and error_code(payload) == "duplicate_correction":
                summary["corrections_existing"] += 1
                outcome, cid = "correction_already_registered", None
            else:
                raise Stop(
                    f"POST correction seq={c['seq']} p{c['page_number']} -> "
                    f"{status} {error_code(payload)}"
                )
            ledger.write(
                action=outcome,
                document=doc["document"],
                version_id=vid,
                seq=c["seq"],
                page_number=c["page_number"],
                correction_id=cid,
            )
    return summary


def run(
    client: Client,
    corrections: dict,
    overrides: dict,
    *,
    apply: bool,
    use_overrides: bool | None = None,
    allow_busy: bool = False,
    ledger: Ledger | None = None,
    only: set[str] | None = None,
) -> list[dict]:
    """Seed every document (or ``only`` those slugs); returns per-document summaries."""
    ledger = ledger or Ledger(None)
    docs = [
        d
        for d in corrections["documents"]
        if (only is None or d["document"] in only)
        and (
            d["corrections"]
            or any(
                o["source_sha256"] == d["source_sha256"] for o in overrides["overrides"]
            )
        )
    ]
    versions = resolve_versions(client, docs)
    return [
        seed_document(
            client,
            doc,
            versions[doc["source_sha256"]],
            overrides["overrides"],
            apply=apply,
            use_overrides=use_overrides,
            allow_busy=allow_busy,
            ledger=ledger,
        )
        for doc in docs
    ]


def main(argv: list[str] | None = None) -> int:
    """CLI; exit 1 on any controlled stop."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True)
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--seed-dir", type=Path, default=SEED_DIR)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--pause", type=float, default=1.0)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--override-verified", action="store_true")
    choice.add_argument("--no-overrides", action="store_true")
    parser.add_argument("--allow-busy", action="store_true")
    parser.add_argument("--document", action="append", help="limit to slug(s)")
    args = parser.parse_args(argv)
    try:
        token = load_token(args.env_file)
        corrections = json.loads(
            (args.seed_dir / "corrections.json").read_text("utf-8")
        )
        overrides = json.loads(
            (args.seed_dir / "page_overrides.json").read_text("utf-8")
        )
        client = Client(args.base_url, token, pause=max(args.pause, 0.0))
        ledger_path = None
        if args.apply:
            ledger_path = args.ledger or args.seed_dir / "ledger.jsonl"
        summaries = run(
            client,
            corrections,
            overrides,
            apply=args.apply,
            use_overrides=(
                True if args.override_verified else False if args.no_overrides else None
            ),
            allow_busy=args.allow_busy,
            ledger=Ledger(ledger_path),
            only=set(args.document) if args.document else None,
        )
    except Stop as stop:
        print(f"Stopped: {stop}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError) as error:
        print(f"Stopped: {type(error).__name__}", file=sys.stderr)
        return 1
    for s in summaries:
        print(json.dumps(s, ensure_ascii=False, sort_keys=True))
    if args.apply:
        print("Registered. Next: reprocess each version (no new OCR is paid).")
    else:
        print("Dry-run: nothing written. Re-run with --apply to register.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Test doubles for the admin ports, including a D1 fake over the real schema."""

import asyncio
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlencode

import pytest

from unitropico_api.domain.admin import (
    Conflict,
    IngestionLaunchError,
    IngestionOutcome,
    IngestionRequest,
    WorkflowState,
    instance_id,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION = REPO_ROOT / "infra" / "d1" / "migrations" / "0001_initial.sql"
TOKEN = "s3cr3t-admin-token-xyz"
AUTH = {"authorization": f"Bearer {TOKEN}"}


class FakeStatement:
    """Prepared statement bound to the sqlite connection of FakeD1."""

    def __init__(self, db: FakeD1, sql: str, params: tuple = ()) -> None:
        """Keep SQL and bound values."""
        self.db, self.sql, self.params = db, sql, params

    def bind(self, *values: object) -> FakeStatement:
        """Return a copy with ?1..?N values, like D1."""
        return FakeStatement(self.db, self.sql, values)

    async def all(self) -> dict:
        """Return D1's {"results": [...], "meta": {...}} shape."""
        return {"results": self.db.execute(self), "meta": {}}

    async def first(self) -> dict | None:
        """Return the first row or None."""
        rows = self.db.execute(self)
        return rows[0] if rows else None

    async def run(self) -> dict:
        """Execute and return metadata only."""
        self.db.execute(self)
        return {"success": True, "meta": {}}


class FakeD1:
    """In-memory sqlite with the real migration, mimicking the D1 binding."""

    def __init__(self) -> None:
        """Apply infra/d1/migrations/0001_initial.sql or fail clearly."""
        if not MIGRATION.is_file():
            pytest.fail(f"No se encontró la migración D1 real en {MIGRATION}")
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(MIGRATION.read_text())
        self.broken = False

    def prepare(self, sql: str) -> FakeStatement:
        """Prepare a statement."""
        return FakeStatement(self, sql)

    def execute(self, statement: FakeStatement) -> list[dict]:
        """Run one statement and convert errors to D1-like messages."""
        if self.broken:
            raise RuntimeError("network lost")
        try:
            cursor = self.conn.execute(statement.sql, statement.params)
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as error:
            raise RuntimeError(f"D1_ERROR: {error}: SQLITE_CONSTRAINT") from None

    async def batch(self, statements: list[FakeStatement]) -> list[dict]:
        """Run all statements in one transaction, like D1 batch."""
        self.conn.execute("BEGIN")
        try:
            results = [{"results": self.execute(s), "meta": {}} for s in statements]
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")
        return results

    def sql(self, query: str, *params: object) -> list[dict]:
        """Direct helper for arranging and asserting test state."""
        cursor = self.conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def version(self, version_id: str) -> dict:
        """Return the raw row of a version."""
        return self.sql("SELECT * FROM document_versions WHERE id = ?", version_id)[0]

    def apply_corrections(self, version_id: str) -> None:
        """Clear corrections_dirty as a completed ingestion chunk step would."""
        self.sql(
            "UPDATE document_versions SET corrections_dirty = 0 WHERE id = ?",
            version_id,
        )

    def events(self, version_id: str) -> list[tuple]:
        """Return (step, status, error_code, detail) of a version's events."""
        rows = self.sql(
            "SELECT step, status, error_code, detail FROM processing_events "
            "WHERE version_id = ? ORDER BY id",
            version_id,
        )
        return [tuple(row.values()) for row in rows]

    def simulate_workflow(self, version_id: str, final: str = "ready") -> None:
        """Advance a version as the ingestion Workflow would.

        Like the ingestion chunk step, a successful run applies the current
        corrections and clears ``corrections_dirty``.
        """
        self.sql(
            "UPDATE document_versions SET status = 'processing' WHERE id = ?",
            version_id,
        )
        if final == "failed":
            self.sql(
                "UPDATE document_versions SET status = 'failed', "
                "error_code = 'ocr_failed' WHERE id = ?",
                version_id,
            )
            return
        if final == "processing":
            return
        self.sql(
            "UPDATE document_versions SET status = 'extracted', page_count = 3, "
            "extraction_r2_key = 'extractions/' || id || '.json', "
            "chunker_version = 'chunker-1', corrections_dirty = 0 WHERE id = ?",
            version_id,
        )
        self.sql(
            "INSERT INTO chunks (id, version_id, ordinal, text, text_sha256, "
            "page_start, page_end, token_estimate) "
            "VALUES ('ch_' || ?1, ?1, 0, 'Texto', ?2, 1, 2, 3)",
            version_id,
            "a" * 64,
        )
        self.sql(
            "UPDATE document_versions SET status = ? WHERE id = ?", final, version_id
        )


class FakeBlobs:
    """BlobStore double keeping objects in memory."""

    def __init__(self) -> None:
        """Start empty."""
        self.objects: dict[str, bytes] = {}
        self.puts = 0

    async def put_pdf(self, key: str, data: bytes, sha256: str) -> bool:
        """Store unless the same size exists."""
        if len(self.objects.get(key, b"")) == len(data):
            return False
        self.objects[key] = data
        self.puts += 1
        return True


class FakeLauncher:
    """IngestionLauncher double with configurable failures."""

    def __init__(self) -> None:
        """Start with no instances."""
        self.calls: list[tuple[str, dict]] = []
        self.fail = False
        self.active = False
        self.active_on_relaunch = False  # race: becomes active after the status check
        self.instances: dict[str, str] = {}

    async def _start(self, kind: str, request: IngestionRequest) -> IngestionOutcome:
        """Record the call and emulate the binding."""
        self.calls.append((kind, request.as_params()))
        if self.fail:
            raise IngestionLaunchError("Error")
        name = instance_id(request.version_id)
        if kind == "relaunch" and (self.active or self.active_on_relaunch):
            raise Conflict("ingestion_in_progress", "La versión ya se está procesando.")
        outcome = "restarted" if name in self.instances else "created"
        self.instances[name] = "queued"
        return IngestionOutcome(instance_id=name, outcome=outcome, status="queued")

    async def launch(self, request: IngestionRequest) -> IngestionOutcome:
        """Emulate create."""
        return await self._start("launch", request)

    async def relaunch(self, request: IngestionRequest) -> IngestionOutcome:
        """Emulate create-or-restart."""
        return await self._start("relaunch", request)

    async def status(self, version_id: str) -> WorkflowState | None:
        """Return the emulated instance state."""
        name = instance_id(version_id)
        if name not in self.instances:
            return None
        # An instance counts as finished unless the test marks it active.
        return WorkflowState(
            instance_id=name, status="running" if self.active else "complete"
        )


def call(
    app,
    method: str,
    path: str,
    *,
    body: bytes | dict | None = None,
    headers: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
    env: object = None,
) -> tuple[int, dict[str, str], object]:
    """Drive one ASGI request in memory and decode the JSON response."""
    raw = json.dumps(body).encode() if isinstance(body, dict) else (body or b"")
    header_list = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if isinstance(body, dict):
        header_list.append((b"content-type", b"application/json"))
    messages = []

    async def receive():
        """Provide the whole body at once."""
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message):
        """Collect response messages."""
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": urlencode(query or {}).encode(),
        "root_path": "",
        "headers": header_list,
        "server": ("test", 80),
        "client": ("test", 1234),
    }
    if env is not None:
        scope["env"] = env
    asyncio.run(app(scope, receive, send))
    response_headers = {k.decode(): v.decode() for k, v in messages[0]["headers"]}
    payload = b"".join(m.get("body", b"") for m in messages[1:])
    return messages[0]["status"], response_headers, json.loads(payload)
